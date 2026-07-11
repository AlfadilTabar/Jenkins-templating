#!/usr/bin/env groovy
/**
 * generateJenkinsfile - Jenkins-native, air-gapped Jenkinsfile generator.
 *
 * Reads a TYPE-only deployment-descriptor.yaml, resolves the matching profile,
 * applies the descriptor's steps.add / steps.remove (refusing to remove locked
 * stages), renders an explicit Jenkinsfile, and ARCHIVES it as a build artifact.
 * It does not commit - a human reviews the artifact and commits it (review gate).
 *
 * Requires the "Pipeline Utility Steps" plugin (readYaml/readFile/writeFile).
 * The governance repo is expected to be checked out in the workspace so the
 * template and profiles can be read directly (no duplicated resources).
 */
def call(Map args) {
    String descriptorPath = args.descriptor ?: 'deployment-descriptor.yaml'
    String governanceRoot = args.governance ?: 'platform-governance'
    String outPath        = args.out        ?: 'Jenkinsfile.generated'
    String genVersion     = '1.0.0'

    def desc = readYaml file: descriptorPath
    validateDescriptor(desc)

    String appType = desc.type
    String profilePath = "${governanceRoot}/profiles/${appType}.yaml"
    if (!fileExists(profilePath)) { error "No profile '${appType}' (${profilePath})." }
    def profile  = readYaml file: profilePath
    String executor = (profile.metadata?.executor ?: 'ansible')
    String agent    = (profile.agent ?: 'linux-deploy')
    def app         = desc.application ?: [:]

    def stages = applyPatches(profile.stages, desc.steps)

    List<String> blocks = stages.collect { renderStage(it, executor) }
    String stagesText = blocks.join('\n')
    String postText   = renderPost(profile)

    String template = readFile file: "${governanceRoot}/generator/templates/Jenkinsfile.tmpl"
    String ts = new Date().format("yyyy-MM-dd'T'HH:mm:ss'Z'", TimeZone.getTimeZone('UTC'))

    String out = template
        .replace('@APP_NAME@',     (app.name ?: '').toString())
        .replace('@APP_TEAM@',     (app.team ?: '').toString())
        .replace('@APP_TYPE@',     appType)
        .replace('@AGENT@',        agent)
        .replace('@GENERATED_AT@', ts)
        .replace('@GEN_VERSION@',  genVersion)
        .replace('@STAGES@',       stagesText)
        .replace('@POST@',         postText)

    writeFile file: outPath, text: out
    archiveArtifacts artifacts: outPath, fingerprint: true
    echo "Generated ${outPath} for ${app.name} (type=${appType}). Review the artifact, then commit it."
    return outPath
}

private void validateDescriptor(desc) {
    List<String> e = []
    if (desc == null)                          e << 'descriptor empty'
    if (desc?.kind != 'DeploymentDescriptor')  e << "kind must be 'DeploymentDescriptor'"
    if (!desc?.type)                           e << "'type' is required"
    if (!desc?.application?.name)              e << 'application.name is required'
    if (e) error "Invalid descriptor:\n  - " + e.join('\n  - ')
}

private List applyPatches(base, patch) {
    List stages = base.collect { it.clone() }
    patch = patch ?: [:]
    (patch.remove ?: []).each { rid ->
        def m = stages.find { it.id == rid }
        if (m == null) error "Cannot remove '${rid}' - not in profile"
        if (m.locked)  error "Stage '${rid}' is locked and cannot be removed (mandatory gate)"
        stages.remove(m)
    }
    (patch.add ?: []).each { a ->
        if (!a.id) error "Added step missing 'id'"
        def ns = [id: a.id, label: (a.label ?: a.id), step: 'platformStep',
                  custom: true, run: (a.run ?: 'bash')]
        if (a.script)   ns.script = a.script
        if (a.playbook) ns.playbook = a.playbook
        if (a.after) {
            int i = stages.findIndexOf { it.id == a.after }
            if (i < 0) error "add '${a.id}' after unknown '${a.after}'"
            stages.add(i + 1, ns)
        } else if (a.before) {
            int i = stages.findIndexOf { it.id == a.before }
            if (i < 0) error "add '${a.id}' before unknown '${a.before}'"
            stages.add(i, ns)
        } else {
            stages << ns
        }
    }
    return stages
}

private String renderStage(st, String executor) {
    String I = ' ' * 8
    List<String> out = ["${I}stage('${st.label}') {"]
    if (st.gatedEnvs) {
        String envs = st.gatedEnvs.collect { "'${it}'" }.join(', ')
        out << "${I}    when { expression { params.TARGET_ENV in [${envs}] } }"
    }
    Closure deployCall = { String stageId ->
        List<String> a = ["env: params.TARGET_ENV", "stage: '${stageId}'", "executor: '${executor}'"]
        if (st.playbook) a << "playbook: '${st.playbook}'"
        if (st.script)   a << "script: '${st.script}'"
        if (st.strategy) a << "defaultStrategy: '${st.strategy}'"
        return "platformDeploy(\n" + a.collect { "${I}        ${it}" }.join(',\n') + "\n${I}    )"
    }
    String call
    if (st.custom) {
        if (st.run == 'deploy') {
            call = deployCall(st.id)
        } else {
            List<String> a = ["name: '${st.id}'", "run: '${st.run ?: 'bash'}'"]
            if (st.script)   a << "script: '${st.script}'"
            if (st.playbook) a << "playbook: '${st.playbook}'"
            a << "env: params.TARGET_ENV"
            call = "platformStep(${a.join(', ')})"
        }
    } else if (st.step == 'platformDeploy') {
        call = deployCall(st.id)
    } else {
        call = "${st.step}(env: params.TARGET_ENV)"
    }
    out << "${I}    steps { ${call} }"
    out << "${I}}"
    return out.join('\n')
}

private String renderPost(profile) {
    String I = ' ' * 8
    List<String> out = []
    def rb = profile.rollback ?: [:]
    if (rb.onFailure) {
        out << "${I}failure {"
        out << "${I}    ${rb.step ?: 'platformRollback'}(env: params.TARGET_ENV)"
        out << "${I}}"
    }
    out << "${I}always {"
    out << "${I}    platformNotify(result: currentBuild.currentResult)"
    out << "${I}}"
    return out.join('\n')
}
