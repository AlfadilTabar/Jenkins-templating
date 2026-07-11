#!/usr/bin/env groovy
def call(Map args) {
    sh label: 'smoke test',
       script: "platform-governance/steps/bash/smoke-test.sh ${args.env}"
}
