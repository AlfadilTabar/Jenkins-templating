#!/usr/bin/env python3
"""
platform-generator
===================
Renders an explicit, auditable Jenkinsfile from an application's TYPE-only
deployment descriptor.

Design contract
---------------
* GENERATION input  = deployment-descriptor.yaml (TYPE + steps.add/steps.remove only)
* RUNTIME input     = deploy/<env>.yaml (inventory + deployables). NEVER read here.
* Hooks are not special: a pre/post action is just an added step (steps.add).
* Profile stages with 'locked: true' cannot be removed by a descriptor
  (mandatory gates -> SAMA change control).
* Deployables in the runtime manifest bind themselves to a stage via `stage:`,
  choose targets (serverGroup / targetServers), a deploymentStrategy, and
  per-deployable pre/post hooks with a runOn location. The generator does not
  need any of that - it only lays out the stages; platformDeploy resolves the
  manifest at runtime.

Deps: PyYAML only (air-gap friendly).
Usage:
  python3 generate.py --descriptor app/deployment-descriptor.yaml \
                      --governance platform-governance --out Jenkinsfile
"""
import argparse, copy, datetime, os, sys
try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML required\n"); sys.exit(2)

GEN_VERSION = "1.0.0"
I = " " * 8

def load_yaml(p):
    with open(p, encoding="utf-8") as fh: return yaml.safe_load(fh)

def validate_descriptor(d):
    e = []
    if not isinstance(d, dict): return ["descriptor empty/not a mapping"]
    if d.get("kind") != "DeploymentDescriptor": e.append("kind must be 'DeploymentDescriptor'")
    if not d.get("type"): e.append("'type' is required")
    if not (d.get("application") or {}).get("name"): e.append("application.name is required")
    return e

def resolve_profile(root, t):
    p = os.path.join(root, "profiles", t + ".yaml")
    if not os.path.isfile(p):
        pdir = os.path.join(root, "profiles")
        av = sorted(f[:-5] for f in os.listdir(pdir)) if os.path.isdir(pdir) else []
        sys.stderr.write("ERROR: no profile '%s'. Available: %s\n" % (t, ", ".join(av))); sys.exit(3)
    return load_yaml(p)

def apply_patches(base, patch):
    stages = copy.deepcopy(base); patch = patch or {}
    for rid in (patch.get("remove") or []):
        m = next((s for s in stages if s.get("id") == rid), None)
        if m is None:
            sys.stderr.write("ERROR: cannot remove '%s' (not in profile)\n" % rid); sys.exit(2)
        if m.get("locked"):
            sys.stderr.write("ERROR: stage '%s' is locked and cannot be removed (mandatory gate)\n" % rid); sys.exit(2)
        stages.remove(m)
    for a in (patch.get("add") or []):
        if not a.get("id"):
            sys.stderr.write("ERROR: added step missing 'id'\n"); sys.exit(2)
        ns = {"id": a["id"], "label": a.get("label", a["id"]),
              "step": "platformStep", "custom": True, "run": a.get("run", "bash")}
        if a.get("script"):   ns["script"] = a["script"]
        if a.get("playbook"): ns["playbook"] = a["playbook"]
        after, before = a.get("after"), a.get("before")
        if after:
            i = next((k for k, s in enumerate(stages) if s.get("id") == after), None)
            if i is None: sys.stderr.write("ERROR: add '%s' after unknown '%s'\n" % (a["id"], after)); sys.exit(2)
            stages.insert(i + 1, ns)
        elif before:
            i = next((k for k, s in enumerate(stages) if s.get("id") == before), None)
            if i is None: sys.stderr.write("ERROR: add '%s' before unknown '%s'\n" % (a["id"], before)); sys.exit(2)
            stages.insert(i, ns)
        else:
            stages.append(ns)
    return stages

def render_stage(st, executor):
    out = [I + "stage('%s') {" % st["label"]]
    if st.get("gatedEnvs"):
        envs = ", ".join("'%s'" % e for e in st["gatedEnvs"])
        out.append(I + "    when { expression { params.TARGET_ENV in [%s] } }" % envs)

    def deploy_call(stage_id):
        args = ["env: params.TARGET_ENV", "stage: '%s'" % stage_id, "executor: '%s'" % executor]
        if st.get("playbook"): args.append("playbook: '%s'" % st["playbook"])
        if st.get("script"):   args.append("script: '%s'" % st["script"])
        if st.get("strategy"): args.append("defaultStrategy: '%s'" % st["strategy"])
        return "platformDeploy(\n" + ",\n".join(I + "        " + a for a in args) + "\n" + I + "    )"

    if st.get("custom"):
        if st.get("run") == "deploy":
            call = deploy_call(st["id"])
        else:
            a = ["name: '%s'" % st["id"], "run: '%s'" % st.get("run", "bash")]
            if st.get("script"):   a.append("script: '%s'" % st["script"])
            if st.get("playbook"): a.append("playbook: '%s'" % st["playbook"])
            a.append("env: params.TARGET_ENV")
            call = "platformStep(" + ", ".join(a) + ")"
    elif st["step"] == "platformDeploy":
        call = deploy_call(st["id"])
    else:
        call = "%s(env: params.TARGET_ENV)" % st["step"]

    out.append(I + "    steps { %s }" % call)
    out.append(I + "}")
    return "\n".join(out)

