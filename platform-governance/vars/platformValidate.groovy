#!/usr/bin/env groovy
/** Validate checksums of fetched artifacts and lint the manifest shape.
 *  Locked stage - present in every generated pipeline. */
def call(Map args) {
    sh label: 'validate manifest',
       script: "python3 platform-governance/steps/python/validate-manifest.py deploy/${args.env}.yaml"
    sh label: 'validate checksums',
       script: "platform-governance/steps/bash/validate-checksum.sh artifacts/"
}
