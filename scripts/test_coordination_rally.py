#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/coordination_rally.py."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import coordination_rally as cr  # noqa: E402
from rally_point import changes, channel_paths, presence  # noqa: E402
from rally_point import discovery_bridge as _bridge  # test isolation


class CoordinationRallyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coord-rally-"))
        self.apps = self.tmp / "apps"
        self.workdir = self.tmp / "Example-iOS-App"
        self.workdir.mkdir()
        self._old_apps_root = os.environ.get("BUILD_LOOP_APPS_ROOT")
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.apps)
        os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = "1"
        from rally_point import discovery_bridge as _bridge
        _bridge.clear_cache()
        subprocess.run(["git", "init"], cwd=self.workdir, check=True, capture_output=True)

    def tearDown(self):
        if self._old_apps_root is None:
            os.environ.pop("BUILD_LOOP_APPS_ROOT", None)
        else:
            os.environ["BUILD_LOOP_APPS_ROOT"] = self._old_apps_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rally_writes_presence_and_handoff(self):
        result = cr.rally(
            workdir=self.workdir,
            session_id="codex-rally-test",
            message="Codex is present for test coordination.",
            tool="codex",
            model="gpt-5",
            to="claude_code",
            owns=[],
            does_not_own=["ExampleApp/Views/HomeView.swift"],
        )

        self.assertEqual(result["action"], "rally-point-posted")
        self.assertEqual(result["app_slug"], "example-ios-app")
        self.assertTrue(result["presence_written"])
        self.assertEqual(result["channel_revision"], 1)

        channel = channel_paths.app_channel_dir("example-ios-app")
        peers = presence.read_active_presence(channel, exclude_session="reader")
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]["session_id"], "codex-rally-test")
        self.assertEqual(peers[0]["phase"], "rally-point")

        records, _ = changes.read_changes_since(channel, 0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "handoff")
        payload = records[0]["payload"]
        self.assertEqual(payload["action"], "rally-point")
        self.assertEqual(payload["to"], "claude_code")
        self.assertEqual(
            payload["ownership"]["does_not_own"],
            ["ExampleApp/Views/HomeView.swift"],
        )

    def test_cli_defaults_session_id_and_splits_csv(self):
        cmd = [
            sys.executable,
            str(HERE / "coordination_rally.py"),
            "--workdir", str(self.workdir),
            "--message", "hello",
            "--owns", "a.py,b.py",
            "--does-not-own", "c.py",
            "--json",
        ]
        run = subprocess.run(cmd, check=True, capture_output=True, text=True)
        result = json.loads(run.stdout)
        self.assertEqual(result["ownership"]["owns"], ["a.py", "b.py"])
        self.assertEqual(result["ownership"]["does_not_own"], ["c.py"])
        self.assertTrue(result["session_id"].startswith("codex-rally-"))

    def test_verify_mode_confirms_revision_advanced_and_record_exists(self):
        result = cr.rally(
            workdir=self.workdir,
            session_id="codex-rally-verify",
            message="verify this post",
            tool="codex",
            model="gpt-5",
            does_not_own=["ExampleApp/Views/HomeView.swift"],
            verify=True,
        )

        self.assertTrue(result["posted"])
        self.assertEqual(result["verify"]["before_revision"], 0)
        self.assertEqual(result["verify"]["after_revision"], 1)
        self.assertEqual(result["verify"]["matching_record_count"], 1)

    def test_cli_verify_emits_posted_true(self):
        cmd = [
            sys.executable,
            str(HERE / "coordination_rally.py"),
            "--workdir", str(self.workdir),
            "--message", "hello",
            "--does-not-own", "ExampleApp/Views/HomeView.swift",
            "--verify",
            "--json",
        ]
        run = subprocess.run(cmd, check=True, capture_output=True, text=True)
        result = json.loads(run.stdout)

        self.assertTrue(result["posted"])
        self.assertEqual(result["verify"]["matching_record_count"], 1)

    def test_cli_rejects_empty_ownership_scope_with_nonzero_exit(self):
        """Codex variance (rev 219): without --owns and --does-not-own the
        CLI used to exit 0 with channel_revision=null / posted=false because
        the MECE gate silently rejected inside post(). The CLI now rejects
        at the argparse boundary with exit code 2 and a stderr message.
        """
        cmd = [
            sys.executable,
            str(HERE / "coordination_rally.py"),
            "--workdir", str(self.workdir),
            "--message", "hello",
            "--verify",
            "--json",
        ]
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn("--owns", run.stderr)
        self.assertIn("--does-not-own", run.stderr)
        # Should not have emitted a success envelope on stdout.
        self.assertEqual(run.stdout, "")

    def test_cli_rejects_empty_ownership_scope_without_verify(self):
        """Same defense without --verify: empty/empty is rejected at CLI."""
        cmd = [
            sys.executable,
            str(HERE / "coordination_rally.py"),
            "--workdir", str(self.workdir),
            "--message", "hello",
            "--json",
        ]
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn("--owns", run.stderr)


if __name__ == "__main__":
    unittest.main()
