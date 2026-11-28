# MyApp component configuration

Commit approved deployment descriptors and settings here. The companion `myapp-component-ansible` repository pins operations to a Git commit in this project.

Each `envs/<environment>/deployment.yml` starts with the deployment policy. `mode` sets the default. Selective mode uses the names under `selective.components`. Full mode uses every entry in the component catalog. Stages list components in order, and `max_parallel` controls concurrent components within each host wave. The top-level host waves group inventory aliases and are inherited by components unless they define their own `host_waves`.

The `components` mapping pins each component's vendor version, Nexus path and SHA-256. It also selects its inventory group, hook execution settings and configuration files. Edit the environment's files and pins through merge requests. When replacing hostnames, update the Ansible inventory and the descriptor together.

A `template` mapping renders a Jinja2 source with the values from `values.yml`. A `copy` mapping installs a static source such as `logback.xml`. Quote normal property values as strings and pass passwords through the named `MYAPP_SECRET_*` variables. Increment the component's `secret_revision` when rotating any referenced secret.

Package pins and hostnames in this project are examples. A promotion candidate replaces selected package pins while retaining the target descriptor's configured modes, stages, host waves and properties.
