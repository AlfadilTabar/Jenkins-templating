#!/usr/bin/env python3
"""Resolve Git and individual Nexus packages, then run the Ansible playbooks."""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import javaproperties
import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from archive import extract, relative_path
from rollout import activate_stages, positive, read_deployment, require, schedule

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS = ("dev", "test", "prod", "dr")
PROMOTION_SOURCE = dict(zip(ENVIRONMENTS[1:], ENVIRONMENTS))
HOOKS = ("start", "stop", "health")
RECEIPT = ".deployment.json"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def identity(value):
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def sha256_file(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def mapping(value, name):
    require(isinstance(value, dict) and all(isinstance(key, str) for key in value), f"{name} must be a mapping with string keys")
    return value


def load(path):
    return mapping(yaml.safe_load(path.read_text()), str(path))


def run(command, capture=False):
    return subprocess.run(command, cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE if capture else None).stdout


def snapshot(repository, ref, work):
    git = ["git", "-C", str(repository.resolve())]
    commit = run([*git, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"], capture=True).strip()
    archive = work / "config.tar"
    run([*git, "archive", "--format=tar", f"--output={archive}", commit])
    target = work / "config"
    target.mkdir()
    extract(archive, target)
    return target, commit


def read_baseline(config, environment):
    data = load(config / "envs" / environment / "deployment.yml")
    require(data["environment"] == environment, f"Environment mismatch in the {environment} baseline")
    components = mapping(data["components"], "components")
    require(components, "Define at least one component")
    read_deployment(data)
    for name, entry in components.items():
        entry = mapping(entry, name)
        require(re.fullmatch(r"[a-z][a-z0-9_-]*", name), f"Invalid component name: {name}")
        require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry["group"]), f"Invalid inventory group for {name}")
        require(re.fullmatch(r"[a-z_][a-z0-9_-]*", entry["service_user"]), f"Invalid service user for {name}")
        require(entry["service_user"] != "root", f"Choose an unprivileged service user for {name}")
        positive(entry.get("hook_timeout", 30), "hook_timeout")
        positive(entry.get("health_retries", 10), "health_retries")
        positive(entry.get("health_delay", 3), "health_delay", 0)
        artifact = mapping(entry["artifact"], "artifact")
        relative_path(artifact["path"])
        require(re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]), f"Invalid package SHA-256 for {name}")
        require(isinstance(artifact["version"], str) and artifact["version"], "artifact.version must be a nonempty string")
    return data


