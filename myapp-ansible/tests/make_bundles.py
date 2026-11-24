"""Generate small reproducible vendor tarballs for the integration test."""

import gzip
import io
import sys
import tarfile
import textwrap
from pathlib import Path

import yaml


def member(tar, name, value, executable=False):
    data = value.encode()
    item = tarfile.TarInfo(name)
    item.size = len(data)
    item.mode = 0o755 if executable else 0o644
    tar.addfile(item, io.BytesIO(data))


def bundle(directory, version, orders, broken=False, required="db.url"):
    components = {"orders": orders, "billing": "2.13.4"}
    manifest = {"bundle_version": version, "components": {}}
    for name, build in components.items():
        manifest["components"][name] = {
            "version": build,
            "path": f"components/{name}",
            "required_properties": {"config/application.properties": [required, "db.password"]},
        }

    path = directory / f"myapp-{version}.tar.gz"
    with path.open("wb") as output, gzip.GzipFile(fileobj=output, filename="", mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as tar:
            member(tar, "manifest.yml", yaml.safe_dump(manifest))
            for name, build in components.items():
                prefix = f"components/{name}"
                member(tar, f"{prefix}/BUILD", f"{build}\n")
                member(tar, f"{prefix}/config/application.properties", "# supplied at deploy time\n")
                start = textwrap.dedent("""\
                    #!/bin/sh
                    set -eu
                    cat BUILD > "$MYAPP_SHARED_DIR/running-version"
                    """)
                member(tar, f"{prefix}/bin/start", start, executable=True)
                member(tar, f"{prefix}/bin/stop", '#!/bin/sh\nrm -f "$MYAPP_SHARED_DIR/running-version"\n', executable=True)
                condition = "exit 1" if broken and name == "orders" else 'test -s "$MYAPP_SHARED_DIR/running-version"'
                member(tar, f"{prefix}/bin/health", f"#!/bin/sh\n{condition}\n", executable=True)
                member(tar, f"{prefix}/bin/version", '#!/bin/sh\ncat "$MYAPP_SHARED_DIR/running-version"\n', executable=True)
    return path


def main():
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    bundle(directory, "2026.11.10.2", "3.8.1")
    bundle(directory, "2026.11.23.1", "3.8.2")
    bundle(directory, "broken", "3.8.3", broken=True)
    bundle(directory, "missing-property", "3.8.4", required="new.required.key")


if __name__ == "__main__":
    main()
