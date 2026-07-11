#!/usr/bin/env groovy
/**
 * platformDeploy - runtime deployment engine (selection + ordering).
 *
 * Reads deploy/<env>.yaml and deploys the deployables BOUND TO THIS STAGE,
 * honouring the manifest's dual mode:
 *   - DEPLOY_MODE=targeted -> manifest.targeted.deploy (+ manifest.targeted.undeploy)
 *   - DEPLOY_MODE=full     -> manifest.full.deployables (whole environment)
 *
 * The actual per-deployable execution (secrets, hooks, strategy, playbook,
 * health gate) is delegated to platformApply so that platformRollback runs
 * through the EXACT same path. After each successful apply the deployment ledger
 * (platformState) records the version, enabling one-click rollback and audit.
 *
 * In-play rollback (a health check failing mid-batch) is handled inside the
 * playbook's block/rescue: the affected host restores its own backup and the
 * play fails, stopping a rolling/canary rollout before the bad version spreads.
 *
 * args: env, stage, executor, playbook|script, defaultStrategy
 */
def call(Map args) {
    String environment = args.env
    String stageId     = args.stage ?: 'deploy'
    String executor    = args.executor ?: 'ansible'
    String mode        = (params.DEPLOY_MODE ?: 'targeted')

    def manifest = readYaml file: "deploy/${environment}.yaml"
    String inventory = manifest.inventory
    def stateOpts = (manifest.stateTracking ?: [:])

    // ---- select the deployables for this mode + stage --------------------
    def pool
    if (mode == 'full') {
        pool = manifest.full?.deployables ?: []
    } else {
        pool = manifest.targeted?.deploy ?: []
        if (params.SELECTOR?.trim()) {
            def ids = params.SELECTOR.split(',').collect { it.trim() }
            pool = pool.findAll { it.id in ids }
        }
    }
    def selected = pool.findAll { (it.stage ?: 'deploy') == stageId }
    selected = orderByDependencies(selected)

    if (!selected && !(mode != 'full' && manifest.targeted?.undeploy)) {
        echo "Nothing bound to stage '${stageId}' for mode '${mode}'."
        return
    }

    // ---- undeploy first (targeted mode only) -----------------------------
    if (mode != 'full') {
        (manifest.targeted?.undeploy ?: []).each { u ->
            echo "Undeploying ${u.id} from ${u.deployPath}"
            sh """
                ansible-playbook platform-governance/steps/ansible/undeploy-tomcat.yml \
                  -i ${inventory} -l ${u.serverGroup ?: 'all'} \
                  -e undeploy_path='${u.deployPath}' -e packaging='${u.packaging ?: 'war'}'
            """
        }
    }

    // ---- deploy each selected deployable ---------------------------------
    selected.each { d ->
        String version = params.RELEASE_VERSION?.trim() ? params.RELEASE_VERSION : d.version
        try {
            platformApply(deployable: d, environment: environment, inventory: inventory,
                          executor: executor, playbook: args.playbook, script: args.script,
                          version: version, defaultStrategy: args.defaultStrategy)
            platformState.record(environment: environment, deployable: d, version: version,
                                 result: 'deployed')
        } catch (err) {
            // best-effort failure marker in the ledger, then re-throw to fail the build
            try { platformState.record(environment: environment, deployable: d, version: version,
                                       result: 'failed', reason: err.message) } catch (ignore) {}
            throw err
        }
    }

    // ---- persist the ledger (archive always; git if configured) ----------
    platformState.persist(environment, stateOpts)
}

/* -------- ordering (selection concern lives here) -------- */
private List orderByDependencies(List items) {
    def byId = [:]; items.each { byId[it.id] = it }
    def ordered = []; def placed = [] as Set
    def ready = { d -> (d.dependsOn ?: []).every { it in placed || !(it in byId.keySet()) } }
    def remaining = items.sort { (it.deploymentOrder ?: 100) }
    int guard = 0
    while (remaining && guard++ < 1000) {
        def next = remaining.find { ready(it) } ?: remaining[0]
        ordered << next; placed << next.id; remaining = remaining - next
    }
    return ordered
}
