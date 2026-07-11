# Metadata-Driven Deployment Pipeline Templating

A profile-based system for generating explicit, auditable Jenkins pipelines for
**any** application type (Java/Tomcat, Java/JBoss, .NET/IIS, Python/systemd, …)
from a small, type-only descriptor — while keeping the actual list of artifacts
(JARs / wheels / DLLs) as pure runtime data that never triggers a pipeline change.

Built for an air-gapped, SAMA-regulated environment: no SaaS, minimal
dependencies (PyYAML for the CLI generator; the Jenkins path needs only the
*Pipeline Utility Steps* plugin), and mandatory gates that app teams cannot
remove.

---

## The core idea

Designing a pipeline per application is slow. Here, an application declares only
**what type it is**, and the pipeline is *generated* from a shared profile. Three
concerns are deliberately separated:

| Concern | Lives in | Read by | Changes when… |
|---|---|---|---|
| **Application type** (+ step add/remove) | `deployment-descriptor.yaml` | the **generator** | the app's *type* or stage shape changes |
| **What to deploy** (artifacts, targets, strategy, hooks) | `deploy/<env>.yaml` | **platformDeploy at runtime** | you add/remove a JAR, bump a version, change a target |
| **How each step works** (playbooks, scripts) | `platform-governance/steps/…` | the runtime steps | you improve an implementation once, for everyone |

Because the artifact list is runtime data, **adding a JAR is a data edit** — the
Jenkinsfile is untouched, so it doesn't need regenerating or re-reviewing.

---

## The three layers

```
┌─ Layer 1: APPLICATION REPO ──────────────────────────────────────────────┐
│  deployment-descriptor.yaml   type + steps.add/steps.remove  (GEN input)  │
│  deploy/dev.yaml uat.yaml prod.yaml   deployables (RUNTIME input)         │
│  inventory/*.ini              Ansible hosts per env                       │
│  .platform/scripts/*          app-specific custom scripts                 │
│  Jenkinsfile                  GENERATED, explicit, committed after review │
└───────────────────────────────────────────────────────────────────────────┘
                    │  type  ▲ generated Jenkinsfile
                    ▼        │
┌─ Layer 2: GOVERNANCE REPO (platform-governance/) ────────────────────────┐
│  profiles/*.yaml       canonical stage sequence per type (+ locked gates) │
│  generator/            generate.py  |  vars/generateJenkinsfile.groovy    │
│  vars/*.groovy         runtime engine: platformDeploy, platformFetch, …    │
│  steps/                ansible/ bash/ powershell/ python/  (Layer 3)      │
│  schema/               JSON schemas for descriptor + manifest             │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## End-to-end flow

1. **App team writes a descriptor** — one line of intent: `type: java-tomcat-linux`,
   plus any `steps.add` / `steps.remove`.
2. **Run the generator** (either `generate.py`, or the `pipeline-generator`
   Jenkins job which calls `generateJenkinsfile`). It resolves the profile,
   applies the patches (refusing to remove `locked` stages), and **archives a
   Jenkinsfile as an artifact**. Nothing is auto-committed.
3. **Human reviews the Jenkinsfile and commits it** into the app repo — the
   review gate that suits regulated change control.
4. **The pipeline runs.** `platformCheckout` loads `deploy/<TARGET_ENV>.yaml`;
   `platformFetch` pulls each artifact's exact version from Nexus once (BODM);
   `platformDeploy` selects the deployables bound to each deploy stage and
   deploys them per their own strategy/target/hooks.

---

## What the runtime manifest controls (per deployable)

```yaml
- id: payment-api
  artifactId: sa.bank.payments:payment-api
  version: 1.8.2
  packaging: war
  deployPath: /opt/tomcat/webapps/payments   # exactly where it lands on the server
  serverGroup: app-tier                       # default target group
  targetServers: [app-01]                     # optional: deploy to ONE/subset only
  stage: deploy                               # which pipeline stage runs this
  deploymentStrategy: rolling                 # rolling | all-at-once | blue-green | canary
  deploymentOrder: 10                         # lower = earlier
  dependsOn: []                               # deployable ids that must go first
  healthCheck:                                # post-deploy gate (run in the playbook)
    enabled: true
    endpoint: /payments/health
    port: 8080
    retries: 12
    delaySeconds: 10
  config:
    environment: { LOG_LEVEL: INFO }          # per-app env vars
    files:                                    # per-app config files
      - source: .platform/config/application-uat.properties
        destination: /opt/tomcat/conf/Catalina/localhost/payments.properties
        mode: "0644"
    jenkinsSecrets:                           # Jenkins credentials only (no vault)
      - credentialId: nexus-deploy-user
        envVar: NEXUS_CREDENTIALS
  preHooks:                                   # a LIST; each hook chooses where it runs
    - playbook: platform-governance/steps/ansible/drain-tomcat.yml
      runOn: serverGroup                      # local | serverGroup | allServers | specificServer:<host>
  postHooks:
    - script: .platform/scripts/smoke.sh
      runOn: local
