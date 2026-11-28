# MyApp deployment and configuration walkthrough

This guide covers the two delivered repositories and all **80 files** inside their archives. It explains how a vendor bundle moves from Nexus to a running component, how your team supplies properties from Git, and how to promote or rebuild an environment. The [source appendix](#complete-file-contents) includes the exact contents of every shipped file. Files with identical contents share a listing, with every path identified.

## Contents

1. [Application delivery model](#application-delivery-model)
2. [Set up the repositories and environments](#set-up-the-repositories-and-environments)
3. [Vendor bundle contract](#vendor-bundle-contract)
4. [How configuration becomes an application properties file](#how-configuration-becomes-an-application-properties-file)
5. [Walk through a deployment](#walk-through-a-deployment)
6. [Read the status report](#read-the-status-report)
7. [Promote through dev, test, production, and DR](#promote-through-dev-test-production-and-dr)
8. [Roll back or rebuild](#roll-back-or-rebuild)
9. [Reference for every repository file](#reference-for-every-repository-file)
10. [Tests and troubleshooting](#tests-and-troubleshooting)
11. [Complete file contents](#complete-file-contents)

## Application delivery model

The vendor publishes a **full application release in one tarball**. A tarball can hold `orders`, `billing`, and every other component, each in its own complete directory. The application support team owns the deployment baseline for each environment. That baseline lists the approved artifact and configuration for each component.

| Source | What it owns | How the deployment uses it |
| --- | --- | --- |
| Vendor | Executables, supporting files, lifecycle commands, component build versions, and the list of required property keys | The controller copies the chosen component directory and validates it against the internal baseline. |
| Nexus | The original vendor tarball at a stable repository path | The controller downloads the tarball and checks its SHA-256 against the baseline. |
| `myapp-config` GitLab project | Environment baselines, property templates, non-secret values, static files, and secret variable names | The controller reads tracked files from the approved Git commit. |
| CI secret store | Environment-specific passwords and tokens, plus access credentials | The runner provides values for the variable names in Git. |
| `myapp-ansible` GitLab project | Inventories, command-line controller, Ansible playbooks, host code, pipeline, and tests | The controller prepares the release. Ansible transfers it and invokes the host code. |
| Application hosts | Active release links, installed copies, service health, and runtime state | A live status query reads each active release receipt and invokes the health and optional version hooks. |

`all` selects every component in **the target environment baseline**. A baseline can point to multiple tarballs. The delivered dev example pins `orders` to bundle `2026.11.23.1` and `billing` to bundle `2026.11.10.2`. The newer bundle can contain both components. A dev rebuild still uses the older bundle for `billing` because that is the artifact approved for dev. The test, prod, and DR example baselines start with both components pinned to the older bundle.

There are three identifiers to look for in a deployment report:

| Identifier | Example | What it identifies |
| --- | --- | --- |
| Bundle version | `2026.11.23.1` | The vendor tarball. The path locates it and the SHA-256 pins its exact bytes. |
| Component version | `3.8.2` | The application build inside the tarball. Several components can have different build versions in one tarball. |
| Config commit and release ID | A Git SHA and a 64-character SHA-256 | The config commit identifies the approved snapshot. The release ID is based on environment, component, artifact mapping, and config fingerprint. It is the name of the installed release directory. |

## Set up the repositories and environments

### Offline supply chain and runner setup

Use a protected **GitLab shell runner** tagged `myapp-offline`. Assign this tag exclusively to shell runners provisioned with Linux, Python 3.12 with `venv` and bundled pip, Bash, Git, OpenSSH, util-linux, coreutils, and the internal CA. Use the same pre-provisioned tooling for Jenkins or local administration. The project uses Ansible built-ins and needs no separate collection or role downloads.

| Connection | Permitted internal purpose |
| --- | --- |
| Runner → GitLab | Check out the deployment project, fetch the approved config project and publish artifacts. Configure GitLab and any artifact storage to stay internal. |
| Runner → Nexus | Fetch the approved wheel archive and immutable vendor archives using direct HTTPS. Place both in hosted repositories fed by the internal supply process. |
| Runner → deployment hosts | SSH and Ansible operations, using internal DNS and trusted host keys. |
| Runner, Nexus, and application or host network policy | Enforce default-deny public egress. Permit only approved internal paths including internal DNS and any application dependencies. This covers the runner's initial source checkout, Nexus service, and independently supplied application and vendor hook code. |

Publish the wheels for the complete Python controller dependency set to an internal Nexus raw hosted repository. Include exactly one compatible wheel for each installed distribution, with every required transitive dependency. Select wheels for the runner's OS, CPU, Python, and native ABI. The following packaging step uses an already approved local wheel directory. Publish the tar and its independently reviewed checksum through your internal Nexus process:

```bash
cd /path/to/approved-wheels
tar -czf ../myapp-python312-wheelhouse-001.tar.gz -- *.whl
sha256sum ../myapp-python312-wheelhouse-001.tar.gz
```

The archive contains only regular `.whl` files at its root, with no wrapper directory or source distributions. Use versioned, immutable Nexus paths. Python's bundled pip creates the venv locally. The bootstrap uses an explicit list of local wheel paths, disables package indexes, dependency fetching and pip version checks, clears inherited pip settings, validates the constrained requirements, and runs `pip check`. If a wheel or dependency is missing, incompatible, or outside the approved requirements range, republish a corrected wheel archive through the internal approval process.

Set these variables at the runner or project boundary, with values from your infrastructure:

| Variable | Value |
| --- | --- |
| `MYAPP_NEXUS_AUTHORITY` | Exact approved Nexus hostname and optional port. The downloader requires both wheel and vendor URLs to match it. |
| `MYAPP_NEXUS_WHEELHOUSE_URL` | Direct internal HTTPS URL of the approved wheel archive. Required before every CI job, including checks. |
| `MYAPP_NEXUS_WHEELHOUSE_SHA256` | Lowercase SHA-256 of the wheel archive. Required before every CI job. |
| `MYAPP_NEXUS_WHEELHOUSE_USER`, `MYAPP_NEXUS_WHEELHOUSE_PASSWORD` | Optional paired Basic authentication credentials for wheelhouse downloads. If access requires credentials, scope these so check jobs can read them too. |
| `MYAPP_NEXUS_BASE_URL` | Direct internal HTTPS base URL of the hosted vendor repository. Required for deploy and rollback when reading from Nexus. |
| `MYAPP_NEXUS_USER`, `MYAPP_NEXUS_PASSWORD` | Optional paired Basic authentication credentials for vendor artifacts. |
| `MYAPP_GITLAB_AUTHORITY` | Exact approved internal GitLab hostname and optional port expected in `CI_SERVER_URL`. Required for CI operation jobs. |

The network helper rejects URL credentials, queries, fragments, wrong authorities, and non-HTTPS requests. It uses system TLS trust, ignores automatic proxy configuration and refuses redirects. The secondary Git clone checks the GitLab authority, verifies TLS, disables redirects and automatic proxy configuration, and clones without submodules. The CI source checkout skips submodules and LFS materialization. Public egress enforcement belongs to the runner, Nexus and target network policies.


### Initial setup

1. Extract the two archives and import their directories as separate GitLab projects. Commit the files in both projects. The CLI reads a Git commit in the config project, so an unpacked directory without a Git repository cannot supply a configuration snapshot.
2. Replace the example hostnames in `myapp-ansible/inventories/{dev,test,prod,dr}/hosts.yml`. Define the inventory groups that will host each component. Put a host in both groups if it runs both components. Replace the service account `myapp` wherever your real service account differs.
3. Prepare the Linux targets with Python 3.8 or later at `/usr/bin/python3`, a Java runtime supported by the application, `runuser` from util-linux, and the service accounts. Configure the Ansible account and its sudo access. The default deploy root is `/opt/myapp`. For an existing application, plan the cutover from the old layout. The host helper expects `current` to be absent on first installation or already be a managed symlink.
4. Publish actual vendor bundles to a Nexus repository that retains release artifacts and disables replacement at the same path. Update the `artifact` fields in Git with the exact path, digest, bundle version, and component version. The checked-in filenames and digests describe demo bundles. Keep the bundles that approved baselines and rollback points reference.
5. Change the files under `myapp-config/envs/<environment>/files/<component>/` to the values and destination paths required by your application. Put secrets in the runner's secret store and scope them to the correct environment. Shared variable names such as `MYAPP_SECRET_ORDERS_DB_PASSWORD` can resolve to different values in dev, test, prod, and DR.
6. Configure the runner or operator workstation with SSH access and the verified host keys for its target hosts. Install the internal CA if your GitLab or Nexus HTTPS certificates require it. Anyone deploying from a workstation or a second orchestrator should use the same environment lock as the primary deployment workflow or coordinate a maintenance window for exclusive access.

For direct CLI use, run from the Ansible project with the config project cloned alongside it:

```bash
cd myapp-ansible
python3.12 -m venv .venv
source .venv/bin/activate
# Export the approved Nexus wheelhouse variables before this command.
python tools/bootstrap_python.py
CONFIG_REPO=../myapp-config
CONFIG_SHA=$(git -C "$CONFIG_REPO" rev-parse HEAD)
export MYAPP_NEXUS_BASE_URL=https://nexus.corp.example/repository/vendor-releases
```

The `HEAD` in that example should resolve to the approved configuration commit. Take secrets from the configured credential bindings. Supply both `MYAPP_NEXUS_USER` and `MYAPP_NEXUS_PASSWORD` if Nexus uses Basic authentication. A Nexus server that permits anonymous downloads can leave both unset. The artifact URL is the HTTPS base URL plus the URL-encoded repository-relative `artifact.path`. The downloader requires a direct endpoint and rejects redirects.

### GitLab and Jenkins

| Input or project setting | Value or purpose |
| --- | --- |
| `CONFIG_PROJECT_PATH` | GitLab namespace and project of `myapp-config`, for example `platform/myapp-config`. |
| `CONFIG_REF` | Full lowercase commit SHA reachable from the config project's default branch. |
| `OPERATION` | `deploy`, `status`, or `promote`. The default `none` runs the check stage without an operation job. |
| `DEPLOY_ENV` | `dev`, `test`, `prod`, or `dr`. For promotion this is the source. |
| `TARGET_ENV` | For promotion only. Choose the next environment in the defined route. |
| `COMPONENTS` | `all`, a single name such as `orders`, or a comma-separated list such as `orders,billing`. |
| `SSH_PRIVATE_KEY` and `SSH_KNOWN_HOSTS` | GitLab **file variables**. The operation job copies them into `~/.ssh/id_ed25519` and `~/.ssh/known_hosts`. |
| `MYAPP_NEXUS_*` | HTTPS repository location plus optional Nexus download credentials. |
| `MYAPP_SECRET_*` | Secret values named by the target environment's `values.yml` files. |
| Job token access | In the config project, allow the deployment project's `CI_JOB_TOKEN` to read the repository. |
| Environment protections | Configure protected branch, runner, operator permissions, and production and DR approvals in GitLab. |

Start a pipeline from the GitLab web interface using the protected default branch of the Ansible project. After the check job, run the displayed manual job. The resource group `myapp-$DEPLOY_ENV` serializes operations in that GitLab project by deploy environment, or by source environment for promotion. GitLab retains the `reports/` job artifacts for 30 days.

For Jenkins, bind the same SSH and environment-scoped credentials on a trusted agent, check out the approved config commit, run the same `tools/deploy.py` command, and archive `reports/`. The shipped pipeline definition is for GitLab CI. Jenkins configuration and any lock shared across both systems live in your infrastructure.

## Vendor bundle contract

A typical vendor archive looks like this:

```text
myapp-2026.11.23.1.tar.gz
├── manifest.yml
└── components/
    ├── orders/
    │   ├── orders.jar
    │   ├── config/application.properties
    │   └── bin/{start,stop,health,version}
    └── billing/
        ├── billing.war
        ├── config/application.properties
        └── bin/{start,stop,health,version}
```

The artifacts inside each directory can be whichever JARs, WARs, and supporting files that component needs. The example shows a placeholder properties file. The Git mapping can create that file during deployment if the vendor omits it. Package relative archive paths beginning with `manifest.yml` or `components/`. The extractor accepts regular files and directories and rejects duplicate entries, link entries, special files, and unsafe paths.

The vendor writes the archive's root `manifest.yml`:

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

`required_properties` is an optional mapping from a file path within the component to a list of required Java property keys. The controller parses each listed file after the environment overlay and rejects missing or blank required values. A vendor handover should describe the meaning and format of each setting, application compatibility, and migration requirements. The shipped code checks file presence, property presence, artifact integrity, expected versions, and service health. Compatibility between component builds is reviewed when approving a mixed-version baseline.

| Vendor file | Required | Contract |
| --- | --- | --- |
| `bin/start` | Yes | Executable file with a shebang. Starts the release and returns. A long-running Java process needs to outlive the start command and direct its logs to a writable location. |
| `bin/stop` | Yes | Stops the instance and returns successfully when it is already stopped. The rollback and interruption recovery paths also call it. |
| `bin/health` | Yes | Exits with code zero when the instance is ready. Activation retries this check. A live status query makes one attempt per component and host. |
| `bin/version` | Optional | Prints only the build version of the **running** application. A version endpoint or process-aware command can implement it. The host compares trimmed stdout with the component version in the receipt. |

Hooks run from the chosen release directory as the component service user. They receive `MYAPP_COMPONENT_DIR`, `MYAPP_SHARED_DIR`, `MYAPP_COMPONENT`, and a small environment that includes `PATH`, `HOME`, `USER`, and `LOGNAME`. The helper does not forward the runner's environment. The application launcher must read the deployed configuration file. Direct runtime logs, PID files, and other writable state to the component's `shared/` directory or the application's approved state path.

## How configuration becomes an application properties file

For example, a test baseline contains this mapping for `orders`:

```yaml
config:
  values: envs/test/files/orders/values.yml
  files:
    - source: envs/test/files/orders/application.properties.j2
      destination: config/application.properties
      action: template
    - source: envs/test/files/orders/logback.xml
      destination: config/logback.xml
      action: copy
```

1. The controller resolves `--config-ref` to a Git commit, runs `git archive`, and extracts the tracked files into a private temporary directory. Git working-tree edits are used only after committing them and selecting that commit.
2. `config.values` and every `source` path are relative to the root of the config repository. They must begin with `envs/test/` in this example. Each `destination` is relative to the selected component directory. A mapping cannot overwrite a lifecycle hook or the deployment receipt.
3. `values.yml` supplies `parameters` as quoted strings and `secret_env` as names of `MYAPP_SECRET_*` variables. Test's `orders` values point to the example database `test-db.example.invalid:5432/orders`, use `"5000"` for the timeout, and name `MYAPP_SECRET_ORDERS_DB_PASSWORD` as the source of `db_password`. The controller fails if a declared secret variable is missing or empty.
4. The `template` action runs Jinja2 with strict handling of undefined values. Its `settings` object contains both normal and resolved secret values. The template maps `settings.db_url` to `db.url`, `settings.db_password` to `db.password`, and `settings.timeout_ms` to `http.timeout.ms`. The `java_property` filter escapes Java properties characters, including spaces and separators. The `copy` action preserves the source bytes, which is how `logback.xml` reaches the release.
5. The controller sets mapped files to `0640`, validates the vendor contract, and creates the component payload. The transferred payload and installed properties contain resolved secrets. The transfer task uses `no_log` and disables diffs. The report identifies the config snapshot and contains deployment metadata. Git stores the secret references and the revision marker.

The config fingerprint hashes the parsed values document, the values path, the mappings, and each mapped source file's bytes. It includes the secret reference and `secret_revision` marker. The release ID additionally hashes the component name, environment, and artifact mapping. A change to a mapped template, a normal parameter, or the secret revision produces a new release ID and triggers activation. An unrelated Git commit does not change that ID. When rotating secrets, update the stored value and commit a new `secret_revision`. Preserve access to earlier secret versions for recovery. The deployer reads the runner's currently injected value for the named variable, so provide the intended version when reconstructing a historical baseline on clean hosts.

## Walk through a deployment

Start with an approved dev baseline. Deploy just `orders` from its pinned version:

```bash
python tools/deploy.py validate --config-repo "$CONFIG_REPO" \
  --config-ref "$CONFIG_SHA" --environment dev --components orders

python tools/deploy.py deploy --config-repo "$CONFIG_REPO" \
  --config-ref "$CONFIG_SHA" --environment dev --components orders
```

| Phase | Files involved | What happens |
| --- | --- | --- |
| Resolve the request | `tools/deploy.py`, `envs/dev/deployment.yml`, `inventories/dev/hosts.yml` | The controller validates the baseline, selects named components, preserves `deployment_order`, and runs `ansible-inventory --list` to enumerate the hosts in the selected groups. The `validate` action ends after this planning step. |
| Verify and prepare | `tools/deploy.py`, `tools/archive.py`, vendor `manifest.yml`, mapped files in Git | The controller obtains the exact tarball, checks SHA-256 and versions, copies the chosen component, applies environment config, checks required properties, and appends a deployment receipt. Within an invocation it reuses a bundle already fetched by digest. |
| Stage on hosts | `playbooks/stage.yml`, `playbooks/vars/host.yml`, `tools/host.py` | Ansible copies each component payload to a private remote transfer directory and runs the helper. The helper extracts the files into a temporary directory and renames it to `releases/<release-id>` after verifying the receipt. The play attempts to clean up the transfer directory in its `always` block. All requested releases stage before any activation starts. |
| Activate on hosts | `playbooks/activate.yml`, `playbooks/vars/host.yml`, `tools/host.py`, vendor hooks | Ansible handles one component at a time. Its `serial` value sets the batch size within the component's host group. On each host the helper locks the component, records the old release, stops it, atomically points `current` to the new release, starts it, and verifies health. |
| Verify and report | `playbooks/status.yml`, `tools/host.py`, `tools/deploy.py` | The status play reads the active release and checks it. The controller compares the observed release ID with the requested one and writes the JSON report. |

The controller passes the play inputs in a temporary JSON file and packages the remote Python helper with its archive module as `myapp-host.pyz`. Ansible sends and runs that zipapp using the host's `/usr/bin/python3`. The host does not need the controller's Python packages.

### Installed layout

```text
/opt/myapp/orders/
├── .lock
├── .activation.json                 # Exists while activation or recovery is pending
├── current -> releases/<release-id>
├── releases/
│   └── <release-id>/
│       ├── .deployment.json
│       ├── bin/
│       ├── config/
│       └── orders.jar
└── shared/
```

| Path | Purpose |
| --- | --- |
| `.lock` | Advisory filesystem lock for the helper's operations on this component and host. GitLab's separate resource group coordinates whole jobs. |
| `.activation.json` | Journal of the previously active release, or null for a first install. The helper removes it after successful activation or successful recovery. |
| `current` | The stable symlink to the selected release directory. The helper validates its layout and uses an atomic replacement to switch it. |
| `releases/<release-id>/` | One prepared component payload. The helper reuses an existing release with the same ID. Installed releases are retained by this implementation. |
| `.deployment.json` | Receipt recording environment, component, component build, bundle version and path, digest, config commit and fingerprint, release ID, and optional CI job URL. |
| `shared/` | Writable state for the service account that survives release changes. The helper assigns ownership when it creates the directory. |

The helper gives installed directories and executable files mode `0750`, non-executable files mode `0640`, and sets their group to the service account's primary group. The intended privileged deployment owns the release files while the service account can read and execute them through that group. To isolate components that run on the same host, assign service accounts with separate primary groups. A rollout policy change such as a different batch size does not change the artifact/config release identity.

A rerun for the same release ID checks its health and skips the restart if healthy. If the release is already selected but unhealthy, activation tries to restart it. The original receipt stays with an existing release, so it can refer to an earlier Git commit that had the same effective config.

## Read the status report

Run status for an entire environment or add `--components orders` for a subset:

```bash
python tools/deploy.py status --config-repo "$CONFIG_REPO" \
  --config-ref "$CONFIG_SHA" --environment dev \
  --report reports/dev-status.json
```

The command prints a table and writes JSON. The default path is `reports/status.json`. The JSON has `environment`, `baseline_commit`, and `components`, a list with one row per selected component and inventory host. Each row has the inventory hostname, observed state, expected release ID, and baseline match. An active release contributes the receipt fields. `running_version` comes from the optional version hook. With that hook absent, `running_version` is null and `component_version` provides the active on-disk build.

| State or field | Meaning | What to check |
| --- | --- | --- |
| `healthy` and `matches_baseline: true` | The selected release is active and its health checks pass. The optional version hook also matched when present. | Confirm the baseline commit corresponds to the intended rollout. |
| `healthy` and `matches_baseline: false` | An active release is healthy but its ID differs from the expected one. This can follow a selective deployment, a new baseline awaiting deployment, or a rollback. | Compare the observed artifact and configuration with the approved baseline before redeploying. |
| `unhealthy` | A health check or running-version comparison failed. | Inspect the host's service logs and the hook implementation. |
| `absent` | The helper found no active release. | Check whether this is a fresh target or an interrupted or failed first installation. |
| `activation_interrupted` | The journal is still present. It can indicate a cancelled activation or an unsuccessful recovery. | Read the activation error and address the hook or host issue. A subsequent deploy attempts recovery under the component lock. |
| `unknown` | Ansible returned no usable status result for the host. | Check Ansible output for SSH, host key, sudo, Python, or helper failures. |
| `expected_release_id` | The release ID calculated from the selected config commit and artifact pin. | Compare it with the reported `release_id`. |
| `config_commit` and `baseline_commit` | `config_commit` is recorded in the installed release. `baseline_commit` is the snapshot being checked now. | They may differ after an unrelated commit while the release IDs still match. |

The status command returns zero only when every selected row is healthy and matches the baseline. Promotion uses this same condition. Deployment also returns nonzero if an earlier staging or activation step failed, even when rollback restored service. A command that stops before planning completes can exit before writing a report.

## Promote through dev, test, production, and DR

Promotion prepares a change to the target baseline. Deployment applies that approved change to the target hosts. Each promotion uses the source environment's live status as a gate and carries the existing artifact pin forward.

### Example with orders 3.8.2

1. **Dev.** Publish the full vendor bundle in Nexus. In a config merge request, update `components.orders.artifact` in `envs/dev/deployment.yml` with its path, digest, bundle version, and component build. In the delivered example this change is already represented. Review vendor compatibility with the pinned billing version. If the new build has different settings, update the dev mappings and values as part of approval. Merge and deploy `orders` on dev.
2. **Prepare the test change.** Use the configuration commit that still describes the running dev release and contains the current target baseline. This command queries the dev hosts and writes the full candidate test baseline:

   ```bash
   python tools/deploy.py promote --config-repo "$CONFIG_REPO" \
     --config-ref "$CONFIG_SHA" --environment dev --to test \
     --components orders --output reports/test-deployment.yml
   ```

   The generated change copies the `orders.artifact` mapping from dev into the test baseline. The test inventory group and service account still come from the test entry. Its configuration still points to `envs/test/files/orders/`. Its billing pin stays in place. The command validates the target config file mappings and requires the target baseline to already contain the selected component. It does not connect to target hosts, resolve target secrets, or test the vendor property contract in the target. Those steps take place during target deployment.
3. **Approve and deploy test.** Open a merge request against the config project with the proposed test artifact change and any required test settings. If another change advanced the target since generating the candidate, apply the selected artifact change to the latest baseline and review the diff. After the merge, update `CONFIG_SHA` to the newly approved config commit and run the test deployment.

   ```bash
   python tools/deploy.py deploy --config-repo "$CONFIG_REPO" \
     --config-ref "$CONFIG_SHA" --environment test --components orders
   ```

4. **Promote test to production and deploy.** Use `--environment test --to prod`, review the candidate changes in `envs/prod/deployment.yml`, approve the production config and deployment, then use the merged config commit with `deploy --environment prod --components orders`. The same Nexus digest carries into production.
5. **Promote production to DR and deploy as scheduled.** Use `--environment prod --to dr`. Review and merge the DR baseline update. When maintaining or rebuilding the DR application, use `--environment dr` with the approved DR commit. DR keeps its own configuration and secret values.

The command accepts only adjacent transitions. To promote multiple components, supply their names or use `all`. A healthy billing release alone cannot justify promoting orders. The gate checks every expected host for every selected source component. An unreachable host, an interrupted activation, failed health, or a release ID mismatch blocks the candidate. With GitLab CI, download the candidate from `reports/candidate/envs/<target>/deployment.yml` and the source report from `reports/status.json`. The repository does not contain a bot that opens or merges the merge request for you.

### Add a component

Add the vendor's component to the approved bundle and root manifest. Add its host inventory group, service account, complete baseline entry, and position in `deployment_order` in each relevant environment. Add its configuration mappings and bind its required secret variables. A target baseline needs that entry before a promotion candidate can carry the artifact pin into it. Adding another component is a change to inventory and configuration data when it follows the same vendor hook and packaging contract.

## Roll back or rebuild

### Automatic rollback and recovery after interruption

1. The host writes `.activation.json` before stopping the old instance. It stores the previous release ID, or null on a clean host.
2. If activation fails, the helper stops the instance selected by `current`, restores the previous link, and starts and verifies the previous release. For a first install there is no previous release to start, so recovery removes `current` after stopping the failed instance.
3. A successfully recovered deployment still returns a failure for the requested update. Earlier successful hosts or components can remain on the newer release. Use the per-host report to determine what changed.
4. If recovery fails or the operation is interrupted after writing the journal, the journal remains. The next activation for that host and component attempts recovery before proceeding. Correct the underlying hook, permissions, or host issue when recovery cannot complete.

To roll back several hosts or components, use an approved configuration commit with the previous artifact and configuration, retain its Nexus bundles and secret versions, and deploy the affected selection. A reviewed revert in the config project can update the desired baseline while preserving unrelated changes that occurred after the original deployment. A symlink rollback restores application files and hook-selected runtime. Database changes and shared state require their own recovery plan.

### Rebuild on clean hosts

1. Choose the complete approved baseline commit for the target environment. For an exact recreation of a previous state, confirm the commit reflects the desired set of component pins and compare it with the available last-known status reports.
2. Recreate hosts, inventory, service accounts, SSH and sudo access, Java runtime, and connectivity. Restore external databases and persistent data where required. Ensure Nexus and the config repository are available in recovery, along with the matching environment's secret versions.
3. From the deployment project, run the full deployment against the target inventory:

   ```bash
   python tools/deploy.py deploy --config-repo "$CONFIG_REPO" \
     --config-ref "$CONFIG_SHA" --environment dr --components all \
     --report reports/dr-rebuild.json
   ```

4. Check every host row in the report for `healthy` and `matches_baseline: true`. Where runtime version identity matters, confirm the vendor provides `bin/version` and that `running_version` matches the intended build.

The controller pulls every pinned bundle it needs, even if each component was last updated on a different date. The delivered code installs and activates the application. Host provisioning, load balancer draining, database migrations and restores, and application data recovery are separate responsibilities.

## Reference for every repository file

This reference explains how the files are used. The appendix links each exact path to its verbatim source. The two archive trees contain 50 Ansible repository files and 30 config repository files.

### Deployment repository root

| Path under `myapp-ansible/` | Contents and role in the flow |
| --- | --- |
| `.gitignore` | Ignores the local virtual environment, CI config clone, reports, and Python bytecode directories. It keeps generated workspace material out of version control. |
| `.gitlab-ci.yml` | Defines `check` and `operate` stages. The pre-provisioned shell runner uses its local Python to create a venv and installs the approved wheel archive from Nexus. The `check` job compiles scripts and runs the local integration test. The shared operation setup retrieves configuration and installs SSH credentials. Three protected manual operation jobs invoke the CLI for deploy, status, or a promotion candidate. Resource groups and artifact retention are defined here. |
| `README.md` | Operational quick start. Covers setup, the bundle contract, deploy/status/rebuild, promotion, GitLab and Jenkins usage, and local verification. It is read by operators and has no runtime consumer. |
| `ansible.cfg` | Enables SSH host-key checking, disables Ansible retry files, and uses silent Python interpreter discovery for modules. The playbook script tasks separately name `/usr/bin/python3` for the host helper. |
| `requirements.txt` | Installs Ansible Core 2.19 for orchestration, Jinja2 for template rendering, PyYAML for YAML documents, and javaproperties for Java-compatible escaping and parsing. The pre-provisioned shell runner and local venv use Python 3.12. The bootstrap validates these ranges against the approved Nexus wheel set. |

### Environment inventories

| Path under `myapp-ansible/` | Contents and role in the flow |
| --- | --- |
| `inventories/dev/hosts.yml` | Defines example target `dev-app-01` at `dev-app-01.example.invalid`. Used by dev deploy/status and the live source check for dev → test. |
| `inventories/test/hosts.yml` | Defines example target `test-app-01` at `test-app-01.example.invalid`. Used by test deploy/status and the live source check for test → prod. |
| `inventories/prod/hosts.yml` | Defines example target `prod-app-01` at `prod-app-01.example.invalid`. Used by production deploy/status and the live source check for prod → DR. |
| `inventories/dr/hosts.yml` | Defines example target `dr-app-01` at `dr-app-01.example.invalid`. Used by DR deploy, status, and rebuild. |

Each inventory uses `all.vars` for the app root `/opt/myapp`, privilege escalation through `deploy_become: true`, and the SSH user `ansible`. The example host is a member of both `orders_nodes` and `billing_nodes`. The per-component baseline chooses exactly one group through its `group` field. Add or separate hosts within that group to represent your topology.

### Playbooks

| Path under `myapp-ansible/` | Contents and role in the flow |
| --- | --- |
| `playbooks/stage.yml` | Targets the planned group and creates a private transfer directory. It copies the payload with mode `0600`, runs the host helper's `stage` action, and attempts cleanup in an `always` block. Copy output and diffs are suppressed for the payload. A failed staging play prevents the controller from reaching activation. |
| `playbooks/activate.yml` | Targets the planned group with `serial` equal to the configured batch size and `any_errors_fatal: true`. It runs the host helper's `activate` action and reports its changed flag. The helper owns the stop/switch/start/health/rollback sequence. |
| `playbooks/status.yml` | Runs the host helper's read and health operation. It saves one JSON result on the controller through a delegated local copy. The filename is a SHA-256 of the inventory hostname, and the file mode is `0600`. Results are combined by the controller rather than left on the remote host. |
| `playbooks/vars/host.yml` | Builds the shared helper command arguments from inventory and plan variables. It passes the app root, component name, service account, hook timeout, health attempts, and retry delay, with shell quoting for the path and names. All three plays use it. |

### Deployment scripts

| Path under `myapp-ansible/` | Contents and role in the flow |
| --- | --- |
| `tools/ci_checkout.sh` | Validates the full config SHA, GitLab project path, and HTTPS server URL. It passes the job token as a scoped Git HTTPS header for the clone invocation. It clones into `.config-repo` and uses `git merge-base --is-ancestor` to enforce ancestry from the default branch. CI calls it before invoking the Python controller. |
| `tools/deploy.py` | The runner-side entry point. Reads CLI arguments, snapshots Git, loads and validates baselines, calculates identities, obtains and verifies Nexus bundles, renders and validates configuration, runs Ansible, collects reports, and writes promotion candidates. |
| `tools/host.py` | The Linux-side entry point. Manages staged directories, permissions and locks, switches active releases, runs hooks, recovers from failure, and reports what is active. It is delivered to hosts inside the generated zipapp. |
| `tools/archive.py` | The shared path and extraction logic. The controller uses it for the Git snapshot and vendor tarball. The host helper uses it for the component payload. It checks relative path segments and archive member types, streams regular files into a fresh directory, and applies modes and optional group ownership. |

#### Controller functions in `tools/deploy.py`

| Code | Use in the flow |
| --- | --- |
| `require`, `positive`, `mapping`, `load` | Validate conditions, numeric settings, mapping shape, and safely parsed YAML. |
| `digest`, `identity`, `sha256_file` | Compute SHA-256 for file contents, identifiers derived from deterministic JSON, and entire bundle files. |
| `run`, `snapshot` | Execute commands from the Ansible project, resolve a config ref to a commit, and extract the commit's Git archive into the private workspace. |
| `read_baseline`, `choose` | Read the requested environment file, validate names, version fields, groups, account name, and rollout settings, and return the chosen components in declared order. |
| `configuration` | Check environment-local config paths, read mappings and values, validate secret reference names, and return the file sources, values, references, and fingerprint. It does not resolve secret values. |
| `obtain_bundle` | Use the configured direct Nexus HTTPS authority and shared transport, apply optional Basic credentials, verify the expected checksum, and extract once per artifact digest for an invocation. `--bundle-dir` substitutes a local artifact source and still requires the checksum. |
| `stage_bundle` | Select and copy one component from the extracted vendor tar, check the vendor's manifest versions and hooks, resolve secret values, overlay mapped config, validate required Java properties, write the receipt, and make the transport tar. |
| `inventory`, `hosts`, `make_plan` | Enumerate Ansible groups and their child hosts, determine target membership and rollout settings, and compute one expected release ID per selected component. |
| `build_host_tool`, `play` | Make a Python zipapp containing the host and extraction code, serialize play inputs into the controller's temporary workspace, and invoke `ansible-playbook` with the correct inventory. |
| `collect_status` | Run the status play for selected components, produce a row for every expected host, mark missing results as `unknown`, write JSON, print a status table, and indicate whether every row is healthy and matches. |
| `promote` | Load the target baseline, replace the selected artifact mappings, validate their target configuration mappings, and write the candidate YAML. The live source check is performed before this function is called. |
| `main` | Parse operation arguments, enforce adjacent promotions, create the temporary workspace, plan the request, and dispatch. Deployment uses separate loops so controller preparation completes for all selected components before remote staging, and remote staging completes before any activation. |

#### Host functions in `tools/host.py`

| Code | Use in the flow |
| --- | --- |
| `write_json` | Writes the transaction journal to a temporary file, flushes and syncs file contents, then replaces the journal path. |
| `Component.__init__`, `lock` | Resolve component paths and service account, then serialize per-host operations with `flock` on `.lock`. |
| `stage` | Set up release and shared directories, unpack into temporary storage, verify the receipt identifies the requested component and release, and finalize the directory by renaming it. It returns unchanged when the requested staged release already exists with its expected ID. |
| `receipt`, `active`, `point_to` | Read release metadata, resolve the managed `current` symlink, and atomically replace or remove the link. A legacy directory named `current` or an unexpected symlink target causes an error. |
| `hook`, `check` | Execute vendor hooks under the service account with a clean environment, enforce the timeout, retry health during activation, and compare the optional running build with the receipt. |
| `recover`, `activate` | Recover any interrupted journal before switching or restarting a release. On failure, try to restore and verify the recorded previous release. |
| `status` | Read the current release and return its receipt plus health and running-version observations. A journal still on disk takes precedence in the reported state. |
| `main` | Parse helper arguments, validate a release ID when required, dispatch stage/activate/status, and return JSON or an error to Ansible. |

### Environment configuration repository

| Path under `myapp-config/` | Contents and role in the flow |
| --- | --- |
| `.gitignore` | Ignores `*.local`, `*.secret`, and `.env` files so common local or sensitive files stay outside a normal add. Keep real secrets in the approved secret store. |
| `README.md` | Describes baseline ownership, file mappings, secret revision updates, and the release review process. It is for application support and release reviewers. |
| `envs/dev/deployment.yml` | Demo dev baseline. Orders `3.8.2` comes from `2026.11.23.1`. Billing `2.13.4` comes from `2026.11.10.2`. Both map to dev-specific files. |
| `envs/test/deployment.yml` | Demo test baseline. Orders `3.8.1` and billing `2.13.4` both start on `2026.11.10.2`. A dev promotion candidate changes the selected test artifact pins here. |
| `envs/prod/deployment.yml` | Demo production baseline with the older bundle for both components. Carries the approved production artifact selections and references production configuration paths. |
| `envs/dr/deployment.yml` | Demo recovery baseline with the older bundle for both components. Carries the approved recovery artifact selections and references DR-specific files. |

All four baselines define `deployment_order: [orders, billing]`. The order preserves a predictable sequence when deploying both or rebuilding. Each entry shares this schema:

| Key | Contents and effect |
| --- | --- |
| `environment` | Must match the chosen directory and CLI environment. |
| `deployment_order` | Lists each component exactly once. Selection filters this list without reordering it. |
| `components.<name>.group` | Ansible host group for this component. The demonstration uses `orders_nodes` and `billing_nodes`. |
| `service_user` | Unprivileged account for running hooks and the primary group for staged files. The examples use `myapp`. |
| `serial` | Hosts in an activation batch. The examples use `1`. |
| `hook_timeout` | Maximum seconds for each hook invocation. The examples use `30`. |
| `health_retries` and `health_delay` | Maximum attempts on start verification and seconds between failed attempts. The examples use `10` and `3`. A separate status check uses one attempt. |
| `artifact.path` | Repository-relative path to the original tarball. |
| `artifact.sha256` | Exact expected digest of the tarball bytes. |
| `artifact.bundle_version` and `artifact.component_version` | Expected string versions validated against the root vendor manifest. |
| `config.values` | Repository-relative path to the values and secret references for this component and environment. |
| `config.files[].source`, `destination`, `action` | Source in the selected Git snapshot, destination inside the copied component, and whether to render or copy it. |

Each of the following rows identifies the three actual files for that component and environment. Every template renders `config/application.properties`. Every logging XML maps to `config/logback.xml` and contains a console appender with a timestamp, level, logger, and message pattern and an INFO root logger. Each `values.yml` uses `"5000"` and `secret_revision: "1"` in the demo. The database and secret binding differ as shown.

| Values file | Properties template | Logging file | Environment-specific effect |
| --- | --- | --- | --- |
| `envs/dev/files/orders/values.yml` | `envs/dev/files/orders/application.properties.j2` | `envs/dev/files/orders/logback.xml` | Dev orders reads `dev-db.example.invalid:5432/orders` and the dev value for `MYAPP_SECRET_ORDERS_DB_PASSWORD`. |
| `envs/dev/files/billing/values.yml` | `envs/dev/files/billing/application.properties.j2` | `envs/dev/files/billing/logback.xml` | Dev billing reads `dev-db.example.invalid:5432/billing` and the dev value for `MYAPP_SECRET_BILLING_DB_PASSWORD`. |
| `envs/test/files/orders/values.yml` | `envs/test/files/orders/application.properties.j2` | `envs/test/files/orders/logback.xml` | Test orders reads `test-db.example.invalid:5432/orders` and the test value for `MYAPP_SECRET_ORDERS_DB_PASSWORD`. |
| `envs/test/files/billing/values.yml` | `envs/test/files/billing/application.properties.j2` | `envs/test/files/billing/logback.xml` | Test billing reads `test-db.example.invalid:5432/billing` and the test value for `MYAPP_SECRET_BILLING_DB_PASSWORD`. |
| `envs/prod/files/orders/values.yml` | `envs/prod/files/orders/application.properties.j2` | `envs/prod/files/orders/logback.xml` | Production orders reads `prod-db.example.invalid:5432/orders` and the production value for `MYAPP_SECRET_ORDERS_DB_PASSWORD`. |
| `envs/prod/files/billing/values.yml` | `envs/prod/files/billing/application.properties.j2` | `envs/prod/files/billing/logback.xml` | Production billing reads `prod-db.example.invalid:5432/billing` and the production value for `MYAPP_SECRET_BILLING_DB_PASSWORD`. |
| `envs/dr/files/orders/values.yml` | `envs/dr/files/orders/application.properties.j2` | `envs/dr/files/orders/logback.xml` | DR orders reads `dr-db.example.invalid:5432/orders` and the DR value for `MYAPP_SECRET_ORDERS_DB_PASSWORD`. |
| `envs/dr/files/billing/values.yml` | `envs/dr/files/billing/application.properties.j2` | `envs/dr/files/billing/logback.xml` | DR billing reads `dr-db.example.invalid:5432/billing` and the DR value for `MYAPP_SECRET_BILLING_DB_PASSWORD`. |

### Offline bootstrap `tools/bootstrap_python.py`

Run in a fresh Python 3.12 virtual environment with bundled pip available. The module uses the standard library and the adjacent `archive.py` and `network.py`, so it can run before installing Ansible or any third-party libraries.

| Function | Purpose and inputs |
| --- | --- |
| `read_requirements(source, destination)` | Reads the repository's requirements, accepts only package names with simple version constraints, and writes a sanitized temporary requirements file. Direct references, pip directives, included files, and indexes are rejected. |
| `pip(arguments)` | Calls `sys.executable -I -m pip` with caching/version checks disabled, strips inherited `PIP_*` settings, and disables pip configuration-file loading. |
| `install(wheelhouse, requirements)` | Requires a flat local wheel directory, installs explicit wheel paths without indexes or dependency fetching, checks the approved requirement ranges against installed distributions, and runs `pip check`. |
| `main()` | Requires a venv and an approved archive checksum. Downloads the wheel archive from the configured Nexus authority, verifies its bytes, extracts it to a private directory, installs dependencies, and exits nonzero on failure. |

### Internal HTTPS transport `tools/network.py`

| Function or class | Purpose and inputs |
| --- | --- |
| `approved_https(url, authority, label)` | Parses the URL, requires the exact configured internal hostname and optional port, and rejects TLS downgrade, malformed URL details and embedded credentials. The GitLab checkout uses the same gate. |
| `NoRedirect.redirect_request(req, fp, code, msg, headers, newurl)` | Refuses automatic redirects from urllib. This keeps downloads on the explicitly configured Nexus authority. |
| `download_nexus(url, destination, credential_prefix)` | Validates the configured Nexus authority, reads optional paired Basic credentials for either the wheel or vendor repo, disables proxy discovery, and copies the response to a new local file. The caller verifies its expected SHA-256. |

### Offline guard tests `tests/test_offline.py`

These standard-library tests use mocked transports and pip calls. They make no real network requests.

| Function | What it verifies |
| --- | --- |
| `OfflineTests.test_requires_approved_https_authority()` | Rejects an unapproved hostname, plain HTTP, embedded credentials, query/fragment and malformed paths. |
| `OfflineTests.test_download_ignores_proxies_and_refuses_redirects()` | Checks that the download installs an empty proxy handler and disables redirect handling. |
| `OfflineTests.test_requirements_disallow_links_and_pip_directives()` | Accepts the expected constraint form and rejects direct links, additional indexes, and included requirement files. |
| `OfflineTests.test_pip_clears_inherited_configuration()` | Asserts inherited pip variables and config files cannot change the install policy, and verifies offline pip flags. |
| `OfflineTests.test_wheel_install_uses_only_explicit_local_files()` | Verifies exact local wheel installation arguments, the post-install dependency check, and rejection of other archive entries. |

### Integration fixtures in the deployment repository

| Path under `myapp-ansible/` | Contents and role in the flow |
| --- | --- |
| `tests/make_bundles.py` | Generates two full demo bundles plus a bundle whose orders health hook fails and a bundle that requires an undeclared property. Each has a root manifest, per-component build marker, placeholder properties, and executable shell hooks. These hooks write, remove, or read a local running-version marker to simulate a service. |
| `tests/integration.sh` | Creates a temporary Git config repository and local Ansible inventories, generates bundles, sets demo secret values, runs the deployment and promotion scenarios, verifies assertions, and cleans up the test workspace. It is called by the GitLab `check` job. |
| `tests/example/envs/dev/deployment.yml` | Byte-identical copy of the companion dev baseline. Gives the integration scenario a newer orders build to promote. |
| `tests/example/envs/test/deployment.yml` | Byte-identical copy of the companion test baseline. Supplies the initial test state before the selective deployment. |
| `tests/example/envs/prod/deployment.yml` | Byte-identical copy of the companion prod baseline. Supplies production settings for the simulated promotion and clean installation. |
| `tests/example/envs/dr/deployment.yml` | Byte-identical copy of the companion DR baseline. Supplies recovery settings for the final simulated promotion and clean installation. |

For each of the eight component/environment combinations in the config table, the Ansible archive ships a byte-identical copy of its three files at `tests/example/` plus the table path. For example, `envs/test/files/orders/values.yml` in the config project corresponds to `tests/example/envs/test/files/orders/values.yml` in Ansible. All 24 fixture file paths are individually listed in the appendix. Their role is to let GitLab exercise rendering and promotion with example data even when the Ansible project is checked out by itself. Validating real environments uses the separate config project's approved commit.

### Command-line arguments

| Argument | Effect |
| --- | --- |
| `deploy` | Build the chosen payloads, run staging and activation, then collect status. |
| `status` | Plan the expected release IDs and query the selected hosts. |
| `promote` | Query the source hosts first, then write a candidate if every selected row is healthy and matches. |
| `validate` | Validate the baseline, selection, config mappings, and inventory membership. This action does not fetch bundles, resolve secrets, or connect to hosts. |
| `--config-repo` | Required path to the Git repo containing the environment files. |
| `--config-ref` | Config ref to resolve to a commit. Defaults to `HEAD` for the local CLI. Use the approved commit explicitly for operations. |
| `--environment` | Required target for deploy, validate, or status. Selects the source for promotion. |
| `--components` | Defaults to `all`. Accepts a unique comma-separated selection of baseline names. |
| `--inventory` | Overrides the matching environment's shipped deployment inventory. Used by the local test harness. |
| `--bundle-dir` | Reads bundles from a local directory using the artifact mapping paths and checksum verification. Used for fixtures or an approved offline copy. |
| `--report` | JSON output path, defaulting to `reports/status.json`. |
| `--to`, `--output` | Required together for promotion. Define the adjacent target environment and candidate YAML file. |

## Tests and troubleshooting

Run the same integration test as CI with controller dependencies activated:

```bash
cd myapp-ansible
python tests/test_offline.py
bash tests/integration.sh
```

The test runs Ansible against local targets and uses fixture bundles with shell hooks. It validates the four baselines, installs dev and test from scratch, promotes orders selectively while preserving billing and test configuration, checks rendered Java properties and file mode, repeats the deployment, and walks through production and DR installs. It then verifies that a failing health check restores the previous build, that a missing required property prevents activation, and that drift blocks a promotion candidate. Setting `MYAPP_CONFIG_EXAMPLE` to an absolute path to the companion project exercises that project's sample files directly.

| Symptom | Where it stops | What to inspect |
| --- | --- | --- |
| Missing group or empty target list | Planning | Match the baseline's component `group` to the environment inventory and add the intended hosts. |
| Failed config checkout in CI | Runner setup | Confirm the commit is present on the config default branch, the project path is correct, and the config project allows the deployment project's job token. |
| Nexus authentication, HTTP, or checksum failure | Bundle preparation | Check the direct HTTPS repository URL, certificate trust, credentials, artifact path, and expected digest. |
| Bundle version, component version, or hook error | Bundle preparation | Compare the vendor manifest and hook executable bits with the approved baseline. |
| Missing secret, failed template, or missing required key | Bundle preparation | Check the environment-scoped CI binding, mapped template, and vendor contract. A healthy currently deployed version can remain active while that error is fixed. |
| Ansible staging fails on one host | Remote staging | Check disk space, Python, SSH and sudo access. Some hosts can have a staged directory, and the controller has yet to start activation. |
| Activation fails and `Rollback completed` appears | Remote activation | Check the application logs, selected versions and hook behavior. The previous release was restored on that component and host when one existed. |
| `Rollback failed` or `activation_interrupted` | Recovery | Inspect the hook failure and journal. Repair the underlying problem so the next deploy can complete recovery. |
| Healthy hosts with baseline drift | Status or promotion gate | Compare the actual receipt with the configured artifact and config fingerprint. Deploy the approved state or reconcile the baseline by review. |

### Generated and external files

The two archives contain repository sources. The following files are produced or required when the workflow runs:

| File or directory | Created or supplied by | Role |
| --- | --- | --- |
| Vendor `.tar` or `.tar.gz` and root `manifest.yml` | Vendor and Nexus | Provides the original component files and the required settings contract. The test harness produces fixture versions locally. |
| `.config-repo/` | `tools/ci_checkout.sh` | Git checkout used as the source of the approved snapshot. |
| Temporary `config.tar`, config snapshot, downloaded bundles, and extracted payloads | `tools/deploy.py` | Working files for a single invocation, kept in the controller's temporary directory. |
| Temporary `myapp-host.pyz`, `ansible-input.json`, and `payload-<component>.tar` | `tools/deploy.py` | Host helper, Ansible variables, and component transfer archive. |
| Remote private `payload.tar` | `playbooks/stage.yml` | Temporary upload used to stage a release and then cleaned up by the play when possible. |
| `~/.ssh/id_ed25519` and `~/.ssh/known_hosts` on the CI runner | GitLab operation job from file variables | Credentials and trusted host keys used by Ansible SSH. |
| `current`, `releases/`, `shared/`, `.lock`, `.activation.json`, `.deployment.json` | Host helper and staged payload | Active release selection, installed copies, writable service state, host lock, recovery journal, and release receipt. |
| `reports/status.json` or the explicit report path | `tools/deploy.py` | Live inventory-wide report for the request. |
| `reports/candidate/envs/<target>/deployment.yml` in CI | Promotion command | Proposed target baseline. Reviewed and merged through the configuration project. |

## Complete file contents

Use the index to jump to the exact contents of any archive file. The listings reproduce the shipped bytes as text. Identical files share a listing, which names all corresponding paths. The two archives contain 38 distinct file contents.

| Archive | Files | SHA-256 |
| --- | ---: | --- |
| `myapp-ansible.tar.gz` | 50 | `6c076533a1c82b5c2bf88407fd06ca8ee52a5f172829da9c85041674cb41678d` |
| `myapp-config.tar.gz` | 30 | `1c89290a9c4bdd7ba0522ea6b95ff163bc57ee21bed4458e75342ad285a3ef92` |

### Index for `myapp-ansible/`

| Relative file path | Exact contents |
| --- | --- |
| `.gitignore` | [Listing 01](#source-01) |
| `.gitlab-ci.yml` | [Listing 02](#source-02) |
| `README.md` | [Listing 03](#source-03) |
| `ansible.cfg` | [Listing 04](#source-04) |
| `inventories/dev/hosts.yml` | [Listing 05](#source-05) |
| `inventories/dr/hosts.yml` | [Listing 06](#source-06) |
| `inventories/prod/hosts.yml` | [Listing 07](#source-07) |
| `inventories/test/hosts.yml` | [Listing 08](#source-08) |
| `playbooks/activate.yml` | [Listing 09](#source-09) |
| `playbooks/stage.yml` | [Listing 10](#source-10) |
| `playbooks/status.yml` | [Listing 11](#source-11) |
| `playbooks/vars/host.yml` | [Listing 12](#source-12) |
| `requirements.txt` | [Listing 13](#source-13) |
| `tests/example/envs/dev/deployment.yml` | [Listing 25](#source-25) |
| `tests/example/envs/dev/files/billing/application.properties.j2` | [Listing 26](#source-26) |
| `tests/example/envs/dev/files/billing/logback.xml` | [Listing 27](#source-27) |
| `tests/example/envs/dev/files/billing/values.yml` | [Listing 28](#source-28) |
| `tests/example/envs/dev/files/orders/application.properties.j2` | [Listing 26](#source-26) |
| `tests/example/envs/dev/files/orders/logback.xml` | [Listing 27](#source-27) |
| `tests/example/envs/dev/files/orders/values.yml` | [Listing 29](#source-29) |
| `tests/example/envs/dr/deployment.yml` | [Listing 30](#source-30) |
| `tests/example/envs/dr/files/billing/application.properties.j2` | [Listing 26](#source-26) |
| `tests/example/envs/dr/files/billing/logback.xml` | [Listing 27](#source-27) |
| `tests/example/envs/dr/files/billing/values.yml` | [Listing 31](#source-31) |
| `tests/example/envs/dr/files/orders/application.properties.j2` | [Listing 26](#source-26) |
| `tests/example/envs/dr/files/orders/logback.xml` | [Listing 27](#source-27) |
| `tests/example/envs/dr/files/orders/values.yml` | [Listing 32](#source-32) |
| `tests/example/envs/prod/deployment.yml` | [Listing 33](#source-33) |
| `tests/example/envs/prod/files/billing/application.properties.j2` | [Listing 26](#source-26) |
| `tests/example/envs/prod/files/billing/logback.xml` | [Listing 27](#source-27) |
| `tests/example/envs/prod/files/billing/values.yml` | [Listing 34](#source-34) |
| `tests/example/envs/prod/files/orders/application.properties.j2` | [Listing 26](#source-26) |
| `tests/example/envs/prod/files/orders/logback.xml` | [Listing 27](#source-27) |
| `tests/example/envs/prod/files/orders/values.yml` | [Listing 35](#source-35) |
| `tests/example/envs/test/deployment.yml` | [Listing 36](#source-36) |
| `tests/example/envs/test/files/billing/application.properties.j2` | [Listing 26](#source-26) |
| `tests/example/envs/test/files/billing/logback.xml` | [Listing 27](#source-27) |
| `tests/example/envs/test/files/billing/values.yml` | [Listing 37](#source-37) |
| `tests/example/envs/test/files/orders/application.properties.j2` | [Listing 26](#source-26) |
| `tests/example/envs/test/files/orders/logback.xml` | [Listing 27](#source-27) |
| `tests/example/envs/test/files/orders/values.yml` | [Listing 38](#source-38) |
| `tests/integration.sh` | [Listing 14](#source-14) |
| `tests/make_bundles.py` | [Listing 15](#source-15) |
| `tests/test_offline.py` | [Listing 16](#source-16) |
| `tools/archive.py` | [Listing 17](#source-17) |
| `tools/bootstrap_python.py` | [Listing 18](#source-18) |
| `tools/ci_checkout.sh` | [Listing 19](#source-19) |
| `tools/deploy.py` | [Listing 20](#source-20) |
| `tools/host.py` | [Listing 21](#source-21) |
| `tools/network.py` | [Listing 22](#source-22) |

### Index for `myapp-config/`

| Relative file path | Exact contents |
| --- | --- |
| `.gitignore` | [Listing 23](#source-23) |
| `README.md` | [Listing 24](#source-24) |
| `envs/dev/deployment.yml` | [Listing 25](#source-25) |
| `envs/dev/files/billing/application.properties.j2` | [Listing 26](#source-26) |
| `envs/dev/files/billing/logback.xml` | [Listing 27](#source-27) |
| `envs/dev/files/billing/values.yml` | [Listing 28](#source-28) |
| `envs/dev/files/orders/application.properties.j2` | [Listing 26](#source-26) |
| `envs/dev/files/orders/logback.xml` | [Listing 27](#source-27) |
| `envs/dev/files/orders/values.yml` | [Listing 29](#source-29) |
| `envs/dr/deployment.yml` | [Listing 30](#source-30) |
| `envs/dr/files/billing/application.properties.j2` | [Listing 26](#source-26) |
| `envs/dr/files/billing/logback.xml` | [Listing 27](#source-27) |
| `envs/dr/files/billing/values.yml` | [Listing 31](#source-31) |
| `envs/dr/files/orders/application.properties.j2` | [Listing 26](#source-26) |
| `envs/dr/files/orders/logback.xml` | [Listing 27](#source-27) |
| `envs/dr/files/orders/values.yml` | [Listing 32](#source-32) |
| `envs/prod/deployment.yml` | [Listing 33](#source-33) |
| `envs/prod/files/billing/application.properties.j2` | [Listing 26](#source-26) |
| `envs/prod/files/billing/logback.xml` | [Listing 27](#source-27) |
| `envs/prod/files/billing/values.yml` | [Listing 34](#source-34) |
| `envs/prod/files/orders/application.properties.j2` | [Listing 26](#source-26) |
| `envs/prod/files/orders/logback.xml` | [Listing 27](#source-27) |
| `envs/prod/files/orders/values.yml` | [Listing 35](#source-35) |
| `envs/test/deployment.yml` | [Listing 36](#source-36) |
| `envs/test/files/billing/application.properties.j2` | [Listing 26](#source-26) |
| `envs/test/files/billing/logback.xml` | [Listing 27](#source-27) |
| `envs/test/files/billing/values.yml` | [Listing 37](#source-37) |
| `envs/test/files/orders/application.properties.j2` | [Listing 26](#source-26) |
| `envs/test/files/orders/logback.xml` | [Listing 27](#source-27) |
| `envs/test/files/orders/values.yml` | [Listing 38](#source-38) |

### Verbatim listings

#### Source 01

`myapp-ansible/.gitignore`

```text
.venv/
.config-repo/
reports/
__pycache__/
```

#### Source 02

`myapp-ansible/.gitlab-ci.yml`

```yaml
stages: [check, operate]

default:
  tags: [myapp-offline]
  before_script:
    - python3.12 -m venv --clear .venv
    - source .venv/bin/activate
    - python tools/bootstrap_python.py

variables:
  GIT_SUBMODULE_STRATEGY: none
  GIT_LFS_SKIP_SMUDGE: "1"
  OPERATION:
    value: "none"
    options: ["none", deploy, status, promote]
    description: Operation to enable on a protected branch pipeline
  DEPLOY_ENV:
    value: dev
    options: [dev, test, prod, dr]
    description: Environment to deploy or query, or the source environment for promotion
  TARGET_ENV:
    value: test
    options: [test, prod, dr]
    description: Target environment for the promotion candidate
  COMPONENTS:
    value: all
    description: all or a comma-separated list such as orders,billing
  CONFIG_REF:
    value: ""
    description: Full commit SHA from the configuration repository's default branch
  CONFIG_PROJECT_PATH:
    value: ""
    description: GitLab namespace/project for the configuration repository

check:
  stage: check
  script:
    - python -m compileall -q tools tests
    - python tests/test_offline.py
    - bash tests/integration.sh

.operation:
  stage: operate
  before_script:
    - !reference [default, before_script]
    - test -n "$CONFIG_REF" && test -n "$CONFIG_PROJECT_PATH"
    - bash tools/ci_checkout.sh
    - install -m 0700 -d ~/.ssh
    - install -m 0600 "$SSH_PRIVATE_KEY" ~/.ssh/id_ed25519
    - install -m 0600 "$SSH_KNOWN_HOSTS" ~/.ssh/known_hosts
  resource_group: "myapp-$DEPLOY_ENV"
  artifacts:
    when: always
    paths:
      - reports/
    expire_in: 30 days

deploy:
  extends: .operation
  rules:
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && $CI_COMMIT_REF_PROTECTED == "true" && $CI_PIPELINE_SOURCE == "web" && $OPERATION == "deploy"'
      when: manual
  environment:
    name: $DEPLOY_ENV
    action: start
  script:
    - python tools/deploy.py deploy --config-repo .config-repo --config-ref "$CONFIG_REF" --environment "$DEPLOY_ENV" --components "$COMPONENTS"

status:
  extends: .operation
  rules:
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && $CI_COMMIT_REF_PROTECTED == "true" && $CI_PIPELINE_SOURCE == "web" && $OPERATION == "status"'
      when: manual
  environment:
    name: $DEPLOY_ENV
    action: verify
  script:
    - python tools/deploy.py status --config-repo .config-repo --config-ref "$CONFIG_REF" --environment "$DEPLOY_ENV" --components "$COMPONENTS"

prepare-promotion:
  extends: .operation
  rules:
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && $CI_COMMIT_REF_PROTECTED == "true" && $CI_PIPELINE_SOURCE == "web" && $OPERATION == "promote"'
      when: manual
  environment:
    name: $DEPLOY_ENV
    action: verify
  script:
    - >-
      python tools/deploy.py promote --config-repo .config-repo --config-ref "$CONFIG_REF"
      --environment "$DEPLOY_ENV" --to "$TARGET_ENV" --components "$COMPONENTS"
      --output "reports/candidate/envs/$TARGET_ENV/deployment.yml"
```

#### Source 03

`myapp-ansible/README.md`

````markdown
# MyApp deployment

This repository runs Ansible. The companion `myapp-config` repository owns the four environment baselines and their configuration. Keep them as separate GitLab projects. A baseline records exactly which bundle and component version to install for every component. A full vendor bundle can be used to deploy one component, several components, or the entire environment.

## Set up

1. Import both archives into GitLab as separate repositories. Replace the example hostnames under `inventories/`, the component groups and service accounts in the baselines, and the example configuration and artifact coordinates. Initialize a full known-good baseline for each environment.
2. Prepare Linux application hosts with Python 3.8 or later at `/usr/bin/python3`, the Java/runtime dependencies, `runuser` from util-linux, and the unprivileged component accounts. The Ansible SSH account needs sudo on the targets with the privileges required by the unarchive and lifecycle helper. The managed application root defaults to `/opt/myapp`. Plan a one-time migration if services already run from legacy directories.
3. Provision the controller and Nexus access using the offline setup below.
4. Retain every immutable bundle referenced by a baseline or rollback point.
5. Provide the secret environment variables referenced in the configuration repository through your CI secret store. Scope them to each environment so the same variable name can resolve to a different value in dev, test, prod and DR.

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
````

#### Source 04

`myapp-ansible/ansible.cfg`

```ini
[defaults]
host_key_checking = True
retry_files_enabled = False
interpreter_python = auto_silent
```

#### Source 05

`myapp-ansible/inventories/dev/hosts.yml`

```yaml
---
all:
  vars:
    deploy_root: /opt/myapp
    deploy_become: true
    ansible_user: ansible
  children:
    orders_nodes:
      hosts:
        dev-app-01:
          ansible_host: dev-app-01.example.invalid
    billing_nodes:
      hosts:
        dev-app-01:
```

#### Source 06

`myapp-ansible/inventories/dr/hosts.yml`

```yaml
---
all:
  vars:
    deploy_root: /opt/myapp
    deploy_become: true
    ansible_user: ansible
  children:
    orders_nodes:
      hosts:
        dr-app-01:
          ansible_host: dr-app-01.example.invalid
    billing_nodes:
      hosts:
        dr-app-01:
```

#### Source 07

`myapp-ansible/inventories/prod/hosts.yml`

```yaml
---
all:
  vars:
    deploy_root: /opt/myapp
    deploy_become: true
    ansible_user: ansible
  children:
    orders_nodes:
      hosts:
        prod-app-01:
          ansible_host: prod-app-01.example.invalid
    billing_nodes:
      hosts:
        prod-app-01:
```

#### Source 08

`myapp-ansible/inventories/test/hosts.yml`

```yaml
---
all:
  vars:
    deploy_root: /opt/myapp
    deploy_become: true
    ansible_user: ansible
  children:
    orders_nodes:
      hosts:
        test-app-01:
          ansible_host: test-app-01.example.invalid
    billing_nodes:
      hosts:
        test-app-01:
```

#### Source 09

`myapp-ansible/playbooks/activate.yml`

```yaml
---
- name: Activate the staged component in batches
  hosts: "{{ component_group }}"
  gather_facts: false
  become: "{{ deploy_become | default(true) | bool }}"
  serial: "{{ rollout_serial }}"
  any_errors_fatal: true
  vars_files:
    - vars/host.yml
  tasks:
    - name: Switch release and check health with automatic rollback
      ansible.builtin.script:
        cmd: >-
          {{ host_tool | quote }} activate {{ host_options }}
          --release {{ release_id | quote }}
        executable: /usr/bin/python3
      register: activation
      changed_when: activation.rc == 0 and (activation.stdout | from_json).changed
```

#### Source 10

`myapp-ansible/playbooks/stage.yml`

```yaml
---
- name: Stage component on every target before activation
  hosts: "{{ component_group }}"
  gather_facts: false
  become: "{{ deploy_become | default(true) | bool }}"
  any_errors_fatal: true
  vars_files:
    - vars/host.yml
  tasks:
    - name: Transfer and stage the component
      block:
        - name: Create a private transfer directory
          ansible.builtin.tempfile:
            state: directory
            prefix: myapp-transfer-
          register: transfer

        - name: Transfer the staged package
          no_log: true
          ansible.builtin.copy:
            src: "{{ component_archive }}"
            dest: "{{ transfer.path }}/payload.tar"
            mode: "0600"
          diff: false

        - name: Install the immutable release directory
          ansible.builtin.script:
            cmd: >-
              {{ host_tool | quote }} stage {{ host_options }}
              --release {{ release_id | quote }}
              --archive {{ (transfer.path + '/payload.tar') | quote }}
            executable: /usr/bin/python3
          register: stage_result
          changed_when: stage_result.rc == 0 and (stage_result.stdout | from_json).changed
      always:
        - name: Remove the transfer directory
          ansible.builtin.file:
            path: "{{ transfer.path }}"
            state: absent
          when: transfer.path is defined
```

#### Source 11

`myapp-ansible/playbooks/status.yml`

```yaml
---
- name: Report the active component on each target
  hosts: "{{ component_group }}"
  gather_facts: false
  become: "{{ deploy_become | default(true) | bool }}"
  vars_files:
    - vars/host.yml
  tasks:
    - name: Read the active receipt and check application health
      ansible.builtin.script:
        cmd: "{{ host_tool | quote }} status {{ host_options }}"
        executable: /usr/bin/python3
      register: status_result
      changed_when: false

    - name: Save the host result on the controller
      ansible.builtin.copy:
        content: "{{ status_result.stdout | from_json | to_json }}"
        dest: "{{ status_dir }}/{{ inventory_hostname | hash('sha256') }}.json"
        mode: "0600"
      delegate_to: localhost
      become: false
      vars:
        ansible_connection: local
        ansible_python_interpreter: "{{ ansible_playbook_python }}"
```

#### Source 12

`myapp-ansible/playbooks/vars/host.yml`

```yaml
---
host_options: >-
  --root {{ deploy_root | quote }}
  --component {{ component_name | quote }}
  --user {{ component_user | quote }}
  --timeout {{ hook_timeout }}
  --retries {{ health_retries }}
  --delay {{ health_delay }}
```

#### Source 13

`myapp-ansible/requirements.txt`

```text
ansible-core>=2.19,<2.20
Jinja2>=3.1,<4
PyYAML>=6,<7
javaproperties>=0.8,<1
```

#### Source 14

`myapp-ansible/tests/integration.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

temp=$(mktemp -d)
export ANSIBLE_HOME="$temp/ansible-home"
export ANSIBLE_LOCAL_TEMP="$temp/ansible-local"
export ANSIBLE_REMOTE_TEMP="$temp/ansible-remote"
trap 'rm -rf "$temp"' EXIT
config="$temp/config"
fixtures="$temp/bundles"
unpack_fixture="${MYAPP_CONFIG_EXAMPLE:-tests/example}"
cp -R "$unpack_fixture" "$config"
rm -rf "$config/.git"
git -C "$config" init -q
git -C "$config" config user.name Test
git -C "$config" config user.email test@example.invalid
python tests/make_bundles.py "$fixtures"
export MYAPP_SECRET_ORDERS_DB_PASSWORD='demo: tricky = value'
export MYAPP_SECRET_BILLING_DB_PASSWORD='demo password'

for environment in dev test prod dr; do
  cat > "$temp/$environment.yml" <<YAML
all:
  vars:
    ansible_connection: local
    deploy_become: false
    deploy_root: "$temp/targets/$environment"
  children:
    orders_nodes:
      hosts:
        local-app:
    billing_nodes:
      hosts:
        local-app:
YAML
  python - "$config/envs/$environment/deployment.yml" "$fixtures" "$(id -un)" <<'PY'
import hashlib
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text())
for entry in data["components"].values():
    # Allow the test itself to be run as root in a CI container. The hook user
    # is replaced with nobody below when the controller is root.
    entry["service_user"] = sys.argv[3] if sys.argv[3] != "root" else "nobody"
    entry["health_retries"] = 2
    entry["health_delay"] = 0
    archive = Path(sys.argv[2]) / entry["artifact"]["path"]
    entry["artifact"]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
path.write_text(yaml.safe_dump(data, sort_keys=False))
PY
done

if [[ "$(id -u)" == 0 ]]; then
  chmod 0755 "$temp"
  # The release parent needs traversal permissions when hooks drop to nobody.
  install -d -m 0755 "$temp/targets"
fi

commit() {
  git -C "$config" add .
  git -C "$config" commit -qm "$1"
}

invoke() {
  local action=$1 environment=$2
  shift 2
  python tools/deploy.py "$action" --environment "$environment" \
    --config-repo "$config" --inventory "$temp/$environment.yml" --bundle-dir "$fixtures" \
    --report "$temp/$environment.json" "$@"
}

commit initial
for environment in dev test prod dr; do
  invoke validate "$environment"
done
invoke deploy dev
invoke deploy test
invoke promote dev --to test --components orders --output "$temp/candidate.yml"
python - "$config/envs/test/deployment.yml" "$temp/candidate.yml" <<'PY'
import sys
from pathlib import Path
import yaml

original, candidate = [yaml.safe_load(Path(path).read_text()) for path in sys.argv[1:]]
assert candidate["components"]["orders"]["artifact"]["component_version"] == "3.8.2"
assert candidate["components"]["orders"]["config"] == original["components"]["orders"]["config"]
assert candidate["components"]["billing"] == original["components"]["billing"]
PY
cp "$temp/candidate.yml" "$config/envs/test/deployment.yml"
commit promoted
invoke deploy test --components orders
invoke status test
invoke deploy test --components orders
python - "$temp/test.json" "$temp/targets/test" <<'PY'
import json
import sys
from pathlib import Path
import javaproperties

row, = json.loads(Path(sys.argv[1]).read_text())["components"]
assert row["running_version"] == "3.8.2"
properties = Path(sys.argv[2]) / "orders/current/config/application.properties"
with properties.open("rb") as file:
    parsed = javaproperties.load(file)
assert "test-db" in parsed["db.url"]
assert parsed["db.password"] == "demo: tricky = value"
assert properties.stat().st_mode & 0o777 == 0o640
PY

invoke promote test --to prod --components orders --output "$temp/prod-candidate.yml"
cp "$temp/prod-candidate.yml" "$config/envs/prod/deployment.yml"
commit promoted-to-prod
invoke deploy prod
invoke promote prod --to dr --components orders --output "$temp/dr-candidate.yml"
cp "$temp/dr-candidate.yml" "$config/envs/dr/deployment.yml"
commit promoted-to-dr
invoke deploy dr

replace_artifact() {
  python - "$config/envs/test/deployment.yml" "$fixtures" "$1" "$2" <<'PY'
import hashlib
import sys
from pathlib import Path
import yaml

path = Path(sys.argv[1])
baseline = yaml.safe_load(path.read_text())
version = sys.argv[3]
name = f"myapp-{version}.tar.gz"
checksum = hashlib.sha256((Path(sys.argv[2]) / name).read_bytes()).hexdigest()
baseline["components"]["orders"]["artifact"].update(
    path=name, sha256=checksum, bundle_version=version, component_version=sys.argv[4]
)
path.write_text(yaml.safe_dump(baseline, sort_keys=False))
PY
  commit "$1"
}

for invalid in broken missing-property; do
  if [[ "$invalid" == broken ]]; then version=3.8.3; else version=3.8.4; fi
  replace_artifact "$invalid" "$version"
  if invoke deploy test --components orders > "$temp/$invalid.log" 2>&1; then
    cat "$temp/$invalid.log"
    printf 'Expected failure for %s\n' "$invalid"
    exit 1
  fi
  if [[ "$invalid" == broken ]]; then
    rg -q 'Rollback completed' "$temp/$invalid.log" 2>/dev/null || grep -q 'Rollback completed' "$temp/$invalid.log"
  else
    rg -q 'Missing required keys' "$temp/$invalid.log" 2>/dev/null || grep -q 'Missing required keys' "$temp/$invalid.log"
  fi
  python - "$temp/test.json" <<'PY'
import json
import sys
from pathlib import Path

row, = json.loads(Path(sys.argv[1]).read_text())["components"]
assert row["state"] == "healthy"
assert row["running_version"] == "3.8.2"
assert not row["matches_baseline"]
PY
done
if invoke promote test --to prod --components orders --output "$temp/blocked.yml"; then
  printf 'Expected promotion to fail when the source differs from its baseline\n'
  exit 1
fi
test ! -e "$temp/blocked.yml"
printf 'Integration checks passed.\n'
```

#### Source 15

`myapp-ansible/tests/make_bundles.py`

```python
"""Generate small reproducible vendor tarballs for the integration test."""

import gzip
import io
import sys
import tarfile
import textwrap
from pathlib import Path

import yaml


def member(tar, name, value, executable=False):
    data = value.encode()
    item = tarfile.TarInfo(name)
    item.size = len(data)
    item.mode = 0o755 if executable else 0o644
    tar.addfile(item, io.BytesIO(data))


def bundle(directory, version, orders, broken=False, required="db.url"):
    components = {"orders": orders, "billing": "2.13.4"}
    manifest = {"bundle_version": version, "components": {}}
    for name, build in components.items():
        manifest["components"][name] = {
            "version": build,
            "path": f"components/{name}",
            "required_properties": {"config/application.properties": [required, "db.password"]},
        }

    path = directory / f"myapp-{version}.tar.gz"
    with path.open("wb") as output, gzip.GzipFile(fileobj=output, filename="", mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as tar:
            member(tar, "manifest.yml", yaml.safe_dump(manifest))
            for name, build in components.items():
                prefix = f"components/{name}"
                member(tar, f"{prefix}/BUILD", f"{build}\n")
                member(tar, f"{prefix}/config/application.properties", "# supplied at deploy time\n")
                start = textwrap.dedent("""\
                    #!/bin/sh
                    set -eu
                    cat BUILD > "$MYAPP_SHARED_DIR/running-version"
                    """)
                member(tar, f"{prefix}/bin/start", start, executable=True)
                member(tar, f"{prefix}/bin/stop", '#!/bin/sh\nrm -f "$MYAPP_SHARED_DIR/running-version"\n', executable=True)
                condition = "exit 1" if broken and name == "orders" else 'test -s "$MYAPP_SHARED_DIR/running-version"'
                member(tar, f"{prefix}/bin/health", f"#!/bin/sh\n{condition}\n", executable=True)
                member(tar, f"{prefix}/bin/version", '#!/bin/sh\ncat "$MYAPP_SHARED_DIR/running-version"\n', executable=True)
    return path


def main():
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    bundle(directory, "2026.11.10.2", "3.8.1")
    bundle(directory, "2026.11.23.1", "3.8.2")
    bundle(directory, "broken", "3.8.3", broken=True)
    bundle(directory, "missing-property", "3.8.4", required="new.required.key")


if __name__ == "__main__":
    main()
```

#### Source 16

`myapp-ansible/tests/test_offline.py`

```python
"""Verify the fail-closed package bootstrap without network access."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import bootstrap_python
import network


class OfflineTests(unittest.TestCase):
    def test_requires_approved_https_authority(self):
        host = "nexus.corp.example:8443"
        network.approved_https(f"https://{host}/repository/vendor/x.tar", host, "Nexus")
        for url in (
            f"http://{host}/x", "https://elsewhere.example/x",
            f"https://user@{host}/x", f"https://{host}/x?query", f"https://{host}/x#target",
            f"https://{host}/bad path", f"https://{host}/x\\y",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                network.approved_https(url, host, "Nexus")

    def test_download_ignores_proxies_and_refuses_redirects(self):
        with patch.dict(os.environ, {"MYAPP_NEXUS_AUTHORITY": "nexus.corp.example"}, clear=True):
            with patch.object(network.urllib.request, "build_opener") as build:
                opener = build.return_value
                opener.open.side_effect = RuntimeError("simulated transfer failure")
                with self.assertRaisesRegex(RuntimeError, "simulated transfer failure"):
                    network.download_nexus("https://nexus.corp.example/repository/x.tar", Path("unused"), "MYAPP_NEXUS")
                proxy, redirects = build.call_args.args
                self.assertEqual(proxy.proxies, {})
                self.assertIsNone(redirects.redirect_request(None, None, 302, "", {}, "https://elsewhere.example/"))

    def test_requirements_disallow_links_and_pip_directives(self):
        with tempfile.TemporaryDirectory() as temp:
            requirements = Path(temp) / "requirements.txt"
            sanitized = Path(temp) / "sanitized.txt"
            requirements.write_text("ansible-core>=2.19,<2.20\n")
            bootstrap_python.read_requirements(requirements, sanitized)
            self.assertEqual(sanitized.read_text(), requirements.read_text())
            for line in ("--extra-index-url https://elsewhere.example/simple", "demo @ https://elsewhere.example/w.whl", "-r other.txt"):
                with self.subTest(line=line), self.assertRaises(ValueError):
                    requirements.write_text(line + "\n")
                    bootstrap_python.read_requirements(requirements, sanitized)

    def test_pip_clears_inherited_configuration(self):
        with patch.dict(os.environ, {"PIP_EXTRA_INDEX_URL": "https://elsewhere.example/simple", "PIP_CONFIG_FILE": "/invalid"}, clear=True):
            with patch.object(bootstrap_python.subprocess, "run") as run:
                bootstrap_python.pip(["install", "--no-index", "--no-deps", "/tmp/example.whl"])
        command = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        self.assertEqual({key: value for key, value in env.items() if key.startswith("PIP_")}, {"PIP_CONFIG_FILE": os.devnull})
        self.assertIn("--disable-pip-version-check", command)
        self.assertIn("--no-index", command)
        self.assertIn("--no-deps", command)

    def test_wheel_install_uses_only_explicit_local_files(self):
        with tempfile.TemporaryDirectory() as temp:
            wheelhouse = Path(temp)
            wheel = wheelhouse / "demo-1.0-py3-none-any.whl"
            wheel.touch()
            with patch.object(bootstrap_python, "pip") as pip:
                bootstrap_python.install(wheelhouse, Path("/tmp/requirements.txt"))
            self.assertEqual(
                pip.call_args_list[0].args[0],
                ["install", "--no-index", "--no-deps", "--only-binary=:all:", str(wheel)],
            )
            self.assertEqual(pip.call_args_list[-1].args[0], ["check"])
            (wheelhouse / "source.tar").touch()
            with self.assertRaisesRegex(ValueError, "flat .whl"):
                bootstrap_python.install(wheelhouse, Path("/tmp/requirements.txt"))


if __name__ == "__main__":
    unittest.main()
```

#### Source 17

`myapp-ansible/tools/archive.py`

```python
"""Extraction shared by the controller and Linux deployment hosts."""

import os
import shutil
import tarfile
from pathlib import Path


def relative_path(value):
    if not isinstance(value, str) or not value or any(
        part in ("", ".", "..") for part in value.split("/")
    ) or "\\" in value or any(ord(char) < 32 for char in value):
        raise ValueError(f"Invalid relative path: {value!r}")
    return Path(value)


def extract(archive, destination, group=None):
    destination = Path(destination)
    with tarfile.open(archive, "r:*") as tar:
        seen = set()
        for member in tar:
            path = relative_path(member.name)
            if path in seen or not (member.isdir() or member.isfile()):
                raise ValueError(f"Unsupported or duplicate archive entry: {member.name}")
            seen.add(path)
            target = destination / path
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o750 if member.mode & 0o111 else 0o640)

    for root, directories, files in os.walk(destination):
        directory = Path(root)
        directory.chmod(0o750)
        if group is not None:
            os.chown(directory, -1, group)
            for filename in files:
                os.chown(directory / filename, -1, group)
```

#### Source 18

`myapp-ansible/tools/bootstrap_python.py`

```python
"""Install controller dependencies exclusively from a verified Nexus wheel archive."""

import hashlib
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from archive import extract
from network import download_nexus

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:(?:===|==|!=|~=|>=|<=|>|<)[0-9][A-Za-z0-9.*+_-]*(?:,|$))+")


def read_requirements(source, destination):
    """Write only simple distribution/version specifiers for the final pip validation."""
    lines = [line.strip() for line in source.read_text().splitlines()]
    requirements = [line for line in lines if line and not line.startswith("#")]
    if not requirements or any(
        line.endswith(",") or not REQUIREMENT.fullmatch(line) for line in requirements
    ):
        raise ValueError("Use only package names with version constraints in requirements.txt")
    destination.write_text("\n".join(requirements) + "\n")


def pip(arguments):
    settings = {name: value for name, value in os.environ.items() if not name.startswith("PIP_")}
    settings["PIP_CONFIG_FILE"] = os.devnull
    command = [sys.executable, "-I", "-m", "pip", "--disable-pip-version-check", "--no-cache-dir"]
    subprocess.run([*command, *arguments], env=settings, check=True)


def install(wheelhouse, requirements):
    wheels = sorted(wheelhouse.iterdir())
    if not wheels or any(not wheel.is_file() or wheel.suffix != ".whl" for wheel in wheels):
        raise ValueError("The Nexus wheel archive must contain only flat .whl files")
    flags = ["--no-index", "--no-deps", "--only-binary=:all:"]
    pip(["install", *flags, *(str(wheel) for wheel in wheels)])
    pip(["install", "--dry-run", *flags, "-r", str(requirements)])
    pip(["check"])


def main():
    if sys.prefix == sys.base_prefix:
        print("Use a dedicated Python virtual environment for the offline bootstrap", file=sys.stderr)
        return 1
    try:
        checksum = os.environ.get("MYAPP_NEXUS_WHEELHOUSE_SHA256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError("Set MYAPP_NEXUS_WHEELHOUSE_SHA256 to the approved archive SHA-256")
        with tempfile.TemporaryDirectory(prefix="myapp-wheels-") as directory:
            work = Path(directory)
            requirements = work / "requirements.txt"
            read_requirements(ROOT / "requirements.txt", requirements)
            archive = work / "wheelhouse.tar"
            download_nexus(
                os.environ.get("MYAPP_NEXUS_WHEELHOUSE_URL", ""), archive, "MYAPP_NEXUS_WHEELHOUSE",
            )
            with archive.open("rb") as file:
                if hashlib.file_digest(file, "sha256").hexdigest() != checksum:
                    raise ValueError("Nexus wheel archive SHA-256 mismatch")
            wheelhouse = work / "wheels"
            wheelhouse.mkdir()
            extract(archive, wheelhouse)
            install(wheelhouse, requirements)
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f"Offline Python bootstrap failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

#### Source 19

`myapp-ansible/tools/ci_checkout.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

[[ "$CONFIG_REF" =~ ^([a-f0-9]{40}|[a-f0-9]{64})$ ]]
[[ "$CONFIG_PROJECT_PATH" =~ ^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$ ]]
python - "$CI_SERVER_URL" "${MYAPP_GITLAB_AUTHORITY:-}" <<'PY'
import sys

sys.path.insert(0, "tools")
from network import approved_https

approved_https(sys.argv[1], sys.argv[2], "GitLab URL")
PY

# Scope credentials and transport policy to this HTTPS clone invocation.
credential=$(printf 'gitlab-ci-token:%s' "$CI_JOB_TOKEN" | base64 -w0)
unset GIT_SSL_NO_VERIFY GIT_CONFIG_PARAMETERS http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
GIT_TERMINAL_PROMPT=0 GIT_LFS_SKIP_SMUDGE=1 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_COUNT=4 \
  GIT_CONFIG_KEY_0="http.${CI_SERVER_URL}/.extraHeader" \
  GIT_CONFIG_VALUE_0="Authorization: Basic $credential" \
  GIT_CONFIG_KEY_1=http.followRedirects GIT_CONFIG_VALUE_1=false \
  GIT_CONFIG_KEY_2=http.proxy GIT_CONFIG_VALUE_2= \
  GIT_CONFIG_KEY_3=http.sslVerify GIT_CONFIG_VALUE_3=true \
  git -c protocol.allow=never -c protocol.https.allow=always \
    clone --no-tags --no-recurse-submodules "${CI_SERVER_URL%/}/${CONFIG_PROJECT_PATH}.git" .config-repo

git -C .config-repo merge-base --is-ancestor "$CONFIG_REF" origin/HEAD
```

#### Source 20

`myapp-ansible/tools/deploy.py`

```python
#!/usr/bin/env python3
"""Resolve Git and Nexus inputs and run the Ansible playbooks."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

import javaproperties
import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from archive import extract, relative_path
from network import approved_https, download_nexus

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS = ("dev", "test", "prod", "dr")
PROMOTION_SOURCE = dict(zip(ENVIRONMENTS[1:], ENVIRONMENTS))
HOOKS = ("start", "stop", "health")
RECEIPT = ".deployment.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def identity(value):
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def sha256_file(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def mapping(value, name):
    require(isinstance(value, dict) and all(isinstance(key, str) for key in value), f"{name} must be a mapping with string keys")
    return value


def load(path):
    return mapping(yaml.safe_load(path.read_text()), str(path))


def run(command, capture=False):
    return subprocess.run(command, cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE if capture else None).stdout


def snapshot(repository, ref, work):
    git = ["git", "-C", str(repository.resolve())]
    commit = run([*git, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"], capture=True).strip()
    archive = work / "config.tar"
    run([*git, "archive", "--format=tar", f"--output={archive}", commit])
    target = work / "config"
    target.mkdir()
    extract(archive, target)
    return target, commit


def positive(value, name, minimum=1):
    require(type(value) is int and value >= minimum, f"{name} must be an integer >= {minimum}")
    return value


def read_baseline(config, environment):
    data = load(config / "envs" / environment / "deployment.yml")
    require(data["environment"] == environment, f"Environment mismatch in the {environment} baseline")
    components = mapping(data["components"], "components")
    order = data["deployment_order"]
    require(isinstance(order, list) and all(isinstance(name, str) for name in order) and len(order) == len(components) and set(order) == set(components), "deployment_order must list every component exactly once")
    for name in order:
        entry = mapping(components[name], name)
        require(re.fullmatch(r"[a-z][a-z0-9_-]*", name), f"Invalid component name: {name}")
        require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry["group"]), f"Invalid inventory group for {name}")
        require(re.fullmatch(r"[a-z_][a-z0-9_-]*", entry["service_user"]), f"Invalid service user for {name}")
        require(entry["service_user"] != "root", f"Choose an unprivileged service user for {name}")
        positive(entry.get("serial", 1), "serial")
        positive(entry.get("hook_timeout", 30), "hook_timeout")
        positive(entry.get("health_retries", 10), "health_retries")
        positive(entry.get("health_delay", 3), "health_delay", 0)
        artifact = mapping(entry["artifact"], "artifact")
        relative_path(artifact["path"])
        require(re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]), f"Invalid bundle SHA-256 for {name}")
        for key in ("bundle_version", "component_version"):
            require(isinstance(artifact[key], str) and artifact[key], f"artifact.{key} must be a nonempty string")
    return data


def choose(baseline, selection):
    order = baseline["deployment_order"]
    names = order if selection == "all" else [item.strip() for item in selection.split(",")]
    require(names and len(names) == len(set(names)) and set(names) <= set(order), "Select 'all' or a comma-separated list of known components")
    return [name for name in order if name in names]


def configuration(root, environment, entry):
    settings = mapping(entry["config"], "config")
    value_path = settings.get("values")
    prefix = ("envs", environment)
    if value_path:
        value_path = relative_path(value_path)
        require(value_path.parts[:2] == prefix, "Values must come from the target environment")
    values = load(root / value_path) if value_path else {}
    params = mapping(values.get("parameters", {}), "parameters")
    secrets = mapping(values.get("secret_env", {}), "secret_env")
    require(all(isinstance(val, str) for val in params.values()), "Quote parameter values as strings")
    require(not (params.keys() & secrets.keys()), "A parameter cannot also be a secret reference")
    secret_revision = values.get("secret_revision")
    require(not secrets or isinstance(secret_revision, str) and secret_revision, "Set secret_revision when using secret references")
    for variable in secrets.values():
        require(isinstance(variable, str) and re.fullmatch(r"MYAPP_SECRET_[A-Z0-9_]+", variable), "Secret references must name MYAPP_SECRET_* environment variables")
    files = {}
    fingerprint = {"values": values, "values_path": str(value_path) if value_path else None, "files": {}}
    for rule in settings.get("files", []):
        source = relative_path(rule["source"])
        require(source.parts[:2] == prefix, "Configuration must come from the target environment")
        destination = relative_path(rule["destination"])
        require(destination != Path(RECEIPT) and destination.parts[0] != "bin", "Configuration cannot overwrite hooks or the receipt")
        action = rule["action"]
        require(action in ("copy", "template") and destination not in files, f"Invalid or duplicate mapping for {destination}")
        raw = (root / source).read_bytes()
        files[destination] = (action, raw)
        fingerprint["files"][str(destination)] = {"action": action, "source": str(source), "sha256": digest(raw)}
    return files, params, secrets, identity(fingerprint)


def obtain_bundle(args, artifact, work):
    sha = artifact["sha256"]
    target = work / f"{sha}.tar"
    if not target.exists():
        path = relative_path(artifact["path"])
        if args.bundle_dir:
            shutil.copyfile(args.bundle_dir / path, target)
        else:
            base = os.environ.get("MYAPP_NEXUS_BASE_URL", "")
            approved_https(base, os.environ.get("MYAPP_NEXUS_AUTHORITY", ""), "Nexus repository URL")
            url = f"{base.rstrip('/')}/{urllib.parse.quote(str(path), safe='/')}"
            download_nexus(url, target, "MYAPP_NEXUS")
        require(sha256_file(target) == sha, f"SHA-256 mismatch for {artifact['path']}")
    directory = work / sha
    if not directory.exists():
        directory.mkdir()
        extract(target, directory)
    return directory


def stage_bundle(args, snapshot_path, commit, work, name, entry, planned_release, config_fingerprint):
    artifact = entry["artifact"]
    contents = obtain_bundle(args, artifact, work)
    manifest = load(contents / "manifest.yml")
    require(manifest["bundle_version"] == artifact["bundle_version"], f"Bundle version mismatch for {name}")
    vendor = mapping(manifest["components"][name], f"vendor component {name}")
    require(vendor["version"] == artifact["component_version"], f"Component version mismatch for {name}")
    source = contents / relative_path(vendor["path"])
    require(source.is_dir(), f"Component directory missing for {name}")
    directory = work / "payloads" / name
    shutil.copytree(source, directory)
    require(not (directory / RECEIPT).exists(), f"Reserved filename in vendor component {name}")
    for hook in HOOKS:
        file = directory / "bin" / hook
        require(file.is_file() and file.stat().st_mode & 0o111, f"Missing executable bin/{hook} for {name}")
    version_hook = directory / "bin/version"
    require(not version_hook.exists() or (version_hook.is_file() and version_hook.stat().st_mode & 0o111), f"Invalid bin/version for {name}")

    files, params, secrets, _ = configuration(snapshot_path, args.environment, entry)
    values = dict(params)
    for parameter, variable in secrets.items():
        require(bool(os.environ.get(variable)), f"Missing secret variable {variable} for {name}")
        values[parameter] = os.environ[variable]
    renderer = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
    renderer.filters["java_property"] = javaproperties.escape
    for destination, (action, raw) in files.items():
        target = directory / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        if action == "template":
            try:
                raw = renderer.from_string(raw.decode()).render(settings=values).encode()
            except Exception:
                raise ValueError(f"Failed to render {name}/{destination}; check the template and declared settings") from None
        target.write_bytes(raw)
        target.chmod(0o640)

    for prop_file, keys in mapping(vendor.get("required_properties", {}), "required_properties").items():
        require(isinstance(keys, list) and all(isinstance(key, str) for key in keys), f"Invalid property contract for {name}")
        properties = directory / relative_path(prop_file)
        require(properties.is_file(), f"Required properties file missing: {name}/{prop_file}")
        try:
            with properties.open("rb") as file:
                values = javaproperties.load(file)
        except Exception:
            raise ValueError(f"Could not parse {name}/{prop_file}") from None
        missing = [key for key in keys if not values.get(key, "").strip()]
        require(not missing, f"Missing required keys in {name}/{prop_file}: {', '.join(missing)}")

    receipt = {
        "component": name, "environment": args.environment, "component_version": artifact["component_version"],
        "bundle_version": artifact["bundle_version"], "nexus_path": artifact["path"],
        "bundle_sha256": artifact["sha256"], "config_commit": commit,
        "config_fingerprint": config_fingerprint, "release_id": planned_release,
        "ci_job": os.environ.get("CI_JOB_URL") or os.environ.get("BUILD_URL"),
    }
    (directory / RECEIPT).write_text(json.dumps(receipt))
    package = work / f"payload-{name}.tar"
    with tarfile.open(package, "w") as tar:
        for child in sorted(directory.iterdir()):
            tar.add(child, arcname=child.name)
    return package


def inventory(path):
    return json.loads(run(["ansible-inventory", "-i", str(path), "--list"], capture=True))


def hosts(groups, group):
    require(group in groups, f"Inventory group {group} is missing")
    values = groups[group]
    result = set(values.get("hosts", []))
    for child in values.get("children", []):
        result.update(hosts(groups, child))
    require(result, f"Inventory group {group} has no hosts")
    return result


def build_host_tool(work):
    package = work / "myapp-host.pyz"
    with zipfile.ZipFile(package, "w") as zipapp:
        zipapp.write(ROOT / "tools/host.py", "__main__.py")
        zipapp.write(ROOT / "tools/archive.py", "archive.py")
    return package


def make_plan(baseline, selected, config, env, inv):
    planned = {}
    for name in selected:
        entry = baseline["components"][name]
        _, _, _, fingerprint = configuration(config, env, entry)
        release = identity({"component": name, "environment": env, "artifact": entry["artifact"], "configuration": fingerprint})
        planned[name] = {
            "component_name": name, "component_group": entry["group"], "component_user": entry["service_user"],
            "rollout_serial": entry.get("serial", 1), "hook_timeout": entry.get("hook_timeout", 30),
            "health_retries": entry.get("health_retries", 10), "health_delay": entry.get("health_delay", 3),
            "release_id": release, "config_fingerprint": fingerprint, "target_hosts": sorted(hosts(inv, entry["group"])),
        }
    return planned


def play(playbook, common, params, work):
    variables = work / "ansible-input.json"
    variables.write_text(json.dumps({**common, **params}))
    run(["ansible-playbook", "-i", common["inventory_path"], str(ROOT / "playbooks" / playbook), "-e", f"@{variables}"])


def collect_status(plan, common, work, report):
    rows = []
    for name, entry in plan.items():
        with tempfile.TemporaryDirectory(dir=work) as directory:
            directory = Path(directory)
            try:
                play("status.yml", common, {**entry, "status_dir": str(directory)}, work)
            except subprocess.CalledProcessError:
                print(f"Ansible status query failed on one or more hosts for {name}", file=sys.stderr)
            for host in entry["target_hosts"]:
                path = directory / f"{digest(host.encode())}.json"
                if path.exists():
                    observed = json.loads(path.read_text())
                else:
                    observed = {"state": "unknown", "error": "Ansible returned no host result"}
                rows.append({
                    **observed, "component": name, "host": host, "expected_release_id": entry["release_id"],
                    "matches_baseline": observed.get("release_id") == entry["release_id"],
                })
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"environment": common["deployment_environment"], "baseline_commit": common["baseline_commit"], "components": rows}, indent=2) + "\n")
    for row in rows:
        version = row.get("component_version", "-")
        print(f"{row['host']:24} {row['component']:20} {version:16} {row['state']:24} baseline={'yes' if row['matches_baseline'] else 'no'}")
    return all(row["state"] == "healthy" and row["matches_baseline"] for row in rows)


def promote(args, config, source, selected):
    target = read_baseline(config, args.to)
    for name in selected:
        require(name in target["components"], f"Add {name} and its {args.to} configuration to the target baseline first")
        target["components"][name]["artifact"] = source["components"][name]["artifact"]
        configuration(config, args.to, target["components"][name])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(target, sort_keys=False))
    print(f"Candidate written to {args.output}. Review it in a GitLab merge request.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["deploy", "status", "promote", "validate"])
    parser.add_argument("--config-repo", type=Path, required=True)
    parser.add_argument("--config-ref", default="HEAD")
    parser.add_argument("--environment", choices=ENVIRONMENTS, required=True)
    parser.add_argument("--components", default="all")
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--bundle-dir", type=Path)
    parser.add_argument("--report", type=Path, default=Path("reports/status.json"))
    parser.add_argument("--to", choices=ENVIRONMENTS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "promote":
        if not args.to or not args.output or PROMOTION_SOURCE.get(args.to) != args.environment:
            parser.error("promotion requires --output and the next environment: dev → test → prod → dr")

    try:
        with tempfile.TemporaryDirectory(prefix="myapp-") as directory:
            work = Path(directory)
            config, commit = snapshot(args.config_repo, args.config_ref, work)
            baseline = read_baseline(config, args.environment)
            selected = choose(baseline, args.components)
            inv_path = (args.inventory or ROOT / "inventories" / args.environment / "hosts.yml").resolve()
            plan = make_plan(baseline, selected, config, args.environment, inventory(inv_path))
            if args.action == "validate":
                print(f"Valid baseline {args.environment} at {commit}: {', '.join(selected)}")
                return 0
            common = {
                "deployment_environment": args.environment,
                "baseline_commit": commit,
                "inventory_path": str(inv_path),
                "host_tool": str(build_host_tool(work)),
            }
            if args.action == "deploy":
                failed = False
                try:
                    for name, params in plan.items():
                        params["component_archive"] = str(stage_bundle(
                            args, config, commit, work, name, baseline["components"][name],
                            params["release_id"], params["config_fingerprint"],
                        ))
                    for params in plan.values():
                        play("stage.yml", common, params, work)
                    for params in plan.values():
                        play("activate.yml", common, params, work)
                except (subprocess.CalledProcessError, OSError, TypeError, KeyError, ValueError, tarfile.TarError) as error:
                    print(f"Deployment failed: {error}", file=sys.stderr)
                    failed = True
                healthy = collect_status(plan, common, work, args.report)
                return int(failed or not healthy)
            healthy = collect_status(plan, common, work, args.report)
            if not healthy:
                return 1
            if args.action == "promote":
                promote(args, config, baseline, selected)
            return 0
    except (subprocess.CalledProcessError, OSError, TypeError, KeyError, ValueError, yaml.YAMLError, tarfile.TarError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

#### Source 21

`myapp-ansible/tools/host.py`

```python
"""Run on a Linux target through Ansible. Requires Python 3 and util-linux."""

import argparse
import contextlib
import fcntl
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from archive import extract

RECEIPT = ".deployment.json"


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        json.dump(value, file)
        file.flush()
        os.fsync(file.fileno())
    os.replace(file.name, path)


class Component:
    def __init__(self, args):
        self.name = args.component
        self.root = Path(args.root) / self.name
        self.releases = self.root / "releases"
        self.shared = self.root / "shared"
        self.current = self.root / "current"
        self.pending = self.root / ".activation.json"
        self.user = pwd.getpwnam(args.user)
        self.timeout = args.timeout
        self.retries = args.retries
        self.delay = args.delay

    @contextlib.contextmanager
    def lock(self):
        self.root.mkdir(mode=0o755, parents=True, exist_ok=True)
        with (self.root / ".lock").open("a") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            yield

    def stage(self, archive, release):
        with self.lock():
            self.releases.mkdir(mode=0o755, exist_ok=True)
            if not self.shared.exists():
                self.shared.mkdir(mode=0o750)
                os.chown(self.shared, self.user.pw_uid, self.user.pw_gid)
            target = self.releases / release
            if target.exists():
                if self.receipt(release)["release_id"] != release:
                    raise RuntimeError("Existing staged release has an invalid receipt")
                return {"changed": False}
            temp = Path(tempfile.mkdtemp(dir=self.releases))
            try:
                extract(archive, temp, self.user.pw_gid)
                receipt = json.loads((temp / RECEIPT).read_text())
                if receipt["release_id"] != release or receipt["component"] != self.name:
                    raise RuntimeError("Staged release receipt does not match the request")
                temp.rename(target)
            finally:
                if temp.exists():
                    shutil.rmtree(temp)
            return {"changed": True}

    def receipt(self, release):
        return json.loads((self.releases / release / RECEIPT).read_text())

    def active(self):
        if self.current.is_symlink():
            target = os.readlink(self.current)
            if re.fullmatch(r"releases/[0-9a-f]{64}", target):
                return Path(target).name
            raise RuntimeError("current symlink points outside the managed releases")
        if self.current.exists():
            raise RuntimeError("current exists and is not a managed symlink")
        return None

    def point_to(self, release):
        if release is None:
            self.current.unlink(missing_ok=True)
            return
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            link = Path(directory) / "current"
            link.symlink_to(Path("releases") / release)
            os.replace(link, self.current)

    def hook(self, release, action):
        directory = self.releases / release
        command = [str(directory / "bin" / action)]
        if os.geteuid() != self.user.pw_uid:
            command = ["runuser", "-u", self.user.pw_name, "--", *command]
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": self.user.pw_dir,
            "USER": self.user.pw_name,
            "LOGNAME": self.user.pw_name,
            "MYAPP_COMPONENT_DIR": str(directory),
            "MYAPP_SHARED_DIR": str(self.shared),
            "MYAPP_COMPONENT": self.name,
        }
        try:
            result = subprocess.run(
                command, cwd=directory, env=env, timeout=self.timeout,
                stdout=subprocess.PIPE if action == "version" else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, text=True, check=True,
            )
        except (subprocess.SubprocessError, OSError) as error:
            raise RuntimeError(f"{action} hook failed for {self.name} ({type(error).__name__})") from None
        return result.stdout.strip() if action == "version" else None

    def check(self, release, attempts):
        for attempt in range(attempts):
            try:
                self.hook(release, "health")
                if (self.releases / release / "bin/version").exists():
                    running = self.hook(release, "version")
                    if running != self.receipt(release)["component_version"]:
                        raise RuntimeError(f"Running version mismatch for {self.name}")
                    return running
                return None
            except RuntimeError:
                if attempt == attempts - 1:
                    raise
                time.sleep(self.delay)

    def recover(self):
        journal = json.loads(self.pending.read_text())
        previous = journal["previous"]
        current = self.active()
        if current is not None:
            self.hook(current, "stop")
        self.point_to(previous)
        if previous is not None:
            self.hook(previous, "start")
            self.check(previous, self.retries)
        self.pending.unlink()

    def activate(self, release):
        with self.lock():
            self.receipt(release)
            if self.pending.exists():
                try:
                    self.recover()
                except Exception as error:
                    raise RuntimeError(f"Interrupted activation recovery failed: {error}") from None
            previous = self.active()
            if previous == release:
                try:
                    self.check(release, 1)
                    return {"changed": False}
                except RuntimeError:
                    pass
            write_json(self.pending, {"previous": previous})
            try:
                if previous is not None:
                    self.hook(previous, "stop")
                self.point_to(release)
                self.hook(release, "start")
                self.check(release, self.retries)
            except Exception as error:
                try:
                    self.recover()
                except Exception as rollback_error:
                    raise RuntimeError(f"{error}. Rollback failed: {rollback_error}") from None
                raise RuntimeError(f"{error}. Rollback completed") from None
            self.pending.unlink()
            return {"changed": True}

    def status(self):
        result = {"state": "absent", "running_version": None}
        if not self.root.is_dir():
            return result
        with self.lock():
            active = self.active()
            if active is not None:
                result.update(self.receipt(active))
                try:
                    result["running_version"] = self.check(active, 1)
                    result["state"] = "healthy"
                except RuntimeError as error:
                    result.update(state="unhealthy", error=str(error))
            if self.pending.exists():
                result["state"] = "activation_interrupted"
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["stage", "activate", "status"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--component", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--retries", type=int, required=True)
    parser.add_argument("--delay", type=int, required=True)
    parser.add_argument("--release")
    parser.add_argument("--archive")
    args = parser.parse_args()
    if args.action != "status" and not re.fullmatch(r"[0-9a-f]{64}", args.release or ""):
        parser.error("a valid release ID is required")
    try:
        component = Component(args)
        if args.action == "stage":
            result = component.stage(args.archive, args.release)
        elif args.action == "activate":
            result = component.activate(args.release)
        else:
            result = component.status()
        print(json.dumps(result))
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

#### Source 22

`myapp-ansible/tools/network.py`

```python
"""Direct HTTPS requests to an explicitly configured internal service."""

import base64
import os
import shutil
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def approved_https(url, authority, label):
    """Require the exact configured hostname and optional port, without URL credentials."""
    parsed = urllib.parse.urlsplit(url)
    if (
        not authority or parsed.scheme != "https" or not parsed.hostname
        or parsed.netloc.lower() != authority.lower() or parsed.username is not None
        or parsed.password is not None or parsed.query or parsed.fragment
        or any(character.isspace() or character == "\\" for character in url)
    ):
        raise ValueError(f"{label} must use HTTPS at its configured internal host and port")
    if parsed.port == 0:
        raise ValueError(f"{label} has an invalid port")
    return parsed


def download_nexus(url, destination, credential_prefix):
    approved_https(url, os.environ.get("MYAPP_NEXUS_AUTHORITY", ""), "Nexus URL")
    user = os.environ.get(f"{credential_prefix}_USER")
    password = os.environ.get(f"{credential_prefix}_PASSWORD")
    if (user is None) != (password is None):
        raise ValueError(f"Set both {credential_prefix} credential variables or neither")
    request = urllib.request.Request(url)
    if user is not None:
        credential = base64.b64encode(f"{user}:{password}".encode()).decode()
        request.add_header("Authorization", f"Basic {credential}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=60) as response, destination.open("xb") as file:
        shutil.copyfileobj(response, file)
```

#### Source 23

`myapp-config/.gitignore`

```text
*.local
*.secret
.env
```

#### Source 24

`myapp-config/README.md`

````markdown
# MyApp environment configuration

This repository holds environment baselines and the configuration owned by application support. It pairs with the `myapp-ansible` deployment repository.

```text
envs/
  dev/deployment.yml
  test/deployment.yml
  prod/deployment.yml
  dr/deployment.yml
```

Each baseline lists all managed components in deployment order. A component records its inventory group, service user, rollout settings, immutable Nexus artifact path and SHA-256, vendor bundle and component versions, and a configuration map. The artifact path is relative to the Nexus repository URL configured on the runner.

## Maintain configuration

- Under each environment's `files/<component>/`, keep ordinary configuration values, templates and static files. Quote property values in YAML so they stay strings.
- In the component's `config.files` list, `source` is a path relative to this Git repository and `destination` is relative to the installed component directory. Use `template` for a Jinja2 template or `copy` for a static file. Use the `java_property` filter for Java properties values.
- In `values.yml`, map secret keys to `MYAPP_SECRET_*` environment variable names. Supply their values from the runner's environment-scoped secret store. When a secret rotates, update `secret_revision` so the next deployment activates a freshly rendered release. Retain recoverable secret versions for baselines that need them. Git contains the reference and revision. The deployment receipt records the configuration commit and fingerprint. The installed properties file contains the resolved value.
- Use merge requests to review and approve changes. Pass the merged commit SHA to deploy. The deployer reads the tracked content at that commit, including the configuration paths in the manifest. The configuration identity uses the declared values, references, revision and mapped files, so unrelated commits or edits in other environments do not trigger a restart.

## Maintain releases

For a new delivery, update the selected component's `artifact` in dev through a merge request. Use Nexus's trusted SHA-256 and confirm that the component name and versions agree with the vendor manifest. Leave other artifact pins in place when you want to deploy a subset.

After deploy and verification, the Ansible repository's promotion command produces a candidate for the next environment. Review and merge the artifact changes there, then deploy the selected components from the merged commit. Promotion carries the artifact pin forward. Each environment supplies its own values, secret bindings, and host settings.

The included `orders` and `billing` entries, host-related values, and artifact digests describe demo fixtures. Replace them with approved values and actual Nexus artifacts before use.
````

#### Source 25

`myapp-config/envs/dev/deployment.yml`

The same contents also appear at these paths.

- `myapp-ansible/tests/example/envs/dev/deployment.yml`

```yaml
environment: dev
deployment_order:
- orders
- billing
components:
  orders:
    group: orders_nodes
    service_user: myapp
    serial: 1
    hook_timeout: 30
    health_retries: 10
    health_delay: 3
    artifact:
      path: myapp-2026.11.23.1.tar.gz
      sha256: 7833de7f1cad9a46d841613002e0e8835590a614983bee4c6abf77ab911347d4
      bundle_version: 2026.11.23.1
      component_version: 3.8.2
    config:
      values: envs/dev/files/orders/values.yml
      files:
      - source: envs/dev/files/orders/application.properties.j2
        destination: config/application.properties
        action: template
      - source: envs/dev/files/orders/logback.xml
        destination: config/logback.xml
        action: copy
  billing:
    group: billing_nodes
    service_user: myapp
    serial: 1
    hook_timeout: 30
    health_retries: 10
    health_delay: 3
    artifact:
      path: myapp-2026.11.10.2.tar.gz
      sha256: 7c24465b7786d550f6fe988f24b5862a946359edac9d9075bc55e9d032140e05
      bundle_version: 2026.11.10.2
      component_version: 2.13.4
    config:
      values: envs/dev/files/billing/values.yml
      files:
      - source: envs/dev/files/billing/application.properties.j2
        destination: config/application.properties
        action: template
      - source: envs/dev/files/billing/logback.xml
        destination: config/logback.xml
        action: copy
```

#### Source 26

`myapp-config/envs/dev/files/billing/application.properties.j2`

The same contents also appear at these paths.

- `myapp-config/envs/dev/files/orders/application.properties.j2`
- `myapp-config/envs/dr/files/billing/application.properties.j2`
- `myapp-config/envs/dr/files/orders/application.properties.j2`
- `myapp-config/envs/prod/files/billing/application.properties.j2`
- `myapp-config/envs/prod/files/orders/application.properties.j2`
- `myapp-config/envs/test/files/billing/application.properties.j2`
- `myapp-config/envs/test/files/orders/application.properties.j2`
- `myapp-ansible/tests/example/envs/dev/files/billing/application.properties.j2`
- `myapp-ansible/tests/example/envs/dev/files/orders/application.properties.j2`
- `myapp-ansible/tests/example/envs/dr/files/billing/application.properties.j2`
- `myapp-ansible/tests/example/envs/dr/files/orders/application.properties.j2`
- `myapp-ansible/tests/example/envs/prod/files/billing/application.properties.j2`
- `myapp-ansible/tests/example/envs/prod/files/orders/application.properties.j2`
- `myapp-ansible/tests/example/envs/test/files/billing/application.properties.j2`
- `myapp-ansible/tests/example/envs/test/files/orders/application.properties.j2`

```jinja
db.url={{ settings.db_url | java_property }}
db.password={{ settings.db_password | java_property }}
http.timeout.ms={{ settings.timeout_ms | java_property }}
```

#### Source 27

`myapp-config/envs/dev/files/billing/logback.xml`

The same contents also appear at these paths.

- `myapp-config/envs/dev/files/orders/logback.xml`
- `myapp-config/envs/dr/files/billing/logback.xml`
- `myapp-config/envs/dr/files/orders/logback.xml`
- `myapp-config/envs/prod/files/billing/logback.xml`
- `myapp-config/envs/prod/files/orders/logback.xml`
- `myapp-config/envs/test/files/billing/logback.xml`
- `myapp-config/envs/test/files/orders/logback.xml`
- `myapp-ansible/tests/example/envs/dev/files/billing/logback.xml`
- `myapp-ansible/tests/example/envs/dev/files/orders/logback.xml`
- `myapp-ansible/tests/example/envs/dr/files/billing/logback.xml`
- `myapp-ansible/tests/example/envs/dr/files/orders/logback.xml`
- `myapp-ansible/tests/example/envs/prod/files/billing/logback.xml`
- `myapp-ansible/tests/example/envs/prod/files/orders/logback.xml`
- `myapp-ansible/tests/example/envs/test/files/billing/logback.xml`
- `myapp-ansible/tests/example/envs/test/files/orders/logback.xml`

```xml
<configuration>
  <appender name="CONSOLE" class="ch.qos.logback.core.ConsoleAppender">
    <encoder><pattern>%d %-5level %logger - %msg%n</pattern></encoder>
  </appender>
  <root level="INFO"><appender-ref ref="CONSOLE"/></root>
</configuration>
```

#### Source 28

`myapp-config/envs/dev/files/billing/values.yml`

The same contents also appear at these paths.

- `myapp-ansible/tests/example/envs/dev/files/billing/values.yml`

```yaml
parameters:
  db_url: "jdbc:postgresql://dev-db.example.invalid:5432/billing"
  timeout_ms: "5000"
secret_env:
  db_password: MYAPP_SECRET_BILLING_DB_PASSWORD
secret_revision: "1"
```

#### Source 29

`myapp-config/envs/dev/files/orders/values.yml`

The same contents also appear at these paths.

- `myapp-ansible/tests/example/envs/dev/files/orders/values.yml`

```yaml
parameters:
  db_url: "jdbc:postgresql://dev-db.example.invalid:5432/orders"
  timeout_ms: "5000"
secret_env:
  db_password: MYAPP_SECRET_ORDERS_DB_PASSWORD
secret_revision: "1"
```

#### Source 30

`myapp-config/envs/dr/deployment.yml`

The same contents also appear at these paths.

- `myapp-ansible/tests/example/envs/dr/deployment.yml`

```yaml
environment: dr
deployment_order:
- orders
- billing
components:
  orders:
    group: orders_nodes
    service_user: myapp
    serial: 1
    hook_timeout: 30
    health_retries: 10
    health_delay: 3
    artifact:
      path: myapp-2026.11.10.2.tar.gz
      sha256: 7c24465b7786d550f6fe988f24b5862a946359edac9d9075bc55e9d032140e05
      bundle_version: 2026.11.10.2
      component_version: 3.8.1
    config:
      values: envs/dr/files/orders/values.yml
      files:
      - source: envs/dr/files/orders/application.properties.j2
        destination: config/application.properties
        action: template
      - source: envs/dr/files/orders/logback.xml
        destination: config/logback.xml
        action: copy
  billing:
    group: billing_nodes
    service_user: myapp
    serial: 1
    hook_timeout: 30
    health_retries: 10
    health_delay: 3
    artifact:
      path: myapp-2026.11.10.2.tar.gz
      sha256: 7c24465b7786d550f6fe988f24b5862a946359edac9d9075bc55e9d032140e05
      bundle_version: 2026.11.10.2
      component_version: 2.13.4
    config:
      values: envs/dr/files/billing/values.yml
      files:
      - source: envs/dr/files/billing/application.properties.j2
        destination: config/application.properties
        action: template
      - source: envs/dr/files/billing/logback.xml
        destination: config/logback.xml
        action: copy
```

#### Source 31

`myapp-config/envs/dr/files/billing/values.yml`

The same contents also appear at these paths.

- `myapp-ansible/tests/example/envs/dr/files/billing/values.yml`

```yaml
parameters:
  db_url: "jdbc:postgresql://dr-db.example.invalid:5432/billing"
  timeout_ms: "5000"
secret_env:
  db_password: MYAPP_SECRET_BILLING_DB_PASSWORD
secret_revision: "1"
```

#### Source 32

`myapp-config/envs/dr/files/orders/values.yml`

The same contents also appear at these paths.

- `myapp-ansible/tests/example/envs/dr/files/orders/values.yml`

```yaml
parameters:
  db_url: "jdbc:postgresql://dr-db.example.invalid:5432/orders"
  timeout_ms: "5000"
secret_env:
  db_password: MYAPP_SECRET_ORDERS_DB_PASSWORD
secret_revision: "1"
```

#### Source 33

`myapp-config/envs/prod/deployment.yml`

The same contents also appear at these paths.

- `myapp-ansible/tests/example/envs/prod/deployment.yml`

```yaml
environment: prod
deployment_order:
- orders
- billing
components:
  orders:
    group: orders_nodes
    service_user: myapp
    serial: 1
    hook_timeout: 30
    health_retries: 10
    health_delay: 3
    artifact:
      path: myapp-2026.11.10.2.tar.gz
      sha256: 7c24465b7786d550f6fe988f24b5862a946359edac9d9075bc55e9d032140e05
      bundle_version: 2026.11.10.2
      component_version: 3.8.1
    config:
      values: envs/prod/files/orders/values.yml
      files:
      - source: envs/prod/files/orders/application.properties.j2
        destination: config/application.properties
        action: template
      - source: envs/prod/files/orders/logback.xml
        destination: config/logback.xml
        action: copy
  billing:
    group: billing_nodes
    service_user: myapp
    serial: 1
    hook_timeout: 30
    health_retries: 10
    health_delay: 3
    artifact:
      path: myapp-2026.11.10.2.tar.gz
      sha256: 7c24465b7786d550f6fe988f24b5862a946359edac9d9075bc55e9d032140e05
      bundle_version: 2026.11.10.2
      component_version: 2.13.4
    config:
      values: envs/prod/files/billing/values.yml
      files:
      - source: envs/prod/files/billing/application.properties.j2
        destination: config/application.properties
        action: template
      - source: envs/prod/files/billing/logback.xml
        destination: config/logback.xml
        action: copy
```

#### Source 34

`myapp-config/envs/prod/files/billing/values.yml`

The same contents also appear at these paths.

- `myapp-ansible/tests/example/envs/prod/files/billing/values.yml`

```yaml
parameters:
  db_url: "jdbc:postgresql://prod-db.example.invalid:5432/billing"
  timeout_ms: "5000"
secret_env:
  db_password: MYAPP_SECRET_BILLING_DB_PASSWORD
secret_revision: "1"
```

#### Source 35

`myapp-config/envs/prod/files/orders/values.yml`

The same contents also appear at these paths.

- `myapp-ansible/tests/example/envs/prod/files/orders/values.yml`

```yaml
parameters:
  db_url: "jdbc:postgresql://prod-db.example.invalid:5432/orders"
  timeout_ms: "5000"
secret_env:
  db_password: MYAPP_SECRET_ORDERS_DB_PASSWORD
secret_revision: "1"
```

#### Source 36

`myapp-config/envs/test/deployment.yml`

The same contents also appear at these paths.

- `myapp-ansible/tests/example/envs/test/deployment.yml`

```yaml
environment: test
deployment_order:
- orders
- billing
components:
  orders:
    group: orders_nodes
    service_user: myapp
    serial: 1
    hook_timeout: 30
    health_retries: 10
    health_delay: 3
    artifact:
      path: myapp-2026.11.10.2.tar.gz
      sha256: 7c24465b7786d550f6fe988f24b5862a946359edac9d9075bc55e9d032140e05
      bundle_version: 2026.11.10.2
      component_version: 3.8.1
    config:
      values: envs/test/files/orders/values.yml
      files:
      - source: envs/test/files/orders/application.properties.j2
        destination: config/application.properties
        action: template
      - source: envs/test/files/orders/logback.xml
        destination: config/logback.xml
        action: copy
  billing:
    group: billing_nodes
    service_user: myapp
    serial: 1
    hook_timeout: 30
    health_retries: 10
    health_delay: 3
    artifact:
      path: myapp-2026.11.10.2.tar.gz
      sha256: 7c24465b7786d550f6fe988f24b5862a946359edac9d9075bc55e9d032140e05
      bundle_version: 2026.11.10.2
      component_version: 2.13.4
    config:
      values: envs/test/files/billing/values.yml
      files:
      - source: envs/test/files/billing/application.properties.j2
        destination: config/application.properties
        action: template
      - source: envs/test/files/billing/logback.xml
        destination: config/logback.xml
        action: copy
```

#### Source 37

`myapp-config/envs/test/files/billing/values.yml`

The same contents also appear at these paths.

- `myapp-ansible/tests/example/envs/test/files/billing/values.yml`

```yaml
parameters:
  db_url: "jdbc:postgresql://test-db.example.invalid:5432/billing"
  timeout_ms: "5000"
secret_env:
  db_password: MYAPP_SECRET_BILLING_DB_PASSWORD
secret_revision: "1"
```

#### Source 38

`myapp-config/envs/test/files/orders/values.yml`

The same contents also appear at these paths.

- `myapp-ansible/tests/example/envs/test/files/orders/values.yml`

```yaml
parameters:
  db_url: "jdbc:postgresql://test-db.example.invalid:5432/orders"
  timeout_ms: "5000"
secret_env:
  db_password: MYAPP_SECRET_ORDERS_DB_PASSWORD
secret_revision: "1"
```
