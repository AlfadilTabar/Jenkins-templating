# MyApp deployment

This repository runs Ansible. The companion `myapp-config` repository owns the four environment baselines and their configuration. Keep them as separate GitLab projects. A baseline records exactly which bundle and component version to install for every component. A full vendor bundle can be used to deploy one component, several components, or the entire environment.

## Set up

1. Import both archives into GitLab as separate repositories. Replace the example hostnames under `inventories/`, the component groups and service accounts in the baselines, and the example configuration and artifact coordinates. Initialize a full known-good baseline for each environment.
2. Prepare Linux application hosts with Python 3.8 or later at `/usr/bin/python3`, the Java/runtime dependencies, `runuser` from util-linux, and the unprivileged component accounts. The Ansible SSH account needs sudo on the targets with the privileges required by the unarchive and lifecycle helper. The managed application root defaults to `/opt/myapp`. Plan a one-time migration if services already run from legacy directories.
3. Run the controller on Linux with Python 3.12, Git and OpenSSH. Install the dependencies in a virtual environment. CI jobs use the same requirements file.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

4. Set `MYAPP_NEXUS_BASE_URL` to the HTTPS Nexus repository URL, for example `https://nexus.example.com/repository/vendor-releases`. Use `MYAPP_NEXUS_USER` and `MYAPP_NEXUS_PASSWORD` if authentication is required. Use a direct HTTPS artifact endpoint and install your internal CA on the runner if needed. Keep Nexus redeploy disabled and retain every bundle referenced by a baseline or rollback point.
5. Provide the secret environment variables referenced in the configuration repository through your CI secret store. Scope them to each environment so the same variable name can resolve to a different value in dev, test, prod and DR.

## Vendor bundle contract

The vendor delivers a single `.tar` or `.tar.gz` with `manifest.yml` at the archive root and a complete directory for each component. Archive member names are relative to the archive root, such as `manifest.yml` and `components/orders/orders.jar`. Package regular files and directories. The extractor rejects links, special files, and paths outside the extraction root.

```yaml
bundle_version: "2026.11.23.1"
components:
  orders:
    version: "3.8.2"
    path: components/orders
    required_properties:
      config/application.properties:
        - db.url
        - db.password
  billing:
    version: "2.13.4"
    path: components/billing
    required_properties:
      config/application.properties:
        - db.url
        - db.password
```

The vendor may include placeholder properties and should document how to configure each key, which versions can run together, and which updates need migrations. The playbook checks the vendor manifest version and every selected component version against the baseline. Agree which combinations the vendor supports before approving a baseline that mixes component versions.

Each component provides executable `bin/start`, `bin/stop` and `bin/health` with shebangs. The hooks run from the release directory as the component's service user. Start should launch the service and return. Stop should terminate that instance and return successfully when it is already down. Health should exit zero only when the intended instance is ready. An optional `bin/version` should print the actual running build version to stdout. With that hook, activation and status also check the running version against the release receipt.

The hooks receive `MYAPP_COMPONENT_DIR`, `MYAPP_SHARED_DIR`, and `MYAPP_COMPONENT`. Keep logs, PID files, and other writable state in the shared directory or at approved external paths. Configure the Java launcher to read the staged property files. If different components need isolation, assign them separate service accounts and primary groups.

## Deploy, report, and rebuild

Commit changes in the configuration repository. Use an approved commit for an operation. Example commands run from this repository, with the config repo cloned at `../myapp-config`.

```bash
ref=$(git -C ../myapp-config rev-parse HEAD)
python tools/deploy.py validate --config-repo ../myapp-config --config-ref "$ref" --environment dev
python tools/deploy.py deploy --config-repo ../myapp-config --config-ref "$ref" --environment dev --components orders
python tools/deploy.py status --config-repo ../myapp-config --config-ref "$ref" --environment dev
python tools/deploy.py deploy --config-repo ../myapp-config --config-ref "$ref" --environment dr --components all
```

Use `--components orders,billing` for a subset. `all` uses the entire environment baseline, which can point to several Nexus bundles. On clean hosts the same command installs every component in `deployment_order`. Provision the hosts and restore external databases, persistent data, and secrets separately before rebuilding. For a recovery rehearsal, run the full deploy against newly provisioned DR hosts.

Ansible stages every selected component on every target before activating any component. It then activates components in dependency order and rolls through hosts in batches of `serial` for each component. Activation stops the old service, atomically updates `current`, then starts the new service and checks health. Failure rolls the affected host and component back and stops subsequent deployment work. Previously successful activations can still be active. A rerun recovers an interrupted activation before trying the requested release. To roll back a wider deployment, select the affected components and redeploy the previous approved baseline commit, with the corresponding secret versions available to the runner. Coordinate traffic draining and database migration separately when the application requires them.

The command writes `reports/status.json` after a deployment or a status check. It shows each host's active component version, bundle path and digest, config commit, health, and whether the active release matches the requested baseline. It marks hosts with no result as `unknown`. For applications without `bin/version`, the version comes from the active directory's receipt. If a report contains drift or failed health checks, the command exits nonzero. A no-op deploy keeps the receipt from the original activation when the artifact and configuration are unchanged.

## Promote an artifact

The supported route is **dev → test → prod → dr**. For example, after approving and deploying a new `orders` version in dev:

```bash
python tools/deploy.py promote --config-repo ../myapp-config --config-ref "$ref" \
  --environment dev --to test --components orders --output reports/test-deployment.yml
```

1. The promotion command queries all source hosts for the selected components. It writes a candidate only when every selected component is healthy and matches the source baseline.
2. Review the candidate. Apply the selected artifact changes to `envs/test/deployment.yml` in a GitLab merge request. The generated file is based on the requested config commit. If the target baseline advanced in the meantime, apply just the selected artifact changes to the latest target file and review the resulting diff. Target host groups, rollout settings, configuration paths and other components stay as defined in the target baseline.
3. After merging, use the new configuration commit to deploy the **same component selection** in test. Repeat from test to prod and then from prod to DR. When a component is introduced, add its inventory group and environment configuration before promoting it.

Promotion creates an inspectable candidate and source status report. The merge request is the approval and audit record.

## GitLab CI

The included pipeline runs the integration test on repository changes. Run a pipeline manually from the protected default branch for real operations. Set `OPERATION`, `DEPLOY_ENV`, `COMPONENTS`, `CONFIG_PROJECT_PATH`, and the full `CONFIG_REF` from the config project's default branch. For a promotion candidate, set `TARGET_ENV` and run **prepare-promotion**. Choose **deploy** or **status** for the other operations. Each operation publishes its reports as job artifacts. GitLab serializes operations by source/deploy environment using a resource group.

In the config project, allow the deployment project's CI job token to read the repository. Configure `SSH_PRIVATE_KEY` and `SSH_KNOWN_HOSTS` as GitLab file variables. Use protected runners and restrict who can run operations and supply pipeline variables. Scope SSH, Nexus and application secrets by environment and configure your GitLab environment approvals and access for prod and DR.

If Jenkins already owns deployment approvals, use the same commands and archive `reports/` in Jenkins. Run on a trusted agent with a verified config checkout, an SSH credential and known-hosts file, and credentials bound to the documented environment variables. Apply an environment lock across jobs in Jenkins, or use one serialized job for all environments. Use one orchestrator per environment or a shared external lock when both tools can deploy there.

## Local verification

Run `bash tests/integration.sh` with the controller dependencies installed. This uses local Ansible targets, a fixture configuration repository, and generated full vendor bundles. To exercise the companion repository's example files directly, set `MYAPP_CONFIG_EXAMPLE` to its absolute path.
