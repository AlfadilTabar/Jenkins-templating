"""Verify the fail-closed package bootstrap without network access."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import bootstrap_python
import network


class OfflineTests(unittest.TestCase):
    def test_requires_approved_https_authority(self):
        host = "nexus.corp.example:8443"
        network.approved_https(f"https://{host}/repository/vendor/x.tar", host, "Nexus")
        for url in (
            f"http://{host}/x", "https://elsewhere.example/x",
            f"https://user@{host}/x", f"https://{host}/x?query", f"https://{host}/x#target",
            f"https://{host}/bad path", f"https://{host}/x\\y",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                network.approved_https(url, host, "Nexus")

    def test_download_ignores_proxies_and_refuses_redirects(self):
        with patch.dict(os.environ, {"MYAPP_NEXUS_AUTHORITY": "nexus.corp.example"}, clear=True):
            with patch.object(network.urllib.request, "build_opener") as build:
                opener = build.return_value
                opener.open.side_effect = RuntimeError("simulated transfer failure")
                with self.assertRaisesRegex(RuntimeError, "simulated transfer failure"):
                    network.download_nexus("https://nexus.corp.example/repository/x.tar", Path("unused"), "MYAPP_NEXUS")
                proxy, redirects = build.call_args.args
                self.assertEqual(proxy.proxies, {})
                self.assertIsNone(redirects.redirect_request(None, None, 302, "", {}, "https://elsewhere.example/"))

    def test_requirements_disallow_links_and_pip_directives(self):
        with tempfile.TemporaryDirectory() as temp:
            requirements = Path(temp) / "requirements.txt"
            sanitized = Path(temp) / "sanitized.txt"
            requirements.write_text("ansible-core>=2.19,<2.20\n")
            bootstrap_python.read_requirements(requirements, sanitized)
            self.assertEqual(sanitized.read_text(), requirements.read_text())
            for line in ("--extra-index-url https://elsewhere.example/simple", "demo @ https://elsewhere.example/w.whl", "-r other.txt"):
                with self.subTest(line=line), self.assertRaises(ValueError):
                    requirements.write_text(line + "\n")
                    bootstrap_python.read_requirements(requirements, sanitized)

    def test_pip_clears_inherited_configuration(self):
        with patch.dict(os.environ, {"PIP_EXTRA_INDEX_URL": "https://elsewhere.example/simple", "PIP_CONFIG_FILE": "/invalid"}, clear=True):
            with patch.object(bootstrap_python.subprocess, "run") as run:
                bootstrap_python.pip(["install", "--no-index", "--no-deps", "/tmp/example.whl"])
        command = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        self.assertEqual({key: value for key, value in env.items() if key.startswith("PIP_")}, {"PIP_CONFIG_FILE": os.devnull})
        self.assertIn("--disable-pip-version-check", command)
        self.assertIn("--no-index", command)
        self.assertIn("--no-deps", command)

    def test_wheel_install_uses_only_explicit_local_files(self):
        with tempfile.TemporaryDirectory() as temp:
            wheelhouse = Path(temp)
            wheel = wheelhouse / "demo-1.0-py3-none-any.whl"
            wheel.touch()
            with patch.object(bootstrap_python, "pip") as pip:
                bootstrap_python.install(wheelhouse, Path("/tmp/requirements.txt"))
            self.assertEqual(
                pip.call_args_list[0].args[0],
                ["install", "--no-index", "--no-deps", "--only-binary=:all:", str(wheel)],
            )
            self.assertEqual(pip.call_args_list[-1].args[0], ["check"])
            (wheelhouse / "source.tar").touch()
            with self.assertRaisesRegex(ValueError, "flat .whl"):
                bootstrap_python.install(wheelhouse, Path("/tmp/requirements.txt"))


if __name__ == "__main__":
    unittest.main()
