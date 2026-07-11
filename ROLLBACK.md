# Rollback Strategy

Rollback is treated as a first-class capability, not an afterthought. A rollback
is simply a **deploy of an older, known-good version**: it runs through the exact
same health-gated path (`platformApply`) as a forward deploy, so a rolled-back
release is verified like any other.

There are three tiers, each for a different failure moment.

---

## The deployment ledger (foundation)

Every successful deploy records a per-deployable entry so there is always a
*previous* version to go back to. The old design overwrote a single
`last-good` file with the version just deployed — leaving nothing to roll back
to. That is fixed: `platformState` maintains, per environment + deployable id:

```
.deploy-state/<env>-<id>.current        JSON  - the version currently live
.deploy-state/<env>-<id>.history.jsonl  JSONL - append-only, one line per event
.deploy-state/<env>-<id>.last-good      text  - last successful version (compat)
```

A history line:

```json
{"ts":"2026-07-10T14:32:00Z","id":"payment-api","version":"1.8.2",
 "fromVersion":"1.8.1","result":"deployed","build":"payments-core-deploy#42","by":"alfadil"}
```

**Persistence** (the workspace can be wiped between builds) is controlled by
`stateTracking.mode` in the manifest:

| mode | behaviour |
|------|-----------|
| `archive` (default) | ledger archived as fingerprinted build artifacts every run |
| `git` | ledger committed to a `deployment-state` branch — durable, reviewable audit trail |
| `workspace` | kept in the workspace only (dev/testing) |

```yaml
stateTracking:
  mode: git
  branch: deployment-state
  credentialsId: gitlab-deploy-token
```

---

## Tier 1 — Automatic in-play rollback (during a deploy)

Owned by the deployment **playbook**. Before swapping the artifact, every host
backs up its current binary. The deploy runs inside a `block/rescue`: if the
post-deploy **health check fails**, that same host **restores its backup** and
the play **fails**, so a rolling or canary batch **stops before the bad version
spreads**.

* Zero human intervention; fastest possible containment.
* Canary is intrinsic: `serial: [1, "100%"]` — one host must pass health before
  the rest proceed.
* Implemented in `deploy-java-tomcat.yml`, `deploy-java-jboss.yml`,
  `deploy-java-weblogic.yml`, `deploy-iis.ps1` / `deploy-dotnet-iis.yml`.

Limitation: Tier 1 only restores the hosts in the **failing batch**. Hosts that
already succeeded earlier in a rolling deploy are handled by Tier 2.

---

## Tier 2a — Automatic release rollback (on pipeline failure)

The generated pipeline's `post { failure { … } }` calls `platformRollback` with
`scope: 'failed'`, restoring the **component(s) that failed this build** to their
**previous good version** across *all* their hosts — cleaning up a partially
rolled-out component. This is emitted only when the profile sets
`rollback.onFailure: true`.

```groovy
post {
  failure {
    platformRollback(env: params.TARGET_ENV, scope: 'failed',
                     executor: 'ansible',
                     playbook: 'steps/ansible/deploy-java-tomcat.yml',
                     reason: 'auto-rollback on pipeline failure')
  }
}
```

## Tier 2b — One-click manual rollback (`Jenkinsfile.rollback`)

The deliberate, audited "1.8.2 broke prod — go back to 1.8.1" button. A separate
job (generated per app with `generate.py --rollback`) with guard rails:

* `CONFIRM` must equal `ROLLBACK`, and `ROLLBACK_REASON` is mandatory (audit).
* Restores the ledger into a fresh workspace (`platformState.restore`).
* Prints the plan (`rollback.sh`) *before* acting.
* Requires an approval `input` for `uat`/`prod`.
* Rolls the selected ids to their previous good version — or to an explicit
  `TO_VERSION` — then records a `rolled-back` ledger event.

```
Parameters:
  TARGET_ENV      prod | uat | dev
  SELECTOR        comma-separated deployable ids, or 'all'
  TO_VERSION      blank = previous good; or an exact version
  ROLLBACK_REASON e.g. CHG-1234   (required)
  CONFIRM         must be 'ROLLBACK'
```

The target version comes from `platformState.previousGood()`: the most recent
successful version that differs from what is currently live. If none is on
record, that deployable is **skipped with a clear message** — never a silent
no-op.

---

## Tier 3 — Audit & disaster recovery (`recovery-cli.py`)

A stdlib-only, air-gapped operator view of the ledger.

```bash
# What is live right now
python3 steps/python/recovery-cli.py status prod

# Full timeline for a deployable
python3 steps/python/recovery-cli.py history prod payment-api

# Preview: current -> previous good for every live deployable
python3 steps/python/recovery-cli.py plan prod

# Emit a 'full'-mode manifest pinned to the CURRENT live versions, to rebuild a
# wiped environment back to its last known-good state
python3 steps/python/recovery-cli.py recover prod \
  --manifest deploy/prod.yaml > deploy/prod.recovery.yaml
```

Running that recovery manifest with `DEPLOY_MODE=full` rebuilds the whole
environment to the exact versions that were live.

---

## When each tier is used

| Tier | Trigger | Speed | Scope |
|------|---------|-------|-------|
| 1 — in-play | health check fails mid-deploy | instant | the failing host/batch |
| 2a — auto release | the deploy pipeline fails | ~minutes | the failed component, all its hosts |
| 2b — one-click | operator runs the rollback job | ~minutes | chosen deployables / whole env |
| 3 — recovery | environment wiped / audit needed | minutes | whole environment from ledger |

---

## Honest caveats

* **Artifact availability.** Rolling back further than the on-host backup relies
  on the previous version still existing in Nexus. Keep release retention long
  enough to cover your rollback window.
* **Config vs binary.** The ledger versions the *artifact*. Application config
  lives in the manifest; rolling an artifact back re-applies it with the
  *current* manifest config. If a bad change was purely config, revert the
  manifest (git) rather than the artifact.
* **WebLogic clusters.** Deploying to a Cluster means one admin-capable host runs
  `weblogic.Deployer` and WebLogic pushes to members — target a single host and
  let WebLogic fan out, rather than a rolling `serial` across members.
* **Ledger discipline.** One-click and recovery depend on the ledger being
  persisted (`stateTracking.mode: git` recommended for prod). In `archive` mode,
  set the rollback job's `DEPLOY_JOB` so it can copy the ledger from the deploy
  job's last successful build.
