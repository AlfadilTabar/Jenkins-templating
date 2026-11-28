"""Run on a Linux target through Ansible. Requires Python 3 and util-linux."""

import argparse
import contextlib
import fcntl
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from archive import extract

RECEIPT = ".deployment.json"


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        json.dump(value, file)
        file.flush()
        os.fsync(file.fileno())
    os.replace(file.name, path)


class Component:
    def __init__(self, args):
        self.name = args.component
        self.root = Path(args.root) / self.name
        self.releases = self.root / "releases"
        self.shared = self.root / "shared"
        self.current = self.root / "current"
        self.pending = self.root / ".activation.json"
        self.user = pwd.getpwnam(args.user)
        self.timeout = args.timeout
        self.retries = args.retries
        self.delay = args.delay

    @contextlib.contextmanager
    def lock(self):
        self.root.mkdir(mode=0o755, parents=True, exist_ok=True)
        with (self.root / ".lock").open("a") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            yield

    def stage(self, archive, release):
        with self.lock():
            self.releases.mkdir(mode=0o755, exist_ok=True)
            if not self.shared.exists():
                self.shared.mkdir(mode=0o750)
                os.chown(self.shared, self.user.pw_uid, self.user.pw_gid)
            target = self.releases / release
            if target.exists():
                if self.receipt(release)["release_id"] != release:
                    raise RuntimeError("Existing staged release has an invalid receipt")
                return {"changed": False}
            temp = Path(tempfile.mkdtemp(dir=self.releases))
            try:
                extract(archive, temp, self.user.pw_gid)
                receipt = json.loads((temp / RECEIPT).read_text())
                if receipt["release_id"] != release or receipt["component"] != self.name:
                    raise RuntimeError("Staged release receipt does not match the request")
                temp.rename(target)
            finally:
                if temp.exists():
                    shutil.rmtree(temp)
            return {"changed": True}

    def receipt(self, release):
        return json.loads((self.releases / release / RECEIPT).read_text())

    def active(self):
        if self.current.is_symlink():
            target = os.readlink(self.current)
            if re.fullmatch(r"releases/[0-9a-f]{64}", target):
                return Path(target).name
            raise RuntimeError("current symlink points outside the managed releases")
        if self.current.exists():
            raise RuntimeError("current exists and is not a managed symlink")
        return None

    def point_to(self, release):
        if release is None:
            self.current.unlink(missing_ok=True)
            return
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            link = Path(directory) / "current"
            link.symlink_to(Path("releases") / release)
            os.replace(link, self.current)

    def hook(self, release, action):
        directory = self.releases / release
        command = [str(directory / "bin" / action)]
        if os.geteuid() != self.user.pw_uid:
            command = ["runuser", "-u", self.user.pw_name, "--", *command]
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": self.user.pw_dir,
            "USER": self.user.pw_name,
            "LOGNAME": self.user.pw_name,
            "MYAPP_COMPONENT_DIR": str(directory),
            "MYAPP_SHARED_DIR": str(self.shared),
            "MYAPP_COMPONENT": self.name,
        }
        try:
            result = subprocess.run(
                command, cwd=directory, env=env, timeout=self.timeout,
                stdout=subprocess.PIPE if action == "version" else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, text=True, check=True,
            )
        except (subprocess.SubprocessError, OSError) as error:
            raise RuntimeError(f"{action} hook failed for {self.name} ({type(error).__name__})") from None
        return result.stdout.strip() if action == "version" else None

    def check(self, release, attempts):
        for attempt in range(attempts):
            try:
                self.hook(release, "health")
                if (self.releases / release / "bin/version").exists():
                    running = self.hook(release, "version")
                    if running != self.receipt(release)["component_version"]:
                        raise RuntimeError(f"Running version mismatch for {self.name}")
                    return running
                return None
            except RuntimeError:
                if attempt == attempts - 1:
                    raise
                time.sleep(self.delay)

    def recover(self):
        journal = json.loads(self.pending.read_text())
        previous = journal["previous"]
        current = self.active()
        if current is not None:
            self.hook(current, "stop")
        self.point_to(previous)
        if previous is not None:
            self.hook(previous, "start")
            self.check(previous, self.retries)
        self.pending.unlink()

    def activate(self, release):
        with self.lock():
            self.receipt(release)
            if self.pending.exists():
                try:
                    self.recover()
                except Exception as error:
                    raise RuntimeError(f"Interrupted activation recovery failed: {error}") from None
            previous = self.active()
            if previous == release:
                try:
                    self.check(release, 1)
                    return {"changed": False}
                except RuntimeError:
                    pass
            write_json(self.pending, {"previous": previous})
            try:
                if previous is not None:
                    self.hook(previous, "stop")
                self.point_to(release)
                self.hook(release, "start")
                self.check(release, self.retries)
            except Exception as error:
                try:
                    self.recover()
                except Exception as rollback_error:
                    raise RuntimeError(f"{error}. Rollback failed: {rollback_error}") from None
                raise RuntimeError(f"{error}. Rollback completed") from None
            self.pending.unlink()
            return {"changed": True}

    def status(self):
        result = {"state": "absent", "running_version": None}
        if not self.root.is_dir():
            return result
        with self.lock():
            active = self.active()
            if active is not None:
                result.update(self.receipt(active))
                try:
                    result["running_version"] = self.check(active, 1)
                    result["state"] = "healthy"
                except RuntimeError as error:
                    result.update(state="unhealthy", error=str(error))
            if self.pending.exists():
                result["state"] = "activation_interrupted"
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["stage", "activate", "status"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--component", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--retries", type=int, required=True)
    parser.add_argument("--delay", type=int, required=True)
    parser.add_argument("--release")
    parser.add_argument("--archive")
    args = parser.parse_args()
    if args.action != "status" and not re.fullmatch(r"[0-9a-f]{64}", args.release or ""):
        parser.error("a valid release ID is required")
    try:
        component = Component(args)
        if args.action == "stage":
            result = component.stage(args.archive, args.release)
        elif args.action == "activate":
            result = component.activate(args.release)
        else:
            result = component.status()
        print(json.dumps(result))
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
