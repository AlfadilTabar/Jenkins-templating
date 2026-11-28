# MyApp component deployment guide

Use one independently versioned Nexus package per component. Each environment has a single Git descriptor that declares its approved versions and the rollout policy. Ansible can deploy the selection in that descriptor or install every pinned component on clean hosts.

![Deployment and promotion flow. The descriptor branches into selective and full modes, then defines stages, host waves, live status, rollback, and promotion.](myapp-component-flow.png)

## Repositories and responsibilities

| Location | Responsibility |
| --- | --- |
| Nexus | Stores the original, immutable vendor tarball for each component build. |
| `myapp-component-config` | Owns `envs/dev/deployment.yml`, `envs/test/deployment.yml`, `envs/prod/deployment.yml`, and `envs/dr/deployment.yml`. Also holds each environment's ordinary property values, Jinja templates, and static configuration files. |
| `myapp-component-ansible` | Owns host inventories, the deployment controller, Ansible playbooks, the lifecycle helper, GitLab CI, and verification tests. |
| CI secret store | Supplies target-specific secret values, SSH credentials, and Nexus credentials where needed. |
| Application hosts | Store active release links, installed files, runtime state, and per-release receipts. Ansible checks hosts directly for live status. |

The vendor publishes `payment/10.1/payment-10.1.tar.gz`, `orders/11.2/orders-11.2.tar.gz`, and `reporting/4.0/reporting-4.0.tar.gz` as separate artifacts in the example. Version strings are independent. A rebuild downloads the approved versions from the chosen environment descriptor.

| Environment | Payment | Orders | Reporting | Default mode |
| --- | --- | --- | --- | --- |
| Dev | `10.1` | `11.2` | `4.0` | Selective |
| Test | `10.0` | `11.1` | `4.0` | Selective |
| Production (`prod`) | `10.0` | `11.1` | `4.0` | Selective |
| DR | `10.0` | `11.1` | `4.0` | Selective |

