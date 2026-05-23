#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for autonomy_gate.py. Zero deps. Run: python3 test_autonomy_gate.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "autonomy_gate.py"


def run(workdir: Path, action: str, command: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workdir",
            str(workdir),
            "--action",
            action,
            "--command",
            command,
            "--json",
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def envelope(result: subprocess.CompletedProcess) -> dict:
    return json.loads(result.stdout)


class AutonomyGateDefaultsTests(unittest.TestCase):
    """Case 1: each of the 7 default confirmFor patterns should exit 1."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _assert_confirm(self, command: str) -> None:
        result = run(self.workdir, "test", command)
        data = envelope(result)
        self.assertEqual(
            data["action"],
            "confirm",
            msg=f"Expected confirm for {command!r}, got {data['action']!r}. Full: {data}",
        )
        self.assertEqual(
            result.returncode,
            1,
            msg=f"Expected exit 1 for {command!r}, got {result.returncode}",
        )

    def test_npm_publish(self) -> None:
        self._assert_confirm("npm publish")

    def test_npm_publish_with_args(self) -> None:
        self._assert_confirm("npm publish --access public")

    def test_git_push_force(self) -> None:
        self._assert_confirm("git push --force origin main")

    def test_git_push_main(self) -> None:
        self._assert_confirm("git push origin main")

    def test_git_push_master(self) -> None:
        self._assert_confirm("git push origin master")

    def test_production_deploy(self) -> None:
        self._assert_confirm("production deploy v1.2.3")

    def test_drop_table(self) -> None:
        self._assert_confirm("DROP TABLE users")

    def test_rm_rf_root(self) -> None:
        self._assert_confirm("rm -rf /")


class AutonomyGateAutoTests(unittest.TestCase):
    """Case 2: normal-looking commands should exit 0 with action=auto."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_edit_file(self) -> None:
        result = run(self.workdir, "guidance fix", "edit scripts/foo.py")
        data = envelope(result)
        self.assertEqual(data["action"], "auto", msg=str(data))
        self.assertEqual(result.returncode, 0)

    def test_cache_resync(self) -> None:
        result = run(self.workdir, "cache resync", "rsync ... codex cache")
        data = envelope(result)
        self.assertEqual(data["action"], "auto", msg=str(data))
        self.assertEqual(result.returncode, 0)

    def test_lint_cleanup(self) -> None:
        result = run(self.workdir, "lint fix", "npx eslint --fix src/")
        data = envelope(result)
        self.assertEqual(data["action"], "auto", msg=str(data))
        self.assertEqual(result.returncode, 0)


class AutonomyGateRepoOverrideTests(unittest.TestCase):
    """Case 3: repo override adds a custom confirmFor pattern."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_config(self, autonomy: dict) -> None:
        config_path = self.workdir / ".build-loop" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"autonomy": autonomy}))

    def test_custom_confirm_pattern_matches(self) -> None:
        """Case 3: custom confirmFor from repo config exits 1."""
        self._write_config({"confirmFor": ["wipe database*"]})
        result = run(self.workdir, "ops", "wipe database staging")
        data = envelope(result)
        self.assertEqual(data["action"], "confirm", msg=str(data))
        self.assertEqual(data["list_source"], "config")
        self.assertEqual(result.returncode, 1)

    def test_empty_confirm_for_disables_all_defaults(self) -> None:
        """Case 4: empty confirmFor replaces defaults — previously-confirm commands now auto.

        Note: uses 'DROP TABLE users' rather than 'npm publish' because npm publish
        triggers the deployment_policy delegation path (which is higher precedence than
        the repo confirmFor override). DROP TABLE is in the 7 defaults but is not
        deployment-flavored, so it correctly exercises the REPLACE semantics.
        """
        self._write_config({"confirmFor": []})
        # DROP TABLE is in the 7 defaults; with empty override it should now auto
        result = run(self.workdir, "db-op", "DROP TABLE users")
        data = envelope(result)
        self.assertEqual(
            data["action"],
            "auto",
            msg=f"Expected auto (defaults replaced), got {data['action']}. Full: {data}",
        )
        self.assertEqual(result.returncode, 0)

    def test_repo_block_for(self) -> None:
        """Case 5: repo blockFor pattern exits 2."""
        self._write_config({"blockFor": ["rm -rf *"]})
        result = run(self.workdir, "cleanup", "rm -rf /home/user")
        data = envelope(result)
        self.assertEqual(data["action"], "block", msg=str(data))
        self.assertEqual(result.returncode, 2)


class AutonomyGateDeploymentPolicyTests(unittest.TestCase):
    """Case 6: deployment-flavored commands route through deployment_policy."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_vercel_deploy_prod_routes_via_deployment_policy(self) -> None:
        """vercel deploy --prod should resolve via deployment_policy (list_source=deployment_policy)."""
        result = run(self.workdir, "deploy", "vercel deploy --prod")
        data = envelope(result)
        # The key assertion: it was routed through deployment_policy
        self.assertEqual(
            data["list_source"],
            "deployment_policy",
            msg=f"Expected list_source=deployment_policy, got {data['list_source']!r}. Full: {data}",
        )
        # deployment_policy classifies vercel deploy --prod as production -> confirm
        self.assertEqual(data["action"], "confirm", msg=str(data))
        self.assertEqual(result.returncode, 1)

    def test_vercel_preview_routes_via_deployment_policy(self) -> None:
        """vercel deploy (no --prod) should route via deployment_policy and be auto."""
        result = run(self.workdir, "preview deploy", "vercel deploy")
        data = envelope(result)
        self.assertEqual(
            data["list_source"],
            "deployment_policy",
            msg=f"Expected list_source=deployment_policy, got {data['list_source']!r}. Full: {data}",
        )
        self.assertEqual(data["action"], "auto", msg=str(data))
        self.assertEqual(result.returncode, 0)


class AutonomyGateMalformedConfigTests(unittest.TestCase):
    """Case 7: malformed config.json falls back to defaults gracefully (no crash)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_raw_config(self, content: str) -> None:
        config_path = self.workdir / ".build-loop" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(content)

    def test_malformed_json_falls_back_to_defaults(self) -> None:
        self._write_raw_config("{ not valid json }")
        # Should NOT crash — must fall back to defaults
        result = run(self.workdir, "test", "edit scripts/foo.py")
        self.assertEqual(result.returncode, 0, msg=f"Crash! stderr: {result.stderr}")
        data = envelope(result)
        self.assertEqual(data["action"], "auto", msg=str(data))

    def test_malformed_json_still_applies_defaults_on_confirm_command(self) -> None:
        self._write_raw_config("this is not json at all")
        result = run(self.workdir, "publish", "npm publish")
        self.assertEqual(result.returncode, 1, msg=f"stderr: {result.stderr}")
        data = envelope(result)
        self.assertEqual(data["action"], "confirm", msg=str(data))

    def test_truncated_json_falls_back(self) -> None:
        self._write_raw_config('{"autonomy": {"confirmFor": [')
        result = run(self.workdir, "test", "edit scripts/bar.py")
        self.assertNotIn("Traceback", result.stderr, msg="Should not crash with traceback")
        data = envelope(result)
        self.assertEqual(data["action"], "auto", msg=str(data))


class AutonomyGateWarnTests(unittest.TestCase):
    """Case 8: repo warnFor patterns exit 0 with action=warn."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_config(self, autonomy: dict) -> None:
        config_path = self.workdir / ".build-loop" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"autonomy": autonomy}))

    def test_warn_pattern_exits_zero(self) -> None:
        """warnFor pattern exits 0 with action=warn."""
        self._write_config({"warnFor": ["touch-prod-config*"]})
        result = run(self.workdir, "ops", "touch-prod-config /etc/foo")
        data = envelope(result)
        self.assertEqual(data["action"], "warn", msg=str(data))
        self.assertEqual(result.returncode, 0, msg=f"Expected exit 0, got {result.returncode}")

    def test_warn_appears_in_envelope(self) -> None:
        """warnFor match surfaces action=warn, list_source=config, and matched_rule in envelope."""
        self._write_config({"warnFor": ["touch-prod-config*"]})
        result = run(self.workdir, "ops", "touch-prod-config /etc/foo")
        data = envelope(result)
        self.assertEqual(data["action"], "warn", msg=str(data))
        self.assertEqual(data["list_source"], "config", msg=str(data))
        self.assertEqual(data["matched_rule"], "touch-prod-config*", msg=str(data))

    def test_confirm_for_wins_over_warn_for_on_tie(self) -> None:
        """When a command matches both confirmFor and warnFor, confirmFor wins (stricter verdict)."""
        self._write_config({"confirmFor": ["foo*"], "warnFor": ["foo*"]})
        result = run(self.workdir, "ops", "foo bar")
        data = envelope(result)
        self.assertEqual(
            data["action"],
            "confirm",
            msg=f"Expected confirm (stricter wins on tie), got {data['action']!r}. Full: {data}",
        )
        self.assertEqual(result.returncode, 1, msg=f"Expected exit 1, got {result.returncode}")


class AutonomyGateSelfTestTests(unittest.TestCase):
    """Verify --self-test exits 0."""

    def test_self_test_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--self-test"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"--self-test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )
        self.assertIn("passed", result.stdout.lower(), msg=result.stdout)


class AutonomyGateFlagsTests(unittest.TestCase):
    """Verify flags are surfaced in the envelope."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_flags_default_to_true(self) -> None:
        result = run(self.workdir, "test", "edit scripts/foo.py")
        data = envelope(result)
        self.assertIn("flags", data)
        flags = data["flags"]
        self.assertTrue(flags["autoFixGuidance"])
        self.assertTrue(flags["autoExecuteOpenRecs"])

    def test_flags_read_from_config(self) -> None:
        config_path = self.workdir / ".build-loop" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({
                "autonomy": {
                    "autoFixGuidance": False,
                    "autoExecuteOpenRecs": True,
                }
            })
        )
        result = run(self.workdir, "test", "edit scripts/foo.py")
        data = envelope(result)
        flags = data["flags"]
        self.assertFalse(flags["autoFixGuidance"])
        self.assertTrue(flags["autoExecuteOpenRecs"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