def configuration(root, environment, entry):
    settings = mapping(entry["config"], "config")
    value_path = settings.get("values")
    prefix = ("envs", environment)
    if value_path:
        value_path = relative_path(value_path)
        require(value_path.parts[:2] == prefix, "Values must come from the target environment")
    values = load(root / value_path) if value_path else {}
    params = mapping(values.get("parameters", {}), "parameters")
    secrets = mapping(values.get("secret_env", {}), "secret_env")
    require(all(isinstance(val, str) for val in params.values()), "Quote parameter values as strings")
    require(not (params.keys() & secrets.keys()), "A parameter cannot also be a secret reference")
    secret_revision = values.get("secret_revision")
    require(not secrets or isinstance(secret_revision, str) and secret_revision, "Set secret_revision when using secret references")
    for variable in secrets.values():
        require(isinstance(variable, str) and re.fullmatch(r"MYAPP_SECRET_[A-Z0-9_]+", variable), "Secret references must name MYAPP_SECRET_* environment variables")
    files = {}
    fingerprint = {"values": values, "values_path": str(value_path) if value_path else None, "files": {}}
    for rule in settings.get("files", []):
        source = relative_path(rule["source"])
        require(source.parts[:2] == prefix, "Configuration must come from the target environment")
        destination = relative_path(rule["destination"])
        require(destination != Path(RECEIPT) and destination.parts[0] != "bin", "Configuration cannot overwrite hooks or the receipt")
        action = rule["action"]
        require(action in ("copy", "template") and destination not in files, f"Invalid or duplicate mapping for {destination}")
        raw = (root / source).read_bytes()
        files[destination] = (action, raw)
        fingerprint["files"][str(destination)] = {"action": action, "source": str(source), "sha256": digest(raw)}
    return files, params, secrets, identity(fingerprint)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def obtain_package(args, artifact, work):
    sha = artifact["sha256"]
    target = work / f"{sha}.tar"
    if not target.exists():
        path = relative_path(artifact["path"])
        if args.package_dir:
            shutil.copyfile(args.package_dir / path, target)
        else:
            base = os.environ.get("MYAPP_NEXUS_BASE_URL", "")
            url = urllib.parse.urlsplit(base)
            require(url.scheme == "https" and url.hostname and not (url.username or url.password or url.query or url.fragment), "Set MYAPP_NEXUS_BASE_URL to the HTTPS Nexus repository URL")
            user = os.environ.get("MYAPP_NEXUS_USER")
            password = os.environ.get("MYAPP_NEXUS_PASSWORD")
            require((user is None) == (password is None), "Set both Nexus credential variables or neither")
            request = urllib.request.Request(f"{base.rstrip('/')}/{urllib.parse.quote(str(path), safe='/')}")
            if user is not None:
                credential = base64.b64encode(f"{user}:{password}".encode()).decode()
                request.add_header("Authorization", f"Basic {credential}")
            with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response, target.open("xb") as file:
                shutil.copyfileobj(response, file)
        require(sha256_file(target) == sha, f"SHA-256 mismatch for {artifact['path']}")
    directory = work / sha
    if not directory.exists():
        directory.mkdir()
        extract(target, directory)
    return directory


def prepare_payload(args, snapshot_path, commit, work, name, entry, planned_release, config_fingerprint):
    artifact = entry["artifact"]
    contents = obtain_package(args, artifact, work)
    manifest = load(contents / "manifest.yml")
    require(manifest["component"] == name, f"Package identifies {manifest['component']!r}, expected {name!r}")
    require(manifest["version"] == artifact["version"], f"Package version mismatch for {name}")
    source = contents / "payload"
    require(source.is_dir(), f"Component directory missing for {name}")
    directory = work / "payloads" / name
    shutil.copytree(source, directory)
    require(not (directory / RECEIPT).exists(), f"Reserved filename in vendor component {name}")
    for hook in HOOKS:
        file = directory / "bin" / hook
        require(file.is_file() and file.stat().st_mode & 0o111, f"Missing executable bin/{hook} for {name}")
    version_hook = directory / "bin/version"
    require(not version_hook.exists() or (version_hook.is_file() and version_hook.stat().st_mode & 0o111), f"Invalid bin/version for {name}")

    files, params, secrets, _ = configuration(snapshot_path, args.environment, entry)
    values = dict(params)
    for parameter, variable in secrets.items():
        require(bool(os.environ.get(variable)), f"Missing secret variable {variable} for {name}")
        values[parameter] = os.environ[variable]
    renderer = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
    renderer.filters["java_property"] = javaproperties.escape
    for destination, (action, raw) in files.items():
        target = directory / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        if action == "template":
            try:
                raw = renderer.from_string(raw.decode()).render(settings=values).encode()
            except Exception:
                raise ValueError(f"Failed to render {name}/{destination}; check the template and declared settings") from None
        target.write_bytes(raw)
        target.chmod(0o640)

    for prop_file, keys in mapping(manifest.get("required_properties", {}), "required_properties").items():
        require(isinstance(keys, list) and all(isinstance(key, str) for key in keys), f"Invalid property contract for {name}")
        properties = directory / relative_path(prop_file)
        require(properties.is_file(), f"Required properties file missing: {name}/{prop_file}")
        try:
            with properties.open("rb") as file:
                values = javaproperties.load(file)
        except Exception:
            raise ValueError(f"Could not parse {name}/{prop_file}") from None
        missing = [key for key in keys if not values.get(key, "").strip()]
        require(not missing, f"Missing required keys in {name}/{prop_file}: {', '.join(missing)}")

    receipt = {
        "component": name, "environment": args.environment, "component_version": artifact["version"],
        "nexus_path": artifact["path"], "package_sha256": artifact["sha256"], "config_commit": commit,
        "config_fingerprint": config_fingerprint, "release_id": planned_release,
        "ci_job": os.environ.get("CI_JOB_URL") or os.environ.get("BUILD_URL"),
    }
    (directory / RECEIPT).write_text(json.dumps(receipt))
    package = work / f"payload-{name}.tar"
    with tarfile.open(package, "w") as tar:
        for child in sorted(directory.iterdir()):
            tar.add(child, arcname=child.name)
    return package


