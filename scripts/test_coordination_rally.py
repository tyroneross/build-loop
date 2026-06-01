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
import stat
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import coordination_rally as cr  # noqa: E402
from rally_point import changes, channel_paths, presence  # noqa: E402
from rally_point import discovery_bridge as _bridge  # test isolation
from rally_point.post import post  # noqa: E402


class CoordinationRallyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coord-rally-"))
        self.apps = self.tmp / "apps"
        self.workdir = self.tmp / "Example-iOS-App"
        self.workdir.mkdir()
        self._old_apps_root = os.environ.get("BUILD_LOOP_APPS_ROOT")
        self._old_internal_only = os.environ.get("BUILD_LOOP_BRIDGE_INTERNAL_ONLY")
        self._old_agent_rally_binary = os.environ.get("AGENT_RALLY_BINARY")
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
        if self._old_internal_only is None:
            os.environ.pop("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", None)
        else:
            os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = self._old_internal_only
        if self._old_agent_rally_binary is None:
            os.environ.pop("AGENT_RALLY_BINARY", None)
        else:
            os.environ["AGENT_RALLY_BINARY"] = self._old_agent_rally_binary
        _bridge.clear_cache()
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

    def test_repo_local_rally_cli_writes_dot_rally_not_global_hub(self):
        fake = self.tmp / "bin" / "rally"
        self._write_fake_repo_local_rally(fake)
        os.environ["AGENT_RALLY_BINARY"] = str(fake)
        os.environ.pop("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", None)
        _bridge.clear_cache()

        result = cr.rally(
            workdir=self.workdir,
            session_id="codex-local-rally",
            message="native visible handoff",
            tool="codex",
            model="gpt-5",
            to="claude_code",
            owns=["src/app.py"],
            verify=True,
        )

        self.assertEqual(result["resolved_via"], "repo-local-rally-cli")
        self.assertEqual(
            Path(result["channel_dir"]).resolve(),
            (self.workdir / ".rally").resolve(),
        )
        self.assertTrue(result["presence_written"])
        self.assertTrue(result["posted"])
        self.assertEqual(result["channel_revision"], 1)
        self.assertTrue((self.workdir / ".rally" / "log" / "repo.jsonl").exists())
        self.assertFalse(
            (self.workdir / ".rally" / "changes.jsonl").exists(),
            "repo-local native mode must not create an invisible changes.jsonl side channel",
        )
        self.assertFalse(
            self.apps.exists(),
            "repo-local native mode must not write to ~/.agent-rally-point/apps fallback",
        )

    def test_post_routes_repo_local_channel_through_native_rally(self):
        fake = self.tmp / "bin" / "rally"
        self._write_fake_repo_local_rally(fake)
        os.environ["AGENT_RALLY_BINARY"] = str(fake)
        os.environ.pop("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", None)
        _bridge.clear_cache()

        channel = self.workdir / ".rally"
        seq = post(
            channel_dir=channel,
            kind="handoff",
            tool="codex",
            model="gpt-5",
            run_id="run-1",
            app_slug=self.workdir.name,
            payload={
                "message": "native post handoff",
                "to": "claude_code",
                "ownership": {
                    "owns": ["src/app.py"],
                    "does_not_own": [],
                    "interface_contract": "native handoff is visible in .rally",
                    "integration_checkpoint": "read .rally/log",
                },
            },
            workdir=self.workdir,
        )

        self.assertEqual(seq, 1)
        self.assertTrue((channel / "log" / "repo.jsonl").exists())
        self.assertFalse(
            (channel / "changes.jsonl").exists(),
            "post() must not create a build-loop-only side channel in native mode",
        )

    def _write_fake_repo_local_rally(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"#!{sys.executable}\n"
            "import json, pathlib, sys, datetime\n"
            "args = sys.argv[1:]\n"
            "if not args:\n"
            "    print('Usage: rally enter --tool <tool>')\n"
            "    print('       rally say <kind> --tool <tool> --subject <subject>')\n"
            "    print('       rally whoami [--tool <id>] [--json]')\n"
            "    raise SystemExit(0)\n"
            "repo = pathlib.Path.cwd()\n"
            "rally = repo / '.rally'\n"
            "log = rally / 'log' / 'repo.jsonl'\n"
            "def opt(name, default=None):\n"
            "    if name in args:\n"
            "        i = args.index(name)\n"
            "        if i + 1 < len(args):\n"
            "            return args[i + 1]\n"
            "    return default\n"
            "def append(kind):\n"
            "    log.parent.mkdir(parents=True, exist_ok=True)\n"
            "    seq = 1\n"
            "    if log.exists():\n"
            "        seq = sum(1 for line in log.read_text().splitlines() if line.strip()) + 1\n"
            "    fact = {\n"
            "        'created_at': '2026-06-01T00:00:00Z', 'event_id': f'fact_{seq}',\n"
            "        'kind': kind, 'tool': opt('--tool', 'unknown'),\n"
            "        'target': opt('--to'), 'subject': opt('--subject'),\n"
            "        'summary': opt('--summary'), 'status': opt('--status'),\n"
            "        'scope': [], 'seq': 0, 'schema': 'agent-rally.fact.v1'}\n"
            "    row = {'seq': seq, 'occurred_at': '2026-06-01T00:00:00Z',\n"
            "           'event_type': kind, 'payload': fact, 'engagement': repo.name}\n"
            "    with log.open('a', encoding='utf-8') as fh:\n"
            "        fh.write(json.dumps(row, separators=(',', ':')) + '\\n')\n"
            "    return seq, fact\n"
            "if args == ['whoami', '--json']:\n"
            "    print(json.dumps({'ok': True, 'data': {'whoami': {\n"
            "        'repo_root': str(repo), 'repo_id': repo.name,\n"
            "        'worktree': str(repo), 'cwd': str(repo), 'build_id': 'test-local'}}}))\n"
            "    raise SystemExit(0)\n"
            "if args and args[0] == 'enter':\n"
            "    rally.mkdir(parents=True, exist_ok=True)\n"
            "    print(json.dumps({'ok': True, 'data': {'enter': {'session_id': opt('--session-id')}}}))\n"
            "    raise SystemExit(0)\n"
            "if len(args) >= 2 and args[0] == 'say':\n"
            "    seq, fact = append(args[1])\n"
            "    fact['seq'] = seq\n"
            "    print(json.dumps({'ok': True, 'data': {'say': {'fact': fact}, 'verified': {'seq': seq}}}))\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(2)\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)


if __name__ == "__main__":
    unittest.main()