These are demonstration pins and hostnames. Replace them with approved Nexus metadata, actual inventory aliases, and your environment settings before deployment. The [complete dev descriptor](#complete-dev-descriptor) at the end of this guide is also included in the config ZIP.

## Read and edit the descriptor

The deployment controls come first. They refer to component names in the catalog farther down.

```yaml
environment: dev
deployment:
  mode: selective
  selective:
    components: [payment, orders]
  full:
    components: all
  stages:
    - name: core-services
      components: [payment, orders]
      max_parallel: 2
    - name: reporting
      components: [reporting]
      max_parallel: 1
  host_waves:
    - [dev-app-01]
    - [dev-app-02, dev-app-03]
```

| Field | Effect |
| --- | --- |
| `environment` | Must match the environment selected at runtime and the descriptor's directory. |
| `deployment.mode` | Default `selective` or `full` for plan, validation, deploy, and promotion. Live status defaults to full visibility. |
| `deployment.selective.components` | Exact list of components eligible in selective mode. This list controls scope. The stage list controls execution order. |
| `deployment.full.components` | Set to `all`. Full mode includes every component in the catalog using its own pinned package. |
| `deployment.stages` | Runs in list order. All work in an enabled stage must finish before the next stage starts. Every catalog entry must appear exactly once in this schedule. Selective mode filters each stage and skips empty stages. |
| `stage.max_parallel` | Maximum concurrent component playbook runs in a host wave. Each playbook runs on the assigned hosts in parallel. Within a wave, the listed stage order determines which components get scheduled first when there are more components than slots. |
| `deployment.host_waves` | Default ordered groups of **inventory host aliases**. A singleton provides a strict host order. Put multiple aliases in one wave for parallel host rollout. Include each host in the union of component target groups exactly once. |
| `components.<name>.host_waves` | Optional replacement for a component's inherited waves. List each host in that component's group exactly once. |
| `components.<name>.group` | Ansible group that hosts this component. It limits staging, activation and status. |
| `components.<name>.artifact` | `version` is a quoted string. `path` is relative to the Nexus release repository. `sha256` is the exact package checksum. |
| `service_user` | Unprivileged target account that runs the component hooks. Its primary group is applied to installed files. |
| `hook_timeout`, `health_retries`, `health_delay` | Maximum seconds per hook, health attempts during activation, and delay in seconds between failed attempts. |
| `config.values`, `config.files` | Values and secret references plus a list of Git source files and component-relative destinations. |

### Component order and host order

Without an override, a component inherits the environment waves, filtered to the members of its own group. The override replaces its waves. A component with no hosts in a particular wave skips that wave. Wave numbers synchronize the components within a stage. For example, an override can put orders on `dev-app-03` in wave 1 while payment runs on `dev-app-01`.

| Stage | Wave | Payment | Orders | Reporting |
| --- | --- | --- | --- | --- |
| Core services | 1 | `dev-app-01` | `dev-app-03` | |
| Core services | 2 | `dev-app-02` and `dev-app-03` | `dev-app-01` | |
| Core services | 3 | Complete | `dev-app-02` | |
| Reporting | 2 | | | `dev-app-02` and `dev-app-03` in full mode |

The core stage can run payment and orders concurrently in waves 1 and 2. Each stage starts its own host-wave sequence. Reporting has no hosts in the default first wave. To roll one component out completely before another starts, put them in successive stages. Use `max_parallel: 1` to serialize component work within each host wave of a stage. To require a common canary host across all components, use the environment default and omit component overrides. Use singleton waves to serialize hosts.

Concurrent components can target the same host. Group independent components in parallel stages only when their vendor hooks, release paths, ports, and shared resources allow that. Deployment stages enforce order and do not select dependencies automatically. For a selective deployment, include required companion upgrades in the selection or confirm the already-active versions are compatible.

## Vendor package contract

Each Nexus tarball contains a top-level `manifest.yml` and a complete `payload/` for exactly one component. The vendor can include JARs, WARs and accompanying files. The package can omit environment properties or include placeholders that the deployment overlays.

```text
payment-10.1.tar.gz
├── manifest.yml
└── payload/
    ├── payment.jar
    ├── config/
    │   └── application.properties
    └── bin/
        ├── start
        ├── stop
        ├── health
        └── version
```

```yaml
component: payment
version: "10.1"
required_properties:
  config/application.properties:
    - db.url
    - db.password
```

`required_properties` is optional. It maps paths inside `payload/` to required Java property keys. After configuration is applied, the controller verifies that the listed files exist and required values are nonblank. The controller also verifies the full archive SHA-256 and checks that the component name and version in the manifest match the approved pin. The tar format accepts normal files and directories with relative paths. Use member paths beginning with either `manifest.yml` or `payload/`.

| Hook | Lifecycle contract |
| --- | --- |
| `bin/start` | Required and executable. Starts the release and returns. |
| `bin/stop` | Required and executable. Stops the service and succeeds when already stopped, allowing rollout recovery to be retried safely. |
| `bin/health` | Required and executable. Returns zero when this service instance is ready. |
| `bin/version` | Optional and executable. Prints the running service build version. When present, activation and status compare its output with the selected release's receipt. |

The helper runs the hooks as the component service user from the release directory with `MYAPP_COMPONENT`, `MYAPP_COMPONENT_DIR` and `MYAPP_SHARED_DIR`. The vendor launcher must read the staged config explicitly or by its normal relative path. Put logs, PID files, and mutable service state under the shared directory or another site-approved path. Release directories are managed by the deployment account.

## Configuration and secrets

The config descriptor maps a Git source to a destination inside the installed component:

```yaml
config:
  values: envs/dev/files/payment/values.yml
  files:
    - source: envs/dev/files/payment/application.properties.j2
      destination: config/application.properties
      action: template
    - source: envs/dev/files/payment/logback.xml
      destination: config/logback.xml
      action: copy
```

The runner resolves the descriptor and these files from the same tracked Git commit. `values.yml` holds quoted ordinary values under `parameters` and points to environment variables under `secret_env`. The payment example uses `MYAPP_SECRET_PAYMENT_DB_PASSWORD`, and orders and reporting have their own variables. Set each variable's value in the target environment's CI or runner secret store.

The `template` action renders Jinja2 with resolved `settings`. The shipped template maps `db_url` and `db_password` to `db.url` and `db.password`, and uses `java_property` to escape Java properties syntax. The `copy` action installs static bytes and is used for `logback.xml`. The generated properties file is mode `0640`. The temporary transport archive contains resolved properties, and the copy task suppresses payload logging and diffs. The receipt and report store provenance and versions.

After rotating a secret, commit an updated `secret_revision` in the relevant values file. The revision participates in the config fingerprint. The actual secret value is supplied by the runner at deploy time. Retain access to the intended historical secret versions if you need to reproduce an earlier release on a clean host.

## Set up and run

1. Extract both ZIPs and import their directories into separate GitLab repositories. Commit the config files. Each operation reads a committed revision, so working-tree edits alone have no effect.
2. Populate the inventory files in `myapp-component-ansible/inventories/`. Update the descriptor waves with those aliases. Make sure the hosts appear in the intended component groups. The shipped inventory uses `/opt/myapp`, SSH user `ansible`, and privilege escalation.
3. Prepare Linux targets with Python 3.8 or later at `/usr/bin/python3`, the application runtime, util-linux for `runuser`, and the required service accounts. Provide SSH, host keys, and sudo access for the deployment account.
4. Publish each vendor package to your Nexus release repository and record its approved version, path and SHA-256 in Git. Install any required CA certificates on the runner and configure the repository to retain the artifacts used for deployment and recovery.
5. Install the controller requirements and configure credentials. Use Python 3.12, Git, and OpenSSH on the controller.

```bash
cd myapp-component-ansible
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

config=../myapp-component-config
ref=$(git -C "$config" rev-parse HEAD)
export MYAPP_NEXUS_BASE_URL=https://nexus.example.com/repository/vendor-releases

python tools/deploy.py plan --environment dev --config-repo "$config" --config-ref "$ref"
python tools/deploy.py deploy --environment dev --config-repo "$config" --config-ref "$ref"
python tools/deploy.py status --environment dev --config-repo "$config" --config-ref "$ref"
```

Set `MYAPP_NEXUS_USER` and `MYAPP_NEXUS_PASSWORD` together when using Basic authentication. The downloader requires direct HTTPS URLs. Supply the `MYAPP_SECRET_*` values securely before deploying. `plan` emits the resolved scope, package pins, target aliases and host waves as JSON. It checks the descriptor and inventory without connecting to the application hosts or downloading artifacts. `validate` performs the same planning validation with a short confirmation.

To use the full mode once, use the explicit override:

```bash
python tools/deploy.py plan --environment dr --mode full --config-repo "$config" --config-ref "$ref"
python tools/deploy.py deploy --environment dr --mode full --config-repo "$config" --config-ref "$ref"
```

To make full mode the default for DR, commit `deployment.mode: full` in its descriptor and keep `deployment.full.components: all`. Both definitions can remain in the descriptor, so `--mode selective` can restore the defined selection for a later run. Selective deploy reports cover the selected components. Run status without a mode override for the entire environment, or use `status --mode selective` for just the selection. Use `--report PATH` to keep reports from separate invocations.

## Execution, state, and rollback

1. The controller snapshots the approved Git commit, validates the desired scope, and resolves the aliases in Ansible inventory. It checks the rollout entries for full host and component coverage.
2. It fetches and verifies every selected package, applies the target configuration, validates required properties, and creates each component payload.
3. Ansible stages every selected payload on all its target hosts before any activation. The host helper installs it in `/opt/myapp/<component>/releases/<release-id>/`.
4. The controller runs the descriptor stages and host waves. It gives each concurrent Ansible process its own input file and limits the play to that component group's assigned wave hosts. Hosts listed together in a wave can run concurrently.
5. The helper locks the component on each host, records the previous active release, stops the old instance, switches `current` atomically, starts the new instance, and checks health. If the same release is already active and healthy, it skips the restart.
6. On activation failure, the helper attempts to restore the previous release on the affected host and component. The controller waits for work already running in the current parallel batch and schedules no further batches, waves, or stages. Earlier successful releases may remain active. A later invocation can attempt recovery of an interrupted journal.
7. The controller writes `reports/status.json` after deployment or a status query. It lists the environment and config baseline commit, plus one result per selected host and component.

| Host path | Purpose |
| --- | --- |
| `current` | Stable symlink for the active release. |
| `releases/<release-id>/.deployment.json` | Receipt with component version, package path and checksum, config commit and fingerprint, release ID, and optional CI job URL. |
| `.activation.json` | Temporary journal of the previously active release. A pending journal indicates interruption or incomplete recovery. |
| `.lock` | Per-component host lock for staging, activation, and status. |
| `shared/` | Service-owned runtime state shared by the releases of one component. |

The expected release ID depends on the environment, component, artifact pin, and configuration fingerprint. Changing unrelated Git files or rollout policy alone leaves that ID unchanged. In the report, compare `release_id` with `expected_release_id` or check `matches_baseline`. Use `component_version` for the active installed build. Use `running_version` for the application-confirmed build when the vendor supplies a version hook. Other possible states include `absent`, `unhealthy`, `unknown` for missing host results, and `activation_interrupted`.

### Manual rollback in GitLab CI

Start a web pipeline on the deployment project's protected default branch. For example, to restore only payment in production, set these inputs and run the manual **rollback** job:

```text
OPERATION=rollback
DEPLOY_ENV=prod
ROLLBACK_CONFIG_REF=<approved-config-commit-sha>
ROLLBACK_MODE=selective
ROLLBACK_COMPONENTS=payment
```

Configure `CONFIG_PROJECT_PATH` as for regular operations. The job requires the full rollback config commit SHA from the config project's default branch history. You can use a previously approved commit with matching inventory or a reviewed revert that restores the chosen package pins and settings while keeping the current topology. GitLab applies the deployment environment's protections and shares the same resource group as a regular deploy.

Selective is the default rollback mode. A comma-separated list such as `payment,orders` overrides the descriptor selection and keeps its stage and host order. If you leave the list blank, the job uses the selective list in the rollback commit. Set `ROLLBACK_MODE=full` and leave the list blank to restore all components pinned at that commit.

The job runs the same package checks, staged rollout, activation and health checks as a deploy. If a restored release fails its health checks, the host helper attempts to recover the release that was active before that rollback attempt. Verify the resulting versions and health in `reports/status.json`. To make the restored versions the desired state for future deployments, merge the rollback pins and configuration into the current baseline. Retain the referenced Nexus packages and compatible secrets. Database migrations and runtime data use their own recovery procedures.

## Promotion and DR

The promotion route is **dev → test → prod → DR**. For example, after deploying the selected payment and orders packages in dev:

```bash
python tools/deploy.py promote --environment dev --to test \
  --config-repo "$config" --config-ref "$ref" --output reports/test-deployment.yml
```

The controller checks all selected dev hosts against the approved source commit. It writes a target descriptor candidate only if the selected installs are healthy and match. The candidate copies the selected `artifact` pins. The target's mode, stages, host waves, component group, other package pins, and configuration come from its existing descriptor. GitLab publishes candidates at `reports/candidate/envs/<target>/deployment.yml`.

Review the diff and merge the target changes in the config project. If the target baseline advanced after the candidate was generated, apply the intended artifact changes to the latest target descriptor. Set `ref` to that newly approved commit and deploy the intended scope in test. Repeat through production and DR. A full promotion checks and carries all source component pins. Selective promotion uses the source descriptor's selection unless you specify full mode.

For clean-host recovery, provision the target hosts and restore required database and persistent state. Use an approved DR descriptor commit and provide the matching DR credentials. Then run `deploy --environment dr --mode full` and verify every status row. Ensure Git and Nexus, including referenced historical packages, are reachable during recovery. Host provisioning, traffic draining, data restoration, and database migration procedures belong to your environment.

## GitLab CI and Jenkins

The shipped `.gitlab-ci.yml` runs Python compilation, scheduler tests, and local Ansible integration tests. On a protected default branch, a web-launched pipeline can expose a manual operation job. Configure protected environments and approvals for production and DR in GitLab.

| GitLab input | Purpose |
| --- | --- |
| `OPERATION` | `deploy`, `status`, `promote` or `rollback`. `none` runs checks without an operation job. |
| `DEPLOY_ENV` | Target environment for deploy, status and rollback. Source environment for promotion. |
| `DEPLOY_MODE` | `descriptor` keeps command defaults. Explicit `selective` and `full` override a run. |
| `TARGET_ENV` | Adjacent destination for promotion. |
| `CONFIG_PROJECT_PATH`, `CONFIG_REF` | Config GitLab project and full commit SHA for deploy, status, and promotion. Allow deployment-project job tokens to read the config project. |
| `ROLLBACK_CONFIG_REF` | Full approved config SHA for the rollback job. |
| `ROLLBACK_MODE`, `ROLLBACK_COMPONENTS` | Default selective rollback or explicit full rollback, with an optional comma-separated component selection in selective mode. |
| `SSH_PRIVATE_KEY`, `SSH_KNOWN_HOSTS` | File variables for remote access. |
| `MYAPP_NEXUS_*`, `MYAPP_SECRET_*` | Download access and environment-scoped application configuration. |

GitLab serializes operations by environment within the deployment project. It stores reports as job artifacts for 30 days. Jenkins can call the same CLI using an approved config checkout, the same credentials, an environment lock and artifact collection for `reports/`. If both systems deploy to the same targets, coordinate them under a shared environment lock.

## Repository file map

| Ansible repository path | Purpose in the flow |
| --- | --- |
| `.gitignore` | Excludes virtualenvs, config clones, reports, and bytecode from version control. |
| `.gitlab-ci.yml` | Defines tests, protected operation jobs, config checkout, SSH setup, locking and report retention. |
| `README.md` | Quick start and operations reference. |
| `requirements.txt` | Ansible, Jinja2, PyYAML and Java properties dependencies for the controller. |
| `ansible.cfg` | SSH host key checking, Python interpreter discovery, and retry-file behavior. |
| `inventories/{dev,test,prod,dr}/hosts.yml` | Per-environment SSH attributes, host aliases, component group memberships and deploy settings. |
| `tools/ci_checkout.sh` | Clones the GitLab config project with the job token and checks that the supplied full SHA is reachable from its default branch. |
| `tools/deploy.py` | Main command. Resolves the approved Git snapshot, verifies packages, builds payloads, invokes Ansible, creates status output, and writes promotion candidates. |
| `tools/rollout.py` | Validates scope, stages and host coverage, computes inherited or overridden waves, and schedules bounded concurrent component activations. |
| `tools/archive.py` | Shared safe tar extraction, relative-path checks and file permission handling. |
| `tools/host.py` | Remote staging, per-component lock, symlink switching, hook execution, rollback, and status. |
| `playbooks/stage.yml` | Transfers each private component tar to the target group, runs the remote stage action, and attempts cleanup. |
| `playbooks/activate.yml` | Activates the component on the host wave selected by the controller's Ansible limit. |
| `playbooks/status.yml` | Reads health and receipt data from hosts and saves the results for the controller. |
| `playbooks/vars/host.yml` | Shared arguments passed to the remote lifecycle helper by all three plays. |
| `tests/make_packages.py` | Generates distinct demo package tarballs and bad-health and missing-property cases. |
| `tests/test_rollout.py` | Tests both modes, host validation, inheritance, component parallelism, order, and failure barriers. |
| `tests/integration.sh` | Drives real local Ansible runs and tracks their outputs through deploy, promotion, recovery, status, and rollback. |
| `tests/example/envs/` | Copy of the config repository's environment descriptors and files. Makes the Ansible repository's tests self-contained. |

| Config repository path | Purpose in the flow |
| --- | --- |
| `.gitignore` | Ignores common local and secret files. Actual credentials belong in the secret store. |
| `README.md` | Explains ownership, rollout policy, package pin updates, and secret revisions. |
| `envs/{dev,test,prod,dr}/deployment.yml` | Four complete environment descriptors with policy at the top and package pins below. |
| `envs/<environment>/files/{payment,orders,reporting}/values.yml` | Ordinary settings, secret variable references, and a config revision marker. |
| `envs/<environment>/files/{payment,orders,reporting}/application.properties.j2` | Renders Java properties using the environment values and resolved secrets. |
| `envs/<environment>/files/{payment,orders,reporting}/logback.xml` | Example static logging configuration copied to the named component's release. |

## Verification and troubleshooting

With the controller dependencies installed, run:

```bash
python tests/test_rollout.py
bash tests/integration.sh
```

The tests use generated component tarballs and local Ansible inventory aliases. They cover selective and full installation, parallel components, host wave boundaries, promotion through DR, Java properties rendering, health-triggered rollback, selective and full rollback to an earlier config commit, and promotion blocked by drift. Your real Nexus, SSH targets and GitLab permissions should be verified with the environment's own deployment pipeline.

| Symptom | Check |
| --- | --- |
| Unknown component or incomplete stage schedule | Every catalog component needs exactly one entry in the stage list. Selective component names must exist in the catalog. |
| Missing, extra, or repeated aliases in a wave plan | Compare the descriptor with the actual inventory host aliases and group memberships. Use `plan` before deploying. |
| Version or checksum mismatch | Compare the vendor manifest, uploaded package, and approved `artifact` mapping. |
| Missing required property | Check the vendor requirement, target config mapping, values file, and target secret binding. Package preparation fails before activation. |
| Deployment stopped after a failed wave | Review the activation log and the per-host report. Investigate hook failure and whether previously successful hosts should keep their release or be rolled back. |
| Source healthy but promotion blocked | Compare actual release IDs with the selected source config commit. A changed pin or config can require a source deploy before promotion. |

## Complete dev descriptor

```yaml
environment: dev
deployment:
  mode: selective
  selective:
    components:
    - payment
    - orders
  full:
    components: all
  stages:
  - name: core-services
    components:
    - payment
    - orders
    max_parallel: 2
  - name: reporting
    components:
    - reporting
    max_parallel: 1
  host_waves:
  - - dev-app-01
  - - dev-app-02
    - dev-app-03
components:
  payment:
    group: payment_nodes
    service_user: myapp
    hook_timeout: 30
    health_retries: 10
    health_delay: 3
    artifact:
      version: '10.1'
      path: payment/10.1/payment-10.1.tar.gz
      sha256: 103188c03f8f22d0e3ce1a89445dd73aa5d2a850a7648141e6711b997d6b00ae
    config:
      values: envs/dev/files/payment/values.yml
      files:
      - source: envs/dev/files/payment/application.properties.j2
        destination: config/application.properties
        action: template
      - source: envs/dev/files/payment/logback.xml
        destination: config/logback.xml
        action: copy
  orders:
    group: orders_nodes
    service_user: myapp
    hook_timeout: 30
    health_retries: 10
    health_delay: 3
    artifact:
      version: '11.2'
      path: orders/11.2/orders-11.2.tar.gz
      sha256: 37419573b34fe4d3a8ef5c3f2ef4321ea5d61d4cd55e75601ebdbd5e65ce4e20
    config:
      values: envs/dev/files/orders/values.yml
      files:
      - source: envs/dev/files/orders/application.properties.j2
        destination: config/application.properties
        action: template
      - source: envs/dev/files/orders/logback.xml
        destination: config/logback.xml
        action: copy
    host_waves:
    - - dev-app-03
    - - dev-app-01
    - - dev-app-02
  reporting:
    group: reporting_nodes
    service_user: myapp
    hook_timeout: 30
    health_retries: 10
    health_delay: 3
    artifact:
      version: '4.0'
      path: reporting/4.0/reporting-4.0.tar.gz
      sha256: b0107c66770556ff35350c925ceea90409ed1e8eeff60e2b01a80c1f043b683b
    config:
      values: envs/dev/files/reporting/values.yml
      files:
      - source: envs/dev/files/reporting/application.properties.j2
        destination: config/application.properties
        action: template
      - source: envs/dev/files/reporting/logback.xml
        destination: config/logback.xml
        action: copy
```
