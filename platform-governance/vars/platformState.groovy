#!/usr/bin/env groovy
/**
 * platformState - the deployment ledger that makes rollback possible.
 *
 * WHY: the previous design overwrote a single ".last-good" file with the version
 * just deployed, so there was no record of the version BEFORE it - i.e. nothing
 * to roll back TO. This step keeps a proper per-deployable ledger:
 *
 *   .deploy-state/<env>-<id>.current        JSON  - the version currently live
 *   .deploy-state/<env>-<id>.history.jsonl  JSONL - append-only, one line/event
 *   .deploy-state/<env>-<id>.last-good      text  - last SUCCESSFUL version
 *
 * The ledger is the source of truth for:
 *   - Tier 2 one-click rollback  (previousGood -> redeploy)
 *   - Tier 3 audit / full recovery  (recovery-cli.py reads these files)
 *   - "what is running where" reporting
 *
 * PERSISTENCE (air-gapped, workspace may be wiped between builds):
 *   mode=archive (default) - archived as fingerprinted build artifacts every run
 *   mode=git               - committed to a 'deployment-state' branch for a
 *                            durable, reviewable audit trail (needs GIT_PUSH creds)
 *   mode=workspace         - keep in workspace only (dev/testing)
 * Controlled by manifest.stateTracking.mode.
 */

private String dir()                 { return '.deploy-state' }
private String base(env, id)         { return "${dir()}/${env}-${id}" }
private String stamp()               { return new Date().format("yyyy-MM-dd'T'HH:mm:ss'Z'", TimeZone.getTimeZone('UTC')) }
private String who()                 { return (env.BUILD_USER ?: env.CHANGE_AUTHOR ?: 'jenkins') }
private String buildRef()            { return "${env.JOB_NAME ?: 'job'}#${env.BUILD_NUMBER ?: '0'}" }

/** Load the currently-live entry for a deployable, or null. */
def current(String env, String id) {
    String f = "${base(env, id)}.current"
    if (!fileExists(f)) return null
    return readJSON(text: readFile(f))
}

/** All ledger events for a deployable, oldest-first (empty list if none). */
List history(String env, String id) {
    String f = "${base(env, id)}.history.jsonl"
    if (!fileExists(f)) return []
    return readFile(f).readLines().findAll { it?.trim() }.collect { readJSON(text: it) }
}

/**
 * The version to roll back TO: the most recent SUCCESSFUL event whose version
 * differs from what is currently live. Returns a map (the ledger entry) or null
 * if there is no earlier good version on record.
 */
def previousGood(String env, String id) {
    def cur = current(env, id)
    String curVer = cur?.version
    def good = history(env, id).findAll { it.result in ['deployed', 'rolled-back'] }
    // walk newest-first, skip anything equal to the current live version
    for (int i = good.size() - 1; i >= 0; i--) {
        if (good[i].version != curVer) return good[i]
    }
    return null
}

/**
 * Record an event and (on success) advance ".current" and ".last-good".
 * result: 'deployed' | 'rolled-back' | 'failed'
 */
def record(Map a) {
    String env = a.environment
    def    d   = a.deployable
    String id  = d.id
    sh "mkdir -p ${dir()}"

    def prev = current(env, id)
    def entry = [
        ts        : stamp(),
        id        : id,
        version   : a.version,
        fromVersion: prev?.version,
        artifactId: d.artifactId,
        packaging : (d.packaging ?: 'jar'),
        deployPath: d.deployPath,
        target    : (d.targetServers ?: d.serverGroup),
        strategy  : (d.deploymentStrategy ?: 'rolling'),
        result    : (a.result ?: 'deployed'),
        reason    : (a.reason ?: null),
        build     : buildRef(),
        by        : who()
    ]
    // append to the immutable history
    writeFile file: "${base(env, id)}.history.jsonl",
              text: (fileExists("${base(env, id)}.history.jsonl") ? readFile("${base(env, id)}.history.jsonl") : '') +
                    groovy.json.JsonOutput.toJson(entry) + "\n"

    // advance the live pointers only on a good result
    if (entry.result in ['deployed', 'rolled-back']) {
        writeFile file: "${base(env, id)}.current",   text: groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(entry))
        writeFile file: "${base(env, id)}.last-good", text: a.version
    }
    echo "STATE ${env}/${id}: ${entry.fromVersion ?: '(none)'} -> ${a.version} [${entry.result}] by ${entry.by}"
}

/**
 * Restore the ledger into the current workspace before a rollback runs in a
 * fresh job (the manual rollback job does not share the deploy job's workspace).
 *   mode=git     - pull the per-env ledger files from the state branch
 *   mode=archive - copy them from the deploy job's last successful build
 *                  (needs the Copy Artifact plugin + opts.fromJob / DEPLOY_JOB)
 */
def restore(String environment, Map opts = [:]) {
    sh "mkdir -p ${dir()}"
    String mode = opts.mode ?: 'archive'
    if (mode == 'git') {
        String branch = opts.branch ?: 'deployment-state'
        sh label: 'restore ledger from state branch', script: """
            set -e
            git fetch origin ${branch} || { echo "state branch '${branch}' not found"; exit 0; }
            for f in \$(git ls-tree -r --name-only origin/${branch} 2>/dev/null | grep '^${dir()}/${environment}-' || true); do
              mkdir -p \$(dirname "\$f"); git show "origin/${branch}:\$f" > "\$f";
            done
            echo "Restored: \$(ls ${dir()}/${environment}-* 2>/dev/null | wc -l) ledger file(s)."
        """
    } else if (mode == 'archive') {
        String src = opts.fromJob ?: env.DEPLOY_JOB
        if (src) {
            copyArtifacts(projectName: src, filter: "${dir()}/${environment}-*.*",
                          selector: lastSuccessful(), optional: true, flatten: false)
        } else {
            echo "stateTracking.mode=archive but no fromJob/DEPLOY_JOB set; assuming ledger already present."
        }
    }
}

/** Persist the ledger so it survives workspace cleanup. */
def persist(String env, Map opts = [:]) {
    if (!fileExists(dir())) return
    // safety net: always archive, fingerprinted, so any build can be traced
    archiveArtifacts artifacts: "${dir()}/${env}-*.*", allowEmptyArchive: true, fingerprint: true

    String mode = opts.mode ?: 'archive'
    if (mode == 'git') {
        String branch = opts.branch ?: 'deployment-state'
        String cred   = opts.credentialsId ?: 'gitlab-deploy-token'
        echo "Persisting deployment state to branch '${branch}'"
        withCredentials([usernamePassword(credentialsId: cred, usernameVariable: 'GU', passwordVariable: 'GP')]) {
            sh label: 'commit deployment-state', script: """
                set -e
                git config user.email "jenkins@platform"
                git config user.name  "platform-pipeline"
                git add -f ${dir()}/${env}-*.* || true
                if ! git diff --cached --quiet; then
                  git commit -m "chore(state): ${env} deployment ledger [${buildRef()}] [skip ci]"
                  # push ledger onto its own branch without disturbing the working branch
                  git push https://\${GU}:\${GP}@\$(git config --get remote.origin.url | sed -E 's#https?://##') HEAD:${branch}
                else
                  echo "No deployment-state changes to commit."
                fi
            """
        }
    }
}
