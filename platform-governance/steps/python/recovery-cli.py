#!/usr/bin/env python3
"""
recovery-cli.py - audit and recovery over the deployment ledger (.deploy-state).

The ledger written by platformState is the source of truth for what is running,
what ran before, and what to recover to. This CLI is the operator-facing,
air-gapped (stdlib-only) view of it - no Jenkins required.

Ledger files (per environment + deployable id):
  .deploy-state/<env>-<id>.current        JSON  - currently live version
  .deploy-state/<env>-<id>.history.jsonl  JSONL - append-only event log

Commands:
  status  <env>                 what is live now (version, when, by, build)
  history <env> [id]            full timeline of deploy/rollback events
  plan    <env> [id]            show current -> previous good (rollback preview)
  recover <env> --manifest F    emit a 'full'-mode manifest pinned to the
                                CURRENTLY LIVE versions, for a DR rebuild to the
                                last known-good state (needs PyYAML)

Examples:
  python3 recovery-cli.py status prod
  python3 recovery-cli.py history prod payment-api
  python3 recovery-cli.py plan prod
  python3 recovery-cli.py recover prod --manifest deploy/prod.yaml > deploy/prod.recovery.yaml
"""
import argparse, glob, json, os, sys

STATE_DIR = os.environ.get("STATE_DIR", ".deploy-state")


def _ids(env):
    out = []
    for f in sorted(glob.glob(os.path.join(STATE_DIR, f"{env}-*.current"))):
        out.append(os.path.basename(f)[len(env) + 1:-len(".current")])
    return out


def _current(env, cid):
    f = os.path.join(STATE_DIR, f"{env}-{cid}.current")
    return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else None


def _history(env, cid):
    f = os.path.join(STATE_DIR, f"{env}-{cid}.history.jsonl")
    if not os.path.exists(f):
        return []
    return [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]


def _previous_good(env, cid):
    cur = _current(env, cid)
    curver = cur.get("version") if cur else None
    good = [e for e in _history(env, cid) if e.get("result") in ("deployed", "rolled-back")]
    for e in reversed(good):
        if e.get("version") != curver:
            return e
    return None


def cmd_status(a):
    ids = _ids(a.env)
    if not ids:
        print(f"No live deployments recorded for '{a.env}'."); return 0
    print(f"LIVE in {a.env}:")
    print(f"  {'deployable':22} {'version':16} {'deployed (UTC)':22} {'by':12} build")
    for cid in ids:
        c = _current(a.env, cid) or {}
        print(f"  {cid:22} {str(c.get('version')):16} {str(c.get('ts')):22} "
              f"{str(c.get('by')):12} {c.get('build','')}")
    return 0


def cmd_history(a):
    ids = [a.id] if a.id else _ids(a.env)
    for cid in ids:
        h = _history(a.env, cid)
        if not h:
            continue
        print(f"\n{a.env}/{cid}:")
        for e in h:
            arrow = f"{e.get('fromVersion') or '(none)'} -> {e.get('version')}"
            reason = f"  reason={e['reason']}" if e.get("reason") else ""
            print(f"  {e.get('ts'):22} {e.get('result'):11} {arrow:24} "
                  f"by {e.get('by')} [{e.get('build')}]{reason}")
    return 0


def cmd_plan(a):
    ids = [a.id] if a.id else _ids(a.env)
    any_rows = False
    for cid in ids:
        cur = _current(a.env, cid)
        prev = _previous_good(a.env, cid)
        if not cur:
            continue
        any_rows = True
        tgt = prev.get("version") if prev else "(no previous good version on record)"
        print(f"  {a.env}/{cid}: {cur.get('version')} -> {tgt}")
    if not any_rows:
        print(f"Nothing live to plan a rollback for in '{a.env}'.")
    return 0


def cmd_recover(a):
    try:
        import yaml
    except ImportError:
        sys.stderr.write("recover needs PyYAML (pip install pyyaml)\n"); return 2
    m = yaml.safe_load(open(a.manifest, encoding="utf-8"))
    # Base the recovery set on full.deployables (the whole-environment definition),
    # falling back to targeted.deploy, and pin each to the currently-live version.
    base = ((m.get("full") or {}).get("deployables")
            or (m.get("targeted") or {}).get("deploy") or [])
    live = {cid: (_current(a.env, cid) or {}).get("version") for cid in _ids(a.env)}
    pinned = []
    for d in base:
        d = dict(d)
        if d.get("id") in live and live[d["id"]]:
            d["version"] = live[d["id"]]
        pinned.append(d)
    out = {
        "apiVersion": m.get("apiVersion", "platform/v1"),
        "kind": "DeploymentManifest",
        "metadata": {"environment": a.env,
                     "description": f"RECOVERY manifest - {a.env} pinned to last known-good live versions"},
        "inventory": m.get("inventory"),
        "full": {"deployables": pinned},
    }
    if m.get("stateTracking"):
        out["stateTracking"] = m["stateTracking"]
    yaml.safe_dump(out, sys.stdout, sort_keys=False, default_flow_style=False)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Audit & recovery over the deployment ledger.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status");  p.add_argument("env"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("history"); p.add_argument("env"); p.add_argument("id", nargs="?"); p.set_defaults(fn=cmd_history)
    p = sub.add_parser("plan");    p.add_argument("env"); p.add_argument("id", nargs="?"); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("recover"); p.add_argument("env"); p.add_argument("--manifest", required=True); p.set_defaults(fn=cmd_recover)
    a = ap.parse_args()
    if not os.path.isdir(STATE_DIR):
        sys.stderr.write(f"No ledger directory '{STATE_DIR}'. Set STATE_DIR or run from the workspace.\n")
        return 1
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
