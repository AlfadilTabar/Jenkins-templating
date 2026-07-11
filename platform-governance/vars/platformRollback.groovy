#!/usr/bin/env groovy
/**
 * platformRollback - restore a previous good version.
 *
 * A rollback is a deploy of an older, known-good version: it runs through the
 * SAME platformApply path (secrets, hooks, strategy, health gate) as a forward
 * deploy, so a rolled-back release is verified exactly like any other.
 *
 * Two entry points:
 *
 *  (1) AUTOMATIC (from post{ failure{} } in the generated pipeline):
 *        platformRollback(env: TARGET_ENV, scope: 'release'|'failed',
 *                         executor: 'ansible', playbook: '<profile playbook>')
 *      Rolls the deployables touched by THIS build back to their previous good
 *      version. Governed by the profile's rollback.onFailure flag; the caller
 *      only invokes this when that flag is true.
 *
 *  (2) MANUAL one-click (the Jenkinsfile.rollback job):
 *        platformRollback(env: TARGET_ENV, ids: ['payment-api'] or 'all',
 *                         toVersion: '',  // blank = previous good
 *                         executor: 'ansible', playbook: '<profile playbook>',
 *                         reason: 'ticket CHG-1234')
 *
 * Target version per deployable = toVersion if given, else platformState
 * .previousGood(). If no earlier good version is on record, that deployable is
 * skipped with a clear message (never a silent no-op).
 *
 * args: env, executor, playbook|script, ids|scope, toVersion, reason, defaultStrategy
 */
def call(Map args) {
    String environment = args.env
    String executor    = args.executor ?: 'ansible'
    String reason      = args.reason ?: (params.ROLLBACK_REASON ?: 'rollback')
    String toVersion   = (args.toVersion ?: params.TO_VERSION ?: '')?.trim()

    def manifest  = readYaml file: "deploy/${environment}.yaml"
    String inventory = manifest.inventory
    def stateOpts = (manifest.stateTracking ?: [:])
    def byId      = indexDeployables(manifest)

    def ids = resolveIds(args, environment, byId.keySet() as List)
    if (!ids) { echo "platformRollback: nothing to roll back for '${environment}'."; return }

    echo "ROLLBACK ${environment}: ${ids.join(', ')} (reason: ${reason})"
    def rolled = []; def skipped = []

    ids.each { id ->
        def d = byId[id]
        if (!d) { skipped << "${id} (no spec in manifest)"; return }

        String target = toVersion
        if (!target) {
            def prev = platformState.previousGood(environment, id)
            target = prev?.version
        }
        if (!target) { skipped << "${id} (no previous good version on record)"; return }

        def cur = platformState.current(environment, id)
        if (cur?.version == target) { skipped << "${id} (already at ${target})"; return }

        echo "  ${id}: ${cur?.version ?: '(unknown)'} -> ${target}"
        fetchVersion(d, target)                       // pull the rollback target from Nexus
        platformApply(deployable: d, environment: environment, inventory: inventory,
                      executor: executor, playbook: args.playbook, script: args.script,
                      version: target, defaultStrategy: args.defaultStrategy)
        platformState.record(environment: environment, deployable: d, version: target,
                             result: 'rolled-back', reason: reason)
        rolled << "${id}@${target}"
    }

    platformState.persist(environment, stateOpts)

    echo "ROLLBACK SUMMARY [${environment}] rolled=[${rolled.join(', ')}] skipped=[${skipped.join('; ')}]"
    if (!rolled && skipped) {
        echo "No deployables were rolled back. Review the skip reasons above."
    }
}

/* ---------------- helpers ---------------- */

/** Merge targeted.deploy + full.deployables into id -> spec (full wins on overlap). */
private Map indexDeployables(manifest) {
    def m = [:]
    (manifest.targeted?.deploy ?: []).each { m[it.id] = it }
    (manifest.full?.deployables ?: []).each { m[it.id] = it }   // prefer the fuller definition
    return m
}

/**
 * Decide which deployable ids to roll back:
 *  - explicit args.ids (list) or 'all'
 *  - else params.SELECTOR ('all' or comma list)
 *  - else scope 'failed'  -> ids whose latest ledger event failed in THIS build
 *  - else scope 'release' -> ids touched by THIS build (any result)
 *  - else -> everything currently live in the environment
 */
private List resolveIds(Map args, String environment, List known) {
    String buildRef = "${env.JOB_NAME ?: 'job'}#${env.BUILD_NUMBER ?: '0'}"

    def explicit = args.ids ?: (params.SELECTOR?.trim() ?: null)
    if (explicit) {
        if (explicit == 'all' || explicit == ['all']) return liveIds(environment, known)
        return (explicit instanceof List) ? explicit : explicit.split(',').collect { it.trim() }
    }

    String scope = args.scope ?: 'live'
    if (scope in ['failed', 'release']) {
        def hit = []
        known.each { id ->
            def h = platformState.history(environment, id)
            if (!h) return
            def last = h[-1]
            if (last.build == buildRef && (scope == 'release' || last.result == 'failed')) hit << id
        }
        return hit
    }
    return liveIds(environment, known)
}

/** ids that currently have a live version recorded. */
private List liveIds(String environment, List known) {
    return known.findAll { platformState.current(environment, it) != null }
}

/** Pull a specific version of a deployable from Nexus onto the agent. */
private void fetchVersion(d, String version) {
    sh 'mkdir -p artifacts'
    sh label: "fetch ${d.id}@${version}",
       script: "platform-governance/steps/bash/fetch-artifact.sh " +
               "'${d.artifactId}' '${version}' '${d.packaging ?: 'jar'}' 'artifacts/'"
}
