"""Install controller dependencies exclusively from a verified Nexus wheel archive."""

import hashlib
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from archive import extract
from network import download_nexus

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:(?:===|==|!=|~=|>=|<=|>|<)[0-9][A-Za-z0-9.*+_-]*(?:,|$))+")


def read_requirements(source, destination):
    """Write only simple distribution/version specifiers for the final pip validation."""
    lines = [line.strip() for line in source.read_text().splitlines()]
    requirements = [line for line in lines if line and not line.startswith("#")]
    if not requirements or any(
        line.endswith(",") or not REQUIREMENT.fullmatch(line) for line in requirements
    ):
        raise ValueError("Use only package names with version constraints in requirements.txt")
    destination.write_text("\n".join(requirements) + "\n")


def pip(arguments):
    settings = {name: value for name, value in os.environ.items() if not name.startswith("PIP_")}
    settings["PIP_CONFIG_FILE"] = os.devnull
    command = [sys.executable, "-I", "-m", "pip", "--disable-pip-version-check", "--no-cache-dir"]
    subprocess.run([*command, *arguments], env=settings, check=True)


def install(wheelhouse, requirements):
    wheels = sorted(wheelhouse.iterdir())
    if not wheels or any(not wheel.is_file() or wheel.suffix != ".whl" for wheel in wheels):
        raise ValueError("The Nexus wheel archive must contain only flat .whl files")
    flags = ["--no-index", "--no-deps", "--only-binary=:all:"]
    pip(["install", *flags, *(str(wheel) for wheel in wheels)])
    pip(["install", "--dry-run", *flags, "-r", str(requirements)])
    pip(["check"])


def main():
    if sys.prefix == sys.base_prefix:
        print("Use a dedicated Python virtual environment for the offline bootstrap", file=sys.stderr)
        return 1
    try:
        checksum = os.environ.get("MYAPP_NEXUS_WHEELHOUSE_SHA256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError("Set MYAPP_NEXUS_WHEELHOUSE_SHA256 to the approved archive SHA-256")
        with tempfile.TemporaryDirectory(prefix="myapp-wheels-") as directory:
            work = Path(directory)
            requirements = work / "requirements.txt"
            read_requirements(ROOT / "requirements.txt", requirements)
            archive = work / "wheelhouse.tar"
            download_nexus(
                os.environ.get("MYAPP_NEXUS_WHEELHOUSE_URL", ""), archive, "MYAPP_NEXUS_WHEELHOUSE",
            )
            with archive.open("rb") as file:
                if hashlib.file_digest(file, "sha256").hexdigest() != checksum:
                    raise ValueError("Nexus wheel archive SHA-256 mismatch")
            wheelhouse = work / "wheels"
            wheelhouse.mkdir()
            extract(archive, wheelhouse)
            install(wheelhouse, requirements)
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f"Offline Python bootstrap failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
