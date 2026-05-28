#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rust ``rally`` bridge tests.

These tests pin the v0.4 cutover behavior: build-loop may discover a Rust
hash-chained channel, but it must write through the Rust CLI instead of
appending flat JSONL directly.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from rally_point import discovery_bridge
from rally_point.post import post
from coordination_rally import rally


class RustRallyBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="rust-rally-bridge-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        self.channel = self.tmp / "home" / ".agent-rally-point" / "apps" / "repo_fake"
        self.channel.mkdir(parents=True)
        self.calls = self.tmp / "calls.jsonl"
        self.fake_rally = self.tmp / "rally"
        self.fake_rally.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            f"channel = {str(self.channel)!r}\n"
            f"calls = {str(self.calls)!r}\n"
            "args = sys.argv[1:]\n"
            "if not args:\n"
            "    print('usage: rally start <tool> [--session-id <id>] [--human]')\n"
            "    print('       rally stop <tool> [--session-id <id>] [--reason <text>] [--json]')\n"
            "    print('       rally post --kind <kind> [--payload <json>] [--subject <text>] [--json]')\n"
            "    raise SystemExit(2)\n"
            "if args == ['setup', '--json']:\n"
            "    print(json.dumps({'ok': True, 'schema': 'agent-rally.command.setup.v1', 'channel': channel}))\n"
            "    raise SystemExit(0)\n"
            "if len(args) >= 2 and args[:2] == ['start', 'codex']:\n"
            "    with open(calls, 'a', encoding='utf-8') as fh:\n"
            "        fh.write(json.dumps(args) + '\\n')\n"
            "    print(json.dumps({'ok': True, 'schema': 'agent-rally.command.start.v1'}))\n"
            "    raise SystemExit(0)\n"
            "if len(args) >= 2 and args[:2] in (['post', '--json'], ['handoff', '--json']):\n"
            "    with open(calls, 'a', encoding='utf-8') as fh:\n"
            "        fh.write(json.dumps(args) + '\\n')\n"
            "    print(json.dumps({'ok': True, 'schema': 'agent-rally.command.post.v1', 'local_seq': 7}))\n"
            "    raise SystemExit(0)\n"
            "if args == ['replay', '--json']:\n"
            "    print(json.dumps({'ok': True, 'data': {'events': [\n"
            "        {'local_seq': 7, 'event': {'kind': 'handoff'}}\n"
            "    ]}}))\n"
            "    raise SystemExit(0)\n"
            "if args == ['checkpoint', 'status', '--json']:\n"
            "    print(json.dumps({'ok': True, 'data': {'checkpoint': {'valid': False}}}))\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(2)\n",
            encoding="utf-8",
        )
        self.fake_rally.chmod(self.fake_rally.stat().st_mode | stat.S_IXUSR)
        self._old_binary = os.environ.get("AGENT_RALLY_BINARY")
        self._old_internal = os.environ.get("BUILD_LOOP_BRIDGE_INTERNAL_ONLY")
        os.environ["AGENT_RALLY_BINARY"] = str(self.fake_rally)
        os.environ.pop("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", None)
        discovery_bridge.clear_cache()

    def tearDown(self) -> None:
        if self._old_binary is None:
            os.environ.pop("AGENT_RALLY_BINARY", None)
        else:
            os.environ["AGENT_RALLY_BINARY"] = self._old_binary
        if self._old_internal is None:
            os.environ.pop("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", None)
        else:
            os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = self._old_internal
        discovery_bridge.clear_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_discovers_rust_rally_channel(self) -> None:
        envelope = discovery_bridge.resolve(self.repo)

        self.assertEqual(envelope.resolved_via, "rust-cli")
        self.assertEqual(envelope.protocol_version, "2.0")
        self.assertEqual(envelope.channel_layout, "hash-chain")
        self.assertEqual(envelope.channel_dir, str(self.channel))

    def test_post_routes_handoffs_through_rust_rally(self) -> None:
        seq = post(
            channel_dir=self.channel,
            kind="handoff",
            tool="codex",
            model="gpt-5",
            run_id="run-1",
            app_slug="repo_fake",
            payload={"message": "take this", "session_id": "s1"},
            workdir=self.repo,
        )

        self.assertEqual(seq, 7)
        self.assertFalse((self.channel / "changes.jsonl").exists())
        posted_args = json.loads(self.calls.read_text(encoding="utf-8").strip())
        self.assertEqual(posted_args[:2], ["handoff", "--json"])
        self.assertIn("--notes", posted_args)

    def test_verified_coordination_rally_accepts_stale_checkpoint_when_event_landed(self) -> None:
        result = rally(
            workdir=self.repo,
            session_id="s2",
            message="coordinate",
            tool="codex",
            model="gpt-5",
            run_id="run-1",
            owns=["scripts/foo.py"],
            verify=True,
        )

        self.assertTrue(result["presence_written"])
        self.assertEqual(result["channel_revision"], 7)
        self.assertTrue(result["posted"])
        self.assertEqual(result["verify"]["protocol"], "rust-cli")
        self.assertEqual(result["verify"]["matching_record_count"], 1)
        self.assertFalse(result["verify"]["checkpoint_valid"])


if __name__ == "__main__":
    unittest.main()
