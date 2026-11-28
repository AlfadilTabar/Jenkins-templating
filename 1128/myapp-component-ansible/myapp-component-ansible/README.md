# MyApp component deployment

Deploy independently versioned vendor packages with Ansible. The companion `myapp-component-config` project stores approved component versions, modes, stages, host waves and settings for dev, test, production and DR.

## Setup

- Use Python 3.12, Git and OpenSSH on the Linux controller. Install the project requirements with `python -m pip install -r requirements.txt`.
- Populate `inventories/{dev,test,prod,dr}/hosts.yml` and update the matching descriptors with the exact inventory aliases. Targets need Python 3.8+ at `/usr/bin/python3`, the application runtime, util-linux and service accounts. Configure SSH, known hosts and sudo for the deployment user.
- Set `MYAPP_NEXUS_BASE_URL` to the HTTPS release repository. Set `MYAPP_NEXUS_USER` and `MYAPP_NEXUS_PASSWORD` if it requires Basic authentication. Inject the `MYAPP_SECRET_*` values listed in the approved environment's config.
- Use a committed config revision. Each package is a tarball with root `manifest.yml` containing `component`, `version` and optional `required_properties`, and a `payload/` directory with the complete component and executable `bin/start`, `bin/stop` and `bin/health`. Optional `bin/version` reports the real running version.

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
python tests/test_rollout.py
bash tests/integration.sh
```

Tests use local Ansible targets and generated demo tarballs. The deployment guide provided alongside the ZIPs covers the descriptor fields, scheduling, file layout and recovery in detail.
