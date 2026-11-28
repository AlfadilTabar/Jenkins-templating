"""Generate one deterministic tarball per demo component build."""

import gzip
import io
import sys
import tarfile
from pathlib import Path

import yaml


def member(tar, name, text, mode):
    data = text.encode()
    item = tarfile.TarInfo(name)
    item.size = len(data)
    item.mode = mode
    tar.addfile(item, io.BytesIO(data))


def package(directory, component, version, broken=False, required="db.url"):
    manifest = {
        "component": component,
        "version": version,
        "required_properties": {"config/application.properties": [required, "db.password"]},
    }
    files = {
        "manifest.yml": (yaml.safe_dump(manifest), 0o644),
        "payload/BUILD": (version + "\n", 0o644),
        "payload/config/application.properties": ("# supplied at deployment\n", 0o644),
        "payload/bin/start": ('#!/bin/sh\nset -eu\ncat BUILD > "$MYAPP_SHARED_DIR/running-version"\n', 0o755),
        "payload/bin/stop": ('#!/bin/sh\nrm -f "$MYAPP_SHARED_DIR/running-version"\n', 0o755),
        "payload/bin/health": (
            "#!/bin/sh\n" + ("exit 1" if broken else 'test -s "$MYAPP_SHARED_DIR/running-version"') + "\n",
            0o755,
        ),
        "payload/bin/version": ('#!/bin/sh\ncat "$MYAPP_SHARED_DIR/running-version"\n', 0o755),
    }
    release = directory / component / version / f"{component}-{version}.tar.gz"
    release.parent.mkdir(parents=True, exist_ok=True)
    with release.open("wb") as output, gzip.GzipFile(fileobj=output, filename="", mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as tar:
            for name, (text, mode) in files.items():
                member(tar, name, text, mode)


def main():
    directory = Path(sys.argv[1])
    for component, versions in {
        "payment": ("10.0", "10.1"),
        "orders": ("11.1", "11.2"),
        "reporting": ("4.0",),
    }.items():
        for version in versions:
            package(directory, component, version)
    package(directory, "payment", "10.2", broken=True)
    package(directory, "payment", "10.3", required="new.required.key")


if __name__ == "__main__":
    main()
