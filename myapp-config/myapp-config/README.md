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
