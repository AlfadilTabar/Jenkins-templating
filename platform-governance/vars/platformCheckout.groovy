#!/usr/bin/env groovy
/** Checkout the app repo and load the per-environment runtime manifest.
 *  Stores the parsed manifest on the build so later steps reuse it. */
def call(Map args) {
    String env = args.env
    checkout scm
    String manifestPath = "deploy/${env}.yaml"
    if (!fileExists(manifestPath)) {
        error "Runtime manifest not found: ${manifestPath}"
    }
    def manifest = readYaml file: manifestPath
    // cache for later stages
    env.PLATFORM_MANIFEST_PATH = manifestPath
    echo "Loaded ${manifestPath}: ${manifest.deployables?.size() ?: 0} deployable(s), inventory=${manifest.inventory}"
    return manifest
}
