"""Resolve the rollout declared at the top of an environment descriptor."""

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor


def require(condition, message):
    if not condition:
        raise ValueError(message)


def positive(value, name, minimum=1):
    require(type(value) is int and value >= minimum, f"{name} must be an integer >= {minimum}")
    return value


def names(values, label):
    require(
        isinstance(values, list) and values
        and all(isinstance(value, str) and value for value in values),
        f"{label} must be a nonempty list of names",
    )
    require(len(values) == len(set(values)), f"{label} contains duplicate names")
    return values


def read_deployment(baseline):
    deployment = baseline["deployment"]
    require(isinstance(deployment, dict), "deployment must be a mapping")
    require(deployment["mode"] in ("selective", "full"), "deployment.mode must be selective or full")
    require(deployment["full"]["components"] == "all", "deployment.full.components must be all")
    selected = names(deployment["selective"]["components"], "deployment.selective.components")
    components = set(baseline["components"])
    require(set(selected) <= components, "Selective mode names an unknown component")
    stages = deployment["stages"]
    require(isinstance(stages, list) and stages, "Define at least one deployment stage")
    stage_names = []
    scheduled = []
    for stage in stages:
        stage_names.append(stage["name"])
        scheduled.extend(names(stage["components"], "stage.components"))
        positive(stage["max_parallel"], "stage.max_parallel")
    names(stage_names, "stage names")
    require(
        Counter(scheduled) == Counter(components),
        "deployment.stages must name each descriptor component exactly once",
    )
    return deployment


def target_hosts(inventory, group):
    require(group in inventory, f"Inventory group {group} is missing")
    group_data = inventory[group]
    result = set(group_data.get("hosts", []))
    for child in group_data.get("children", []):
        result.update(target_hosts(inventory, child))
    require(result, f"Inventory group {group} has no hosts")
    return result


def host_waves(value, expected, label):
    require(isinstance(value, list) and value, f"{label} must contain at least one host wave")
    waves = [names(wave, label) for wave in value]
    counts = Counter(host for wave in waves for host in wave)
    missing = sorted(expected - counts.keys())
    extra = sorted(counts.keys() - expected)
    repeated = sorted(host for host, count in counts.items() if count > 1)
    require(
        not (missing or extra or repeated),
        f"{label} must list each target alias exactly once. Missing: {missing}, extra: {extra}, repeated: {repeated}",
    )
    return waves


def schedule(baseline, inventory, mode, selection=None):
    deployment = read_deployment(baseline)
    requested = deployment["selective"]["components"]
    if selection is not None:
        require(mode == "selective", "--components requires selective mode")
        requested = names(selection, "--components")
        require(set(requested) <= baseline["components"].keys(), "--components contains an unknown component")
    selected = set(baseline["components"] if mode == "full" else requested)
    memberships = {
        name: target_hosts(inventory, entry["group"]) for name, entry in baseline["components"].items()
    }
    all_hosts = set().union(*memberships.values())
    for alias in all_hosts:
        require(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", alias) and alias not in inventory,
            f"Host alias {alias!r} cannot be used as an unambiguous Ansible limit",
        )
    default_waves = host_waves(deployment["host_waves"], all_hosts, "deployment.host_waves")
    by_component = {}
    for name, entry in baseline["components"].items():
        membership = memberships[name]
        if "host_waves" in entry:
            waves = host_waves(entry["host_waves"], membership, f"{name}.host_waves")
        else:
            waves = [[host for host in wave if host in membership] for wave in default_waves]
        by_component[name] = (sorted(membership), waves)
    stages = []
    for stage in deployment["stages"]:
        components = [name for name in stage["components"] if name in selected]
        if components:
            stages.append({**stage, "components": components})
    return stages, by_component


def activate_stages(stages, plan, activate, announce):
    for stage in stages:
        names = stage["components"]
        wave_count = max(len(plan[name]["host_waves"]) for name in names)
        for index in range(wave_count):
            work = {}
            for name in names:
                waves = plan[name]["host_waves"]
                if index < len(waves) and waves[index]:
                    work[name] = waves[index]
            if not work:
                continue
            announce(stage["name"], index + 1, work)
            parallel = min(stage["max_parallel"], len(work))
            items = list(work.items())
            with ThreadPoolExecutor(max_workers=parallel) as executor:
                for offset in range(0, len(items), parallel):
                    jobs = {
                        name: executor.submit(activate, name, wave)
                        for name, wave in items[offset:offset + parallel]
                    }
                    for name, job in jobs.items():
                        try:
                            job.result()
                        except Exception as error:
                            raise RuntimeError(
                                f"Activation failed for {name} in stage {stage['name']}, "
                                f"host wave {index + 1}: {error}"
                            ) from error