def inventory(path):
    return json.loads(run(["ansible-inventory", "-i", str(path), "--list"], capture=True))


def build_host_tool(work):
    package = work / "myapp-host.pyz"
    with zipfile.ZipFile(package, "w") as zipapp:
        zipapp.write(ROOT / "tools/host.py", "__main__.py")
        zipapp.write(ROOT / "tools/archive.py", "archive.py")
    return package


def make_plan(baseline, stages, config, env, target_info):
    selected = [name for stage in stages for name in stage["components"]]
    planned = {}
    for name in selected:
        entry = baseline["components"][name]
        _, _, _, fingerprint = configuration(config, env, entry)
        release = identity({"component": name, "environment": env, "artifact": entry["artifact"], "configuration": fingerprint})
        target_hosts, waves = target_info[name]
        planned[name] = {
            "component_name": name, "component_group": entry["group"], "component_user": entry["service_user"],
            "hook_timeout": entry.get("hook_timeout", 30),
            "health_retries": entry.get("health_retries", 10), "health_delay": entry.get("health_delay", 3),
            "release_id": release, "config_fingerprint": fingerprint, "target_hosts": target_hosts,
            "host_waves": waves, "artifact": entry["artifact"],
        }
    return planned


def play(playbook, common, params, work, wave=None):
    with tempfile.TemporaryDirectory(dir=work) as directory:
        variables = Path(directory) / "ansible-input.json"
        variables.write_text(json.dumps({**common, **params}))
        command = [
            "ansible-playbook", "-i", common["inventory_path"],
            str(ROOT / "playbooks" / playbook), "-e", f"@{variables}",
        ]
        if wave is None:
            run(command)
            return
        command.extend(["--limit", ",".join(wave) + ",", "--forks", str(len(wave))])
        result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        print(f"\n[{params['component_name']} | {', '.join(wave)}]\n{result.stdout}", flush=True)
        result.check_returncode()


def display_plan(stages, plan, mode, environment, commit):
    print(json.dumps({
        "environment": environment, "baseline_commit": commit, "mode": mode,
        "stages": stages, "components": plan,
    }, indent=2))


def collect_status(plan, common, work, report):
    rows = []
    for name, entry in plan.items():
        with tempfile.TemporaryDirectory(dir=work) as directory:
            directory = Path(directory)
            try:
                play("status.yml", common, {**entry, "status_dir": str(directory)}, work)
            except subprocess.CalledProcessError:
                print(f"Ansible status query failed on one or more hosts for {name}", file=sys.stderr)
            for host in entry["target_hosts"]:
                path = directory / f"{digest(host.encode())}.json"
                if path.exists():
                    observed = json.loads(path.read_text())
                else:
                    observed = {"state": "unknown", "error": "Ansible returned no host result"}
                rows.append({
                    **observed, "component": name, "host": host, "expected_release_id": entry["release_id"],
                    "matches_baseline": observed.get("release_id") == entry["release_id"],
                })
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"environment": common["deployment_environment"], "baseline_commit": common["baseline_commit"], "components": rows}, indent=2) + "\n")
    for row in rows:
        version = row.get("component_version", "-")
        print(f"{row['host']:24} {row['component']:20} {version:16} {row['state']:24} baseline={'yes' if row['matches_baseline'] else 'no'}")
    return all(row["state"] == "healthy" and row["matches_baseline"] for row in rows)


