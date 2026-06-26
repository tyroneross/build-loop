#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Native ``rally`` bridge tests (rally's REAL surface).

These tests pin that build-loop discovers a native rally channel and writes
through the rally CLI's real ``say``/``enter``/``whoami`` surface — never a
phantom ``setup``/``post``/``start``/``replay`` surface rally does not ship.

The historic ``rust-cli`` tier (gated on ``rally setup --json`` + a
``stop <tool>``/``post --kind`` help surface) was removed: that surface never
shipped, so the tier could never resolve a real binary. The single live native
path is ``repo-local-rally-cli``.
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


class NativeRallyBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="native-rally-bridge-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        # Native rally owns a repo-local .rally ledger.
        self.channel = self.repo / ".rally"
        self.channel.mkdir(parents=True)
        self.calls = self.tmp / "calls.jsonl"
        # Fake rally exposing rally's REAL surface: enter/say/whoami top-level
        # help, whoami --json (discovery), and say <kind> --json (writes).
        self.fake_rally = self.tmp / "rally"
        self.fake_rally.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"repo = {str(self.repo)!r}\n"
            f"calls = {str(self.calls)!r}\n"
            "args = sys.argv[1:]\n"
            "if not args:\n"
            "    print('usage: rally enter --tool <tool>')\n"
            "    print('       rally say <kind> --tool <tool> --subject <subject>')\n"
            "    print('       rally whoami [--tool <id>] [--json]')\n"
            "    raise SystemExit(2)\n"
            "if args == ['whoami', '--json']:\n"
            "    print(json.dumps({'ok': True, 'data': {'whoami': {\n"
            "        'repo_root': repo, 'repo_id': 'repo', 'worktree': repo,\n"
            "        'cwd': repo, 'build_id': 'test-native'}}}))\n"
            "    raise SystemExit(0)\n"
            "if len(args) >= 2 and args[0] == 'say':\n"
            "    with open(calls, 'a', encoding='utf-8') as fh:\n"
            "        fh.write(json.dumps(args) + '\\n')\n"
            "    print(json.dumps({'ok': True, 'data': {'say': {'fact': {'seq': 7}}}}))\n"
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

    def test_discovers_native_rally_channel(self) -> None:
        envelope = discovery_bridge.resolve(self.repo)

        self.assertEqual(envelope.resolved_via, "repo-local-rally-cli")
        self.assertEqual(envelope.protocol_version, "1.0")
        self.assertEqual(envelope.channel_layout, "repo-local-rally")
        self.assertEqual(envelope.capability_level, "full")
        self.assertEqual(
            Path(envelope.channel_dir).resolve(), self.channel.resolve()
        )

    def test_post_routes_handoffs_through_rally_say(self) -> None:
        seq = post(
            channel_dir=self.channel,
            kind="handoff",
            tool="codex",
            model="gpt-5",
            run_id="run-1",
            app_slug="repo",
            payload={"message": "take this", "session_id": "s1"},
            workdir=self.repo,
        )

        self.assertEqual(seq, 7)
        # No flat-JSONL shadow write: the binary owns the ledger.
        self.assertFalse((self.channel / "changes.jsonl").exists())
        posted_args = json.loads(self.calls.read_text(encoding="utf-8").strip())
        self.assertEqual(posted_args[:2], ["say", "handoff"])
        self.assertIn("--json", posted_args)


if __name__ == "__main__":
    unittest.main()
