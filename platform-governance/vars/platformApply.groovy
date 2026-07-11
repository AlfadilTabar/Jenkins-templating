#!/usr/bin/env groovy
/**
 * platformApply - apply a SINGLE deployable at a SPECIFIC version.
 *
 * This is the shared execution core used by BOTH platformDeploy (forward
 * deploys) and platformRollback (restore a previous version). Keeping it in one
 * place guarantees a rollback runs through the exact same path, hooks, secrets,
 * strategy and health gate as a normal deploy - a rollback is just a deploy of
 * an older, known-good version.
 *
 * ctx keys:
 *   deployable   - the manifest entry (id, artifactId, deployPath, config, ...)
 *   environment  - dev|uat|prod
 *   inventory    - ansible inventory path
 *   executor     - 'ansible' | 'powershell'
 *   playbook     - ansible playbook path      (executor=ansible)
 *   script       - powershell script path     (executor=powershell)
 *   version      - the EXACT version to apply (deploy target or rollback target)
 *   defaultStrategy (optional)
 *
 * Throws on failure so the caller (and Jenkins) sees a red stage; the playbook's
 * own block/rescue performs the in-play per-host restore before that happens.
 */
def call(Map ctx) {
    def    d          = ctx.deployable
    String environment = ctx.environment
    String inventory   = ctx.inventory
    String executor    = ctx.executor ?: 'ansible'
    String version     = ctx.version ?: d.version
    String strategy    = d.deploymentStrategy ?: (ctx.defaultStrategy ?: 'rolling')
    String target      = resolveTarget(d)
    def    serialSpec  = serialFor(strategy)
    String name        = d.artifactId.split(':')[1]
    String pkg         = d.packaging ?: 'jar'
    String artifact    = "artifacts/${name}-${version}.${pkg}"

    echo "APPLY ${d.id} v${version} -> ${target} [strategy=${strategy}]"

    def vars = [deployable: d, artifact_local: artifact, target_limit: target,
                serial_spec: serialSpec, inventory: inventory, applied_version: version]
    writeJSON file: "deploy-vars-${d.id}.json", json: vars, pretty: 2

    withDeployableSecrets(d) {
        runHooks(d.preHooks, d, inventory, 'pre')
        if (executor == 'ansible') {
            sh label: "apply ${d.id}", script: """
                ansible-playbook ${ctx.playbook} \
                  -i ${inventory} -l ${target} \
                  -e @deploy-vars-${d.id}.json
            """
        } else if (executor == 'powershell') {
            sh label: "apply ${d.id}", script: """
                pwsh ${ctx.script} \
                  -VarsFile deploy-vars-${d.id}.json \
                  -ArtifactLocal '${artifact}' \
                  -Inventory '${target}' -Strategy '${strategy}'
            """
        } else {
            error "platformApply: unsupported executor '${executor}'"
        }
        runHooks(d.postHooks, d, inventory, 'post')
    }
}

/* ---------------- helpers (shared execution mechanics) ---------------- */

private def serialFor(String strategy) {
    switch (strategy) {
        case 'rolling':     return 1
        case 'all-at-once': return '100%'
        case 'canary':      return [1, '100%']   // one host first, then the rest
        case 'blue-green':  return '100%'         // playbook/router handles the swap
        default:            return 1
    }
}

private String resolveTarget(d) {
    if (d.targetServers) {
        return (d.targetServers instanceof List) ? d.targetServers.join(',') : d.targetServers.toString()
    }
    return d.serverGroup
}

private void withDeployableSecrets(d, Closure body) {
    def bindings = (d.config?.jenkinsSecrets ?: []).collect {
        string(credentialsId: it.credentialId, variable: it.envVar)
    }
    if (bindings) {
        withCredentials(bindings) {
            def secretMap = [:]
            (d.config.jenkinsSecrets).each { secretMap[it.envVar] = env[it.envVar] }
            withEnv(["APP_SECRETS_JSON=${groovy.json.JsonOutput.toJson(secretMap)}"]) { body() }
        }
    } else {
        body()
    }
}

private void runHooks(hooks, d, inventory, phase) {
    (hooks ?: []).each { h ->
        String runOn = h.runOn ?: 'local'
        String what  = h.script ?: h.playbook
        echo "  ${phase}-hook ${d.id}: ${what} (runOn=${runOn})"
        if (runOn == 'local') {
            if (h.script)   { sh "chmod +x ${h.script}; ./${h.script}" }
            if (h.playbook) { sh "ansible-playbook -i localhost, -c local ${h.playbook}" }
            return
        }
        String limit
        if (runOn == 'serverGroup')                   limit = d.serverGroup
        else if (runOn == 'allServers')               limit = 'all'
        else if (runOn.startsWith('specificServer:')) limit = runOn.split(':', 2)[1].trim()
        else                                          limit = d.serverGroup
        if (h.playbook)    sh "ansible-playbook -i ${inventory} -l ${limit} ${h.playbook}"
        else if (h.script) sh "ansible ${limit} -i ${inventory} -m script -a '${h.script}'"
    }
}