def promote(args, config, source, selected):
    target = read_baseline(config, args.to)
    for name in selected:
        require(name in target["components"], f"Add {name} and its {args.to} configuration to the target baseline first")
        target["components"][name]["artifact"] = source["components"][name]["artifact"]
        configuration(config, args.to, target["components"][name])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(target, sort_keys=False))
    print(f"Candidate written to {args.output}. Review it in a GitLab merge request.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["deploy", "status", "promote", "validate", "plan"])
    parser.add_argument("--config-repo", type=Path, required=True)
    parser.add_argument("--config-ref", default="HEAD")
    parser.add_argument("--environment", choices=ENVIRONMENTS, required=True)
    parser.add_argument("--mode", choices=["selective", "full"])
    parser.add_argument("--components", help="Override the descriptor's selection in selective mode")
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--package-dir", type=Path)
    parser.add_argument("--report", type=Path, default=Path("reports/status.json"))
    parser.add_argument("--to", choices=ENVIRONMENTS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "promote":
        if not args.to or not args.output or PROMOTION_SOURCE.get(args.to) != args.environment:
            parser.error("promotion requires --output and the next environment: dev → test → prod → dr")

    try:
        with tempfile.TemporaryDirectory(prefix="myapp-") as directory:
            work = Path(directory)
            config, commit = snapshot(args.config_repo, args.config_ref, work)
            baseline = read_baseline(config, args.environment)
            mode = args.mode or ("full" if args.action == "status" else baseline["deployment"]["mode"])
            inv_path = (args.inventory or ROOT / "inventories" / args.environment / "hosts.yml").resolve()
            selection = (
                [name.strip() for name in args.components.split(",")]
                if args.components is not None else None
            )
            stages, target_info = schedule(baseline, inventory(inv_path), mode, selection)
            plan = make_plan(baseline, stages, config, args.environment, target_info)
            if args.action == "plan":
                display_plan(stages, plan, mode, args.environment, commit)
                return 0
            if args.action == "validate":
                print(f"Valid baseline {args.environment} at {commit}: {', '.join(plan)}")
                return 0
            common = {
                "deployment_environment": args.environment,
                "baseline_commit": commit,
                "inventory_path": str(inv_path),
                "host_tool": str(build_host_tool(work)),
            }
            if args.action == "deploy":
                failed = False
                try:
                    for name, params in plan.items():
                        params["component_archive"] = str(prepare_payload(
                            args, config, commit, work, name, baseline["components"][name],
                            params["release_id"], params["config_fingerprint"],
                        ))
                    for params in plan.values():
                        play("stage.yml", common, params, work)
                    def activate(name, wave):
                        play("activate.yml", common, plan[name], work, wave=wave)

                    def announce(stage, number, components):
                        batches = " | ".join(f"{name}: {', '.join(wave)}" for name, wave in components.items())
                        print(f"Stage {stage}, host wave {number}: {batches}", flush=True)

                    activate_stages(stages, plan, activate, announce)
                except (subprocess.CalledProcessError, OSError, TypeError, KeyError, ValueError, RuntimeError, yaml.YAMLError, tarfile.TarError) as error:
                    print(f"Deployment failed: {error}", file=sys.stderr)
                    failed = True
                healthy = collect_status(plan, common, work, args.report)
                return int(failed or not healthy)
            healthy = collect_status(plan, common, work, args.report)
            if not healthy:
                return 1
            if args.action == "promote":
                promote(args, config, baseline, plan)
            return 0
    except (subprocess.CalledProcessError, OSError, TypeError, KeyError, ValueError, yaml.YAMLError, tarfile.TarError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
