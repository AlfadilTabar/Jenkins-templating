#!/usr/bin/env groovy
/** Generic runner for an ADDED step (steps.add in the descriptor). Runs an
 *  arbitrary script or playbook. This is how "hooks" are modelled now - there
 *  is no special hook concept, just added steps. */
def call(Map args) {
    String run = args.run ?: 'bash'
    String what = args.script ?: args.playbook
    echo "Custom step '${args.name}' (run=${run}): ${what}"
    switch (run) {
        case 'bash':       sh "chmod +x ${args.script}; ./${args.script}"; break
        case 'python':     sh "python3 ${args.script}"; break
        case 'powershell': sh "pwsh ${args.script}"; break
        case 'ansible':    sh "ansible-playbook ${args.playbook}"; break
        default:           error "Unknown run type '${run}' for step ${args.name}"
    }
}
