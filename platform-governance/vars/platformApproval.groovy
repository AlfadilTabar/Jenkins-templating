#!/usr/bin/env groovy
/** Manual change gate. Locked stage; only rendered with a when{} guard on
 *  gated environments (uat/prod). Records approver for the audit trail. */
def call(Map args) {
    def approver = input(
        message: "Approve deployment of ${env.APP_NAME} to ${args.env}?",
        ok: 'Deploy',
        submitterParameter: 'APPROVED_BY'
    )
    echo "Deployment to ${args.env} approved by: ${approver}"
    currentBuild.description = "${args.env} approved by ${approver}"
}
