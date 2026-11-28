# MyApp component deployment

Deploy independently versioned vendor packages with Ansible. The companion `myapp-component-config` project stores approved component versions, modes, stages, host waves and settings for dev, test, production and DR.

## Setup

- Provision the Linux controller using the offline runner and Nexus setup below.
- Populate `inventories/{dev,test,prod,dr}/hosts.yml` and update the matching descriptors with the exact inventory aliases. Targets need Python 3.8+ at `/usr/bin/python3`, the application runtime, util-linux and service accounts. Configure SSH, known hosts and sudo for the deployment user.
- Set `MYAPP_NEXUS_BASE_URL` to the HTTPS release repository. Set `MYAPP_NEXUS_USER` and `MYAPP_NEXUS_PASSWORD` if it requires Basic authentication. Inject the `MYAPP_SECRET_*` values listed in the approved environment's config.
- Use a committed config revision. Each package is a tarball with root `manifest.yml` containing `component`, `version` and optional `required_properties`, and a `payload/` directory with the complete component and executable `bin/start`, `bin/stop` and `bin/health`. Optional `bin/version` reports the real running version.

## Offline runner and Nexus

Register a dedicated, protected GitLab **shell** runner with the tag `myapp-offline`. Provision Python 3.12 with `venv`/bundled pip, Bash, Git, OpenSSH, util-linux, coreutils, and the internal CA before enrolling the runner. Jobs create an isolated virtual environment from the local interpreter. GitLab draws the project from internal GitLab and does not need a container image.

Publish an approved Python wheelhouse as one tar or tar.gz in a **hosted raw repository** in internal Nexus. Put compatible wheels for every requirement and transitive dependency at the archive root. Include one compatible wheel per distribution and keep an immutable path for each archive. Publish vendor releases to a hosted Nexus repository as well. The provisioning team stages these inputs from approved internal media or an existing internal store. Neither Nexus repository needs a public upstream.

| Setting | Purpose |
| --- | --- |
| `MYAPP_NEXUS_AUTHORITY` | Approved internal Nexus host and optional port, identical to the URLs below. |
| `MYAPP_NEXUS_WHEELHOUSE_URL` | Direct HTTPS URL of the wheelhouse tar in Nexus. |
| `MYAPP_NEXUS_WHEELHOUSE_SHA256` | Approved lowercase SHA-256 of the complete wheelhouse archive. |
| `MYAPP_NEXUS_WHEELHOUSE_USER` and `MYAPP_NEXUS_WHEELHOUSE_PASSWORD` | Optional paired Basic credentials for the wheelhouse. If required, make them available to check jobs as well as operation jobs. |
| `MYAPP_NEXUS_BASE_URL` | Direct HTTPS base URL of the hosted vendor repository. |
| `MYAPP_NEXUS_USER` and `MYAPP_NEXUS_PASSWORD` | Optional paired Basic credentials for vendor artifacts. |
| `MYAPP_GITLAB_AUTHORITY` | Approved internal GitLab host and optional port, matching `CI_SERVER_URL`. Required for GitLab operation jobs. |

For an operator workstation or Jenkins agent, set the same applicable Nexus variables and run:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python tools/bootstrap_python.py
```

Downloads use direct HTTPS at the configured authority, with automatic proxies and redirects disabled. The bootstrap verifies the archive checksum before extraction, then installs only the supplied wheel files with package indexes, dependency fetching, and pip version checks disabled. It checks installed dependency constraints before Ansible runs. Use firewall rules and internal DNS to restrict the runner, Nexus, and target/application networks to their approved internal services. That infrastructure policy covers the runner's initial GitLab checkout and any connections made by the application or vendor hooks.

## Operations

```bash
config=../myapp-component-config
ref=$(git -C "$config" rev-parse HEAD)
python tools/deploy.py plan --environment dev --config-repo "$config" --config-ref "$ref"
python tools/deploy.py deploy --environment dev --config-repo "$config" --config-ref "$ref"
python tools/deploy.py deploy --environment dr --mode full --config-repo "$config" --config-ref "$ref"
python tools/deploy.py status --environment dev --config-repo "$config" --config-ref "$ref"
python tools/deploy.py promote --environment dev --to test --config-repo "$config" --config-ref "$ref" --output reports/test.yml
```

`plan`, `validate`, `deploy`, and `promote` default to the descriptor's mode. Status defaults to full scope. Use `--mode selective` or `--mode full` to override one invocation. The controller stages all selected payloads, then activates the descriptor's stages and host waves in sequence. A stage can run independent components concurrently. Each host wave finishes before the next one starts. A failing activation rolls back that host/component and blocks later work. Status writes `reports/status.json` with one row per selected host and component.

Promotion checks that the selected source installations are healthy and match the approved release IDs. It writes a candidate for the target environment with the same package pins and the target's existing rollout and configuration. Review and merge the candidate before deploying there. The supported route is dev → test → prod → DR.

## Roll back from GitLab

Run a pipeline from the protected default branch with `OPERATION=rollback`, the target `DEPLOY_ENV`, and `ROLLBACK_CONFIG_REF` set to the approved configuration commit that contains the versions to restore. Run the manual **rollback** job. The rollback job uses its own commit input and shares the deploy job's environment protections and lock.

`ROLLBACK_MODE` defaults to `selective`. Set `ROLLBACK_COMPONENTS` to a comma-separated list such as `payment,orders`, or leave it blank to use the rollback descriptor's selective list. For a full rollback, set `ROLLBACK_MODE=full` and leave `ROLLBACK_COMPONENTS` blank. The job uses the selected commit's rollout policy, stages and checks. Review `reports/status.json` for the outcome.

Use a reviewed revert commit based on the current descriptor if the inventory or deployment policy changed since the older release. Merge the intended package pins and configuration into the current baseline to make the restored versions the desired state for future deployments.

## CI and local verification

The GitLab pipeline runs the test suite and exposes manual deploy, status, promotion and rollback jobs on the protected default branch. Set `OPERATION`, `DEPLOY_ENV`, and `CONFIG_PROJECT_PATH`. Use `CONFIG_REF` for deploy, status and promotion. Set `DEPLOY_MODE` to override the descriptor, and `TARGET_ENV` when promoting. Allow the deployment job token to read the config project, configure GitLab file variables `SSH_PRIVATE_KEY` and `SSH_KNOWN_HOSTS`, scope secrets by environment and configure production and DR approvals. Jobs are serialized per environment inside this GitLab project.

Jenkins can run the same CLI with an approved checkout and credentials. Use one orchestrator per environment or coordinate both with an external environment lock.

```bash
python tests/test_offline.py
python tests/test_rollout.py
bash tests/integration.sh
```

Tests use local Ansible targets and generated demo tarballs. The deployment guide provided alongside the ZIPs covers the descriptor fields, scheduling, file layout and recovery in detail.
