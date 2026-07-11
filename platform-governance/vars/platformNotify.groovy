#!/usr/bin/env groovy
def call(Map args) {
    echo "Pipeline result for ${env.APP_NAME}: ${args.result}"
    // wire to email/Slack/Teams as needed
}
