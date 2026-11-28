"""Extraction shared by the controller and Linux deployment hosts."""

import os
import shutil
import tarfile
from pathlib import Path


def relative_path(value):
    if not isinstance(value, str) or not value or any(
        part in ("", ".", "..") for part in value.split("/")
    ) or "\\" in value or any(ord(char) < 32 for char in value):
        raise ValueError(f"Invalid relative path: {value!r}")
    return Path(value)


def extract(archive, destination, group=None):
    destination = Path(destination)
    with tarfile.open(archive, "r:*") as tar:
        seen = set()
        for member in tar:
            path = relative_path(member.name)
            if path in seen or not (member.isdir() or member.isfile()):
                raise ValueError(f"Unsupported or duplicate archive entry: {member.name}")
            seen.add(path)
            target = destination / path
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o750 if member.mode & 0o111 else 0o640)

    for root, directories, files in os.walk(destination):
        directory = Path(root)
        directory.chmod(0o750)
        if group is not None:
            os.chown(directory, -1, group)
            for filename in files:
                os.chown(directory / filename, -1, group)
