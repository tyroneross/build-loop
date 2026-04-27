#!/usr/bin/env python3
"""Tests for deployment_policy.py. Zero deps. Run: python3 test_deployment_policy.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "deployment_policy.py"


def run(workdir: Path, command: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir), "--command", command, *extra],
        capture_output=True,
        text=True,
    )


def output(result: subprocess.CompletedProcess) -> dict:
    return json.loads(result.stdout)


class DeploymentPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_config(self, policy: dict[str, str]) -> None:
        config = self.workdir / ".build-loop" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"deploymentPolicy": policy}))

    def test_preview_deploy_defaults_to_auto(self) -> None:
        result = run(self.workdir, "vercel deploy")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = output(result)
        self.assertEqual(data["target"], "preview")
        self.assertEqual(data["action"], "auto")

    def test_testflight_upload_defaults_to_auto(self) -> None:
        result = run(self.workdir, "xcrun altool --upload-app -f build/MyApp.ipa -t ios")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = output(result)
        self.assertEqual(data["target"], "testflight")
        self.assertEqual(data["action"], "auto")

    def test_xcode_export_defaults_to_testflight_auto(self) -> None:
        result = run(self.workdir, "xcodebuild -exportArchive -archivePath build/App.xcarchive")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = output(result)
        self.assertEqual(data["target"], "testflight")
        self.assertEqual(data["action"], "auto")

    def test_prod_deploy_defaults_to_confirm(self) -> None:
        result = run(self.workdir, "vercel deploy --prod")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = output(result)
        self.assertEqual(data["target"], "production")
        self.assertEqual(data["action"], "confirm")
        self.assertTrue(data["requiresConfirmation"])

    def test_spaced_production_target_defaults_to_confirm(self) -> None:
        result = run(self.workdir, "vercel deploy --target production")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = output(result)
        self.assertEqual(data["target"], "production")
        self.assertEqual(data["action"], "confirm")

    def test_git_push_main_defaults_to_confirm(self) -> None:
        result = run(self.workdir, "git push origin main")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = output(result)
        self.assertEqual(data["target"], "production")
        self.assertEqual(data["action"], "confirm")

    def test_git_push_feature_defaults_to_auto_preview(self) -> None:
        result = run(self.workdir, "git push origin feature/policy")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = output(result)
        self.assertEqual(data["target"], "preview")
        self.assertEqual(data["action"], "auto")

    def test_unknown_defaults_to_confirm(self) -> None:
        result = run(self.workdir, "railway up")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = output(result)
        self.assertEqual(data["target"], "unknown")
        self.assertEqual(data["action"], "confirm")

    def test_repo_config_can_override_target_policy(self) -> None:
        self.write_config({"production": "auto", "unknown": "block"})

        prod = run(self.workdir, "git push origin main")
        self.assertEqual(output(prod)["action"], "auto")

        unknown = run(self.workdir, "railway up")
        self.assertEqual(output(unknown)["action"], "block")

    def test_require_auto_exit_codes(self) -> None:
        preview = run(self.workdir, "vercel deploy", "--require-auto")
        self.assertEqual(preview.returncode, 0, msg=preview.stdout)

        prod = run(self.workdir, "vercel deploy --prod", "--require-auto")
        self.assertEqual(prod.returncode, 2, msg=prod.stdout)

        self.write_config({"unknown": "block"})
        blocked = run(self.workdir, "railway up", "--require-auto")
        self.assertEqual(blocked.returncode, 3, msg=blocked.stdout)

    def test_invalid_config_fails_closed(self) -> None:
        config = self.workdir / ".build-loop" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"deploymentPolicy": {"production": "surprise"}}))

        result = run(self.workdir, "git push origin main")
        self.assertEqual(result.returncode, 1)
        data = output(result)
        self.assertEqual(data["target"], "unknown")
        self.assertEqual(data["action"], "confirm")
        self.assertIn("policy error", data["reason"])


if __name__ == "__main__":
    unittest.main()
