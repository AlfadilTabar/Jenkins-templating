#!/usr/bin/env groovy
/** Build-Once-Deploy-Many: pull each deployable's exact version from Nexus onto
 *  the agent (workspace 'artifacts/'). Honours DEPLOY_MODE (targeted vs full)
 *  and SELECTOR, mirroring platformDeploy so only what will be deployed is
 *  fetched. RELEASE_VERSION, if set, overrides every version so the SAME build
 *  is promoted across environments. */
def call(Map args) {
    def m = readYaml file: "deploy/${args.env}.yaml"
    String mode = (params.DEPLOY_MODE ?: 'targeted')

    def pool
    if (mode == 'full') {
        pool = m.full?.deployables ?: []
    } else {
        pool = m.targeted?.deploy ?: []
        if (params.SELECTOR?.trim()) {
            def ids = params.SELECTOR.split(',').collect { it.trim() }
            pool = pool.findAll { it.id in ids }
        }
    }

    sh 'mkdir -p artifacts'
    pool.each { d ->
        String version = params.RELEASE_VERSION?.trim() ? params.RELEASE_VERSION : d.version
        echo "Fetching ${d.artifactId}:${version} (${d.packaging ?: 'jar'})"
        sh label: "fetch ${d.id}",
           script: "platform-governance/steps/bash/fetch-artifact.sh " +
                   "'${d.artifactId}' '${version}' '${d.packaging ?: 'jar'}' 'artifacts/'"
    }
}
