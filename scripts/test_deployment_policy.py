#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
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

    def test_unknown_defaults_to_auto(self) -> None:
        # Policy change (do-unless-clearly-risky): unknown deployment commands
        # default to auto. Operators who need stricter routing override via
        # .build-loop/config.json deploymentPolicy.unknown.
        result = run(self.workdir, "railway up")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = output(result)
        self.assertEqual(data["target"], "unknown")
        self.assertEqual(data["action"], "auto")

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

    def test_protected_branches_empty_routes_main_push_to_preview(self) -> None:
        """Empty protectedBranches => main is no longer protected; routes as preview.

        Per-repo opt-in for iOS / single-trunk workflows where push-to-main
        is the trigger for a CI-driven TestFlight/preview deploy.
        """
        self.write_config({"protectedBranches": []})
        result = run(self.workdir, "git push origin main")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = output(result)
        self.assertEqual(data["target"], "preview")
        self.assertEqual(data["action"], "auto")

    def test_protected_branches_subset_protects_only_named(self) -> None:
        """Only branches in the override list route as production."""
        self.write_config({"protectedBranches": ["release"]})

        main_push = run(self.workdir, "git push origin main")
        self.assertEqual(output(main_push)["target"], "preview")

        release_push = run(self.workdir, "git push origin release")
        self.assertEqual(output(release_push)["target"], "production")
        self.assertEqual(output(release_push)["action"], "confirm")

    def test_protected_branches_case_insensitive(self) -> None:
        """Override names are compared lowercased."""
        self.write_config({"protectedBranches": ["MAIN"]})
        result = run(self.workdir, "git push origin main")
        self.assertEqual(output(result)["target"], "production")

    def test_protected_branches_snake_case_alias_honored(self) -> None:
        """`protected_branches` (snake_case) is accepted as well."""
        self.write_config({"protected_branches": []})
        result = run(self.workdir, "git push origin main")
        self.assertEqual(output(result)["target"], "preview")

    def test_protected_branches_invalid_type_fails_closed(self) -> None:
        """Non-list protectedBranches => PolicyError; default fail-closed verdict."""
        self.write_config({"protectedBranches": "main"})
        result = run(self.workdir, "git push origin main")
        self.assertEqual(result.returncode, 1)
        data = output(result)
        self.assertEqual(data["action"], "confirm")
        self.assertIn("policy error", data["reason"])

    def test_protected_branches_default_preserves_main_protection(self) -> None:
        """No override => behavior unchanged: main is protected."""
        self.write_config({"production": "confirm"})  # explicit target only, no protectedBranches
        result = run(self.workdir, "git push origin main")
        self.assertEqual(output(result)["target"], "production")
        self.assertEqual(output(result)["action"], "confirm")

    def test_protected_branches_does_not_override_production_shape_commands(self) -> None:
        """Branch declassification does not weaken production for production-shaped commands.

        Per the user-permission posture: declassifying a branch only changes
        routing path; commands that are inherently production-shaped (npm publish,
        gh release, vercel --prod) still route as production regardless.
        """
        self.write_config({"protectedBranches": []})
        for cmd in ("npm publish", "gh release create v1.2.3", "vercel deploy --prod"):
            with self.subTest(cmd=cmd):
                data = output(run(self.workdir, cmd))
                self.assertEqual(data["target"], "production")
                self.assertEqual(data["action"], "confirm")


if __name__ == "__main__":
    unittest.main()