def render_post(profile):
    out, rb = [], (profile.get("rollback") or {})
    if rb.get("onFailure"):
        executor = (profile.get("metadata") or {}).get("executor", "ansible")
        # Locate the primary deploy stage so auto-rollback re-applies via the
        # same executor + playbook/script as the forward deploy.
        pb = sc = None
        for s in profile.get("stages", []):
            if s.get("step") == "platformDeploy" and (s.get("playbook") or s.get("script")):
                pb, sc = s.get("playbook"), s.get("script"); break
        args = ["env: params.TARGET_ENV", "scope: 'failed'", "executor: '%s'" % executor]
        if pb: args.append("playbook: '%s'" % pb)
        if sc: args.append("script: '%s'" % sc)
        args.append("reason: 'auto-rollback on pipeline failure'")
        call = "%s(%s)" % (rb.get("step", "platformRollback"), ", ".join(args))
        out += [I + "failure {", I + "    " + call, I + "}"]
    out += [I + "always {", I + "    platformNotify(result: currentBuild.currentResult)", I + "}"]
    return "\n".join(out)

def render(descriptor_path, governance_root, template_path=None):
    d = load_yaml(descriptor_path)
    errs = validate_descriptor(d)
    if errs: sys.stderr.write("ERROR: invalid descriptor:\n  - " + "\n  - ".join(errs) + "\n"); sys.exit(2)
    t = d["type"]; profile = resolve_profile(governance_root, t)
    executor = (profile.get("metadata") or {}).get("executor", "ansible")
    agent = profile.get("agent", "linux-deploy"); app = d["application"]
    stages = apply_patches(profile.get("stages", []), d.get("steps"))
    if template_path is None:
        template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "Jenkinsfile.tmpl")
    template = open(template_path, encoding="utf-8").read()
    stages_text = "\n".join(render_stage(s, executor) for s in stages)
    post_text = render_post(profile)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (template.replace("@APP_NAME@", str(app.get("name", "")))
            .replace("@APP_TEAM@", str(app.get("team", "")))
            .replace("@APP_TYPE@", str(t)).replace("@AGENT@", str(agent))
            .replace("@GENERATED_AT@", ts).replace("@GEN_VERSION@", GEN_VERSION)
            .replace("@STAGES@", stages_text).replace("@POST@", post_text))

def render_rollback(descriptor_path, governance_root, template_path=None):
    """Render the one-click rollback job (Tier 2) for an application."""
    d = load_yaml(descriptor_path)
    errs = validate_descriptor(d)
    if errs: sys.stderr.write("ERROR: invalid descriptor:\n  - " + "\n  - ".join(errs) + "\n"); sys.exit(2)
    t = d["type"]; profile = resolve_profile(governance_root, t)
    executor = (profile.get("metadata") or {}).get("executor", "ansible")
    agent = profile.get("agent", "linux-deploy"); app = d["application"]
    # find the primary deploy stage's playbook/script to re-apply through
    tgt = ""
    for s in profile.get("stages", []):
        if s.get("step") == "platformDeploy" and (s.get("playbook") or s.get("script")):
            if s.get("playbook"): tgt = ",\n                    playbook: '%s'" % s["playbook"]
            elif s.get("script"): tgt = ",\n                    script: '%s'" % s["script"]
            break
    if template_path is None:
        template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "Jenkinsfile.rollback.tmpl")
    template = open(template_path, encoding="utf-8").read()
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (template.replace("@APP_NAME@", str(app.get("name", "")))
            .replace("@APP_TEAM@", str(app.get("team", "")))
            .replace("@APP_TYPE@", str(t)).replace("@AGENT@", str(agent))
            .replace("@EXECUTOR@", str(executor))
            .replace("@GENERATED_AT@", ts).replace("@GEN_VERSION@", GEN_VERSION)
            .replace("@ROLLBACK_TARGET@", tgt))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--descriptor", required=True); ap.add_argument("--governance", required=True)
    ap.add_argument("--template", default=None); ap.add_argument("--out", default=None)
    ap.add_argument("--rollback", action="store_true",
                    help="Generate the one-click rollback job instead of the deploy pipeline")
    a = ap.parse_args()
    r = render_rollback(a.descriptor, a.governance, a.template) if a.rollback \
        else render(a.descriptor, a.governance, a.template)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(r); sys.stderr.write("Wrote %s\n" % a.out)
    else:
        sys.stdout.write(r)

if __name__ == "__main__":
    main()