```

### Two deployment modes (one manifest)

The generated pipeline runs with `DEPLOY_MODE = targeted | full`:

* **targeted** — deploys `targeted.deploy` (optionally filtered to specific ids
  via the `SELECTOR` param) and removes anything in `targeted.undeploy` by its
  `deployPath`. The everyday, surgical path.
* **full** — deploys `full.deployables`, the complete source-of-truth definition
  of the environment. Disaster recovery: rebuild it from scratch.

Both live in the same `deploy/<env>.yaml`, so the file always doubles as the
record of what should be running in that environment.

### Deployment playbooks (`steps/`)

Production-grade, and all consume the same JSON vars contract (the deployable is
serialized and passed as `-e @deploy-vars-<id>.json`):

* **`ansible/deploy-java-tomcat.yml`** — explicit deploy (autoDeploy off),
  per-host backup before swap, `block/rescue` that restores the backup and stops
  the rollout on a failed health check. Canary falls out of `serial: [1, "100%"]`
  (one host must pass health before the rest). Per-app env via an EnvironmentFile,
  config files copied to their destinations.
* **`ansible/deploy-java-jboss.yml`** — JBoss/WildFly CLI deploy/undeploy with
  `--force`, system properties applied via CLI, health check, rescue undeploys the
  failed artifact.
* **`ansible/deploy-java-weblogic.yml`** — Oracle WebLogic via `weblogic.Deployer`
  (`-redeploy` for Production Redeployment), targeting a Managed Server or Cluster
  through the Admin Server. Per-host source backup, `block/rescue` restores and
  re-redeploys the previous source, health check. Admin creds come from
  `config.jenkinsSecrets` as env vars; WebLogic settings live under the
  deployable's `weblogic:` block.
* **`powershell/deploy-iis.ps1`** — IIS over WinRM: pool offline, backup, robocopy
  mirror, app-pool environment variables + config, pool online, health check with
  backup restore on failure. Rolling honoured with a gap between hosts.
* **`ansible/deploy-dotnet-iis.yml`** — the same IIS flow for teams that prefer
  Ansible `win_*` modules end-to-end.
* **`ansible/drain-tomcat.yml` / `undeploy-tomcat.yml`** — graceful drain and
  path-based undeploy helpers.

* **stage** binds a deployable to a pipeline stage. Add a custom deploy stage via
  the descriptor (`run: deploy`) and point deployables at it.
* **strategy** and **target** are per-deployable, so one manifest can roll the
  API one host at a time while cycling workers all at once.
* **hooks** are just lists of scripts/playbooks with a `runOn` location — there
  is no special "hook" concept baked into the pipeline.

---

## Governance (SAMA-friendly)

* Profile stages marked `locked: true` (validate, approval, deploy) **cannot be
  removed** by a descriptor. App teams can add steps and remove non-critical
  ones, but cannot patch out mandatory gates.
* The Jenkinsfile is **explicit** (every stage visible) and **committed after
  human review** — no hidden dynamic pipeline, full audit trail.
* Approval stages record the approver into the build description.

---

## Try it locally

```bash
python3 platform-governance/generator/generate.py \
  --descriptor examples/sample-java-app/deployment-descriptor.yaml \
  --governance platform-governance \
  --out /tmp/Jenkinsfile

python3 platform-governance/steps/python/validate-manifest.py \
  examples/sample-java-app/deploy/uat.yaml
```

The pre-generated results live at `examples/sample-java-app/Jenkinsfile` and
`examples/sample-dotnet-app/Jenkinsfile` (same generator, Ansible vs PowerShell
executor).

---

## Repository layout

```
platform-governance/
  profiles/            java-tomcat-linux, java-jboss-linux,
                       java-weblogic-linux, dotnet-iis-windows, python-systemd-linux
  generator/
    generate.py        CLI generator (PyYAML only)
    templates/Jenkinsfile.tmpl            deploy pipeline template
    templates/Jenkinsfile.rollback.tmpl   one-click rollback job template
    vars/generateJenkinsfile.groovy   Jenkins-native generator (archives artifact)
    Jenkinsfile.generator             the job you run to produce a Jenkinsfile
  vars/                runtime shared-library steps (platform*.groovy)
  steps/
    ansible/  bash/  powershell/  python/
  schema/              descriptor + manifest JSON schemas
ROLLBACK.md            three-tier rollback design (in-play / one-click / recovery)
examples/
  sample-java-app/     descriptor, deploy/*.yaml, inventory, scripts, Jenkinsfile
  sample-dotnet-app/   same model, PowerShell executor
```

## Notes / next steps

* Wire `platformFetch`/`platformDeploy` `NEXUS_BASE` and real health endpoints.
* blue-green / canary are branched in the playbooks via the `deploy_strategy`
  extra-var — the scaffolds show the branch points; fill in your promotion logic.
* **Rollback** is built in — see `ROLLBACK.md`. The deploy step writes a per-
  deployable ledger under `.deploy-state/` (current + append-only history); the
  generated `Jenkinsfile.rollback` job restores a previous good version one-click,
  the deploy pipeline auto-rolls-back a failed component, and
  `steps/python/recovery-cli.py` gives status / history / DR recovery manifests.
* The generator and runtime steps are intentionally small and readable — adapt
  the `steps/` implementations to your Tomcat/JBoss/WebLogic/IIS specifics.
