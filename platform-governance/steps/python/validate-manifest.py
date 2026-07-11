#!/usr/bin/env python3
"""Lint a runtime deployment manifest (deploy/<env>.yaml).
Validates targeted.deploy / targeted.undeploy / full.deployables against the
platform rules. Exit non-zero on any error."""
import sys, yaml
VALID_STRATEGY = {"rolling", "all-at-once", "blue-green", "canary"}
VALID_PKG = {"jar", "war", "ear", "whl", "dll", "exe"}
VALID_STATE_MODE = {"archive", "git", "workspace"}
VALID_STAGE_MODE = {"nostage", "stage", "external_stage"}

def err(msg): print("MANIFEST ERROR:", msg); return 1

def check_deployable(d, seen_ids):
    rc = 0
    did = d.get("id", "<no id>")
    for req in ("id", "artifactId", "version", "deployPath"):
        if not d.get(req): rc |= err(f"{did}: missing '{req}'")
    if ":" not in str(d.get("artifactId", "")): rc |= err(f"{did}: artifactId must be group:name")
    if not (d.get("serverGroup") or d.get("targetServers")):
        rc |= err(f"{did}: needs serverGroup or targetServers")
    if d.get("packaging") and d["packaging"] not in VALID_PKG:
        rc |= err(f"{did}: invalid packaging '{d['packaging']}'")
    strat = d.get("deploymentStrategy", "rolling")
    if strat not in VALID_STRATEGY: rc |= err(f"{did}: invalid deploymentStrategy '{strat}'")
    for dep in (d.get("dependsOn") or []):
        if dep not in seen_ids: rc |= err(f"{did}: dependsOn '{dep}' not defined in this section")
    hc = d.get("healthCheck") or {}
    if hc.get("enabled", True) and not hc.get("endpoint"):
        rc |= err(f"{did}: healthCheck.enabled but no endpoint")
    for s in ((d.get("config") or {}).get("jenkinsSecrets") or []):
        if not (s.get("credentialId") and s.get("envVar")):
            rc |= err(f"{did}: jenkinsSecret needs credentialId and envVar")
    for phase in ("preHooks", "postHooks"):
        for h in (d.get(phase) or []):
            if not (h.get("script") or h.get("playbook")):
                rc |= err(f"{did}.{phase}: hook needs script or playbook")
            ro = h.get("runOn", "local")
            if ro not in ("local", "serverGroup", "allServers") and not ro.startswith("specificServer:"):
                rc |= err(f"{did}.{phase}: invalid runOn '{ro}'")
    wl = d.get("weblogic")
    if wl is not None:
        if not wl.get("adminUrl"): rc |= err(f"{did}.weblogic: adminUrl required (t3://host:port)")
        if not wl.get("targets"):  rc |= err(f"{did}.weblogic: targets required (Managed Server or Cluster)")
        sm = wl.get("stageMode", "nostage")
        if sm not in VALID_STAGE_MODE: rc |= err(f"{did}.weblogic: invalid stageMode '{sm}'")
    return rc

def check_section(deployables):
    rc = 0
    ids = {d.get("id") for d in deployables}
    for d in deployables: rc |= check_deployable(d, ids)
    return rc

def main(path):
    m = yaml.safe_load(open(path, encoding="utf-8"))
    rc = 0
    if not m: return err("empty manifest")
    if not m.get("inventory"): rc |= err("missing top-level 'inventory'")
    if "targeted" not in m and "full" not in m:
        rc |= err("manifest must define 'targeted' and/or 'full'")
    tgt = m.get("targeted") or {}
    if tgt.get("deploy"):   rc |= check_section(tgt["deploy"])
    for u in (tgt.get("undeploy") or []):
        if not (u.get("id") and u.get("deployPath")):
            rc |= err(f"undeploy item needs id and deployPath: {u}")
    if (m.get("full") or {}).get("deployables"):
        rc |= check_section(m["full"]["deployables"])
    st = m.get("stateTracking")
    if st is not None:
        mode = st.get("mode", "archive")
        if mode not in VALID_STATE_MODE: rc |= err(f"stateTracking: invalid mode '{mode}'")
        if mode == "git" and not st.get("credentialsId"):
            rc |= err("stateTracking.mode=git requires credentialsId")
    if rc == 0:
        nt = len((tgt.get("deploy") or [])); nf = len((m.get("full") or {}).get("deployables") or [])
        print(f"Manifest {path} OK (targeted.deploy={nt}, full={nf})")
    return rc

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "deploy/dev.yaml"))
