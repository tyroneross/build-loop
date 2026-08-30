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
            "import json, os, sys\n"
            f"repo = {str(self.repo)!r}\n"
            f"calls = {str(self.calls)!r}\n"
            "args = sys.argv[1:]\n"
            "if not args:\n"
            "    print('usage: rally enter --tool <tool>')\n"
            "    print('       rally say <kind> --tool <tool> --subject <subject>')\n"
            "    print('       rally whoami [--tool <id>] [--json]')\n"
            "    raise SystemExit(2)\n"
            "if args == ['version', '--json']:\n"
            "    print(json.dumps({'ok': True, 'product': 'rally',\n"
            "        'schema': 'agent-rally.command.version.v1',\n"
            "        'data': {'version': {'version': 'test', 'build_id': 'test-native'}}}))\n"
            "    raise SystemExit(0)\n"
            "if args == ['status', '--json', 'read', '--tool', 'build_loop:discovery']:\n"
            "    if os.environ.get('FAKE_RALLY_STATUS_FAIL'):\n"
            "        print(json.dumps({'ok': False, 'product': 'rally'}))\n"
            "        raise SystemExit(1)\n"
            "    print(json.dumps({'ok': True, 'product': 'rally',\n"
            "        'schema': ('agent-rally.command.status_read.v2' if os.environ.get('FAKE_RALLY_STATUS_SCHEMA_SKEW') else 'agent-rally.command.status_read.v1'),\n"
            "        'data': {'status_read': {'states': []}}}))\n"
            "    raise SystemExit(0)\n"
            "if args == ['whoami', '--json']:\n"
            "    print(json.dumps({'ok': True, 'product': 'rally',\n"
            "      'schema': 'agent-rally.command.whoami.v1', 'data': {'whoami': {\n"
            "        'repo_root': repo, 'repo_id': 'repo', 'room_id': 'repo-room',\n"
            "        'worktree': repo, 'cwd': repo, 'build_id': 'test-native',\n"
            "        'host_runtime': {'ambiguous': bool(os.environ.get('FAKE_RALLY_AMBIGUOUS'))}}}}))\n"
            "    raise SystemExit(0)\n"
            # Real rally prints its fact-kind vocabulary here; the capability
            # gate probes it before every native post. Answer it (and do NOT
            # log it as a call) so the fake stays faithful to the real surface.
            "if args[:2] == ['say', '--help']:\n"
            "    print('Available positional items:')\n"
            "    print('    KIND   fact kind to post; one of: claim, claim.expired,')\n"
            "    print('           release, blocker, resolve, decision, artifact,')\n"
            "    print('           handoff, risk, lesson, session, wake, presence,')\n"
            "    print('           read, backlog-item, receipt, standby, mission')\n"
            "    raise SystemExit(0)\n"
            "if len(args) >= 2 and args[0] == 'say':\n"
            "    if os.environ.get('FAKE_RALLY_SAY_FAIL'):\n"
            "        print(json.dumps({'ok': False, 'product': 'rally'}))\n"
            "        raise SystemExit(1)\n"
            "    if os.environ.get('FAKE_RALLY_SAY_MALFORMED'):\n"
            "        print(json.dumps({'ok': True, 'product': 'rally',\n"
            "            'schema': 'agent-rally.command.say.v1', 'data': {'say': {}}}))\n"
            "        raise SystemExit(0)\n"
            "    with open(calls, 'a', encoding='utf-8') as fh:\n"
            "        fh.write(json.dumps(args) + '\\n')\n"
            "    def opt(name, default=None):\n"
            "        return args[args.index(name) + 1] if name in args else default\n"
            "    def opts(name):\n"
            "        return [args[i + 1] for i, value in enumerate(args[:-1]) if value == name]\n"
            "    fact = {'seq': 7, 'event_id': 'fact_7', 'kind': args[1],\n"
            "        'tool': opt('--tool'), 'subject': opt('--subject'),\n"
            "        'evidence': opts('--evidence'),\n"
            "        'scope': ['file:' + path for path in opts('--path')],\n"
            "        'from_session_id': os.environ.get('RALLY_SESSION_ID')}\n"
            "    if opt('--ref') is not None: fact['ref'] = opt('--ref')\n"
            "    if opt('--ref-standby') is not None: fact['ref'] = opt('--ref-standby')\n"
            "    print(json.dumps({'ok': True, 'product': 'rally',\n"
            "        'schema': 'agent-rally.command.say.v1',\n"
            "        'data': {'say': {'fact': fact}}}))\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(2)\n",
            encoding="utf-8",
        )
        self.fake_rally.chmod(self.fake_rally.stat().st_mode | stat.S_IXUSR)
        self._old_binary = os.environ.get("AGENT_RALLY_BINARY")
        self._old_internal = os.environ.get("BUILD_LOOP_BRIDGE_INTERNAL_ONLY")
        self._old_apps = os.environ.get("BUILD_LOOP_APPS_ROOT")
        self._old_status_fail = os.environ.get("FAKE_RALLY_STATUS_FAIL")
        self._old_say_fail = os.environ.get("FAKE_RALLY_SAY_FAIL")
        self._old_say_malformed = os.environ.get("FAKE_RALLY_SAY_MALFORMED")
        self._old_ambiguous = os.environ.get("FAKE_RALLY_AMBIGUOUS")
        self._old_status_schema_skew = os.environ.get("FAKE_RALLY_STATUS_SCHEMA_SKEW")
        os.environ["AGENT_RALLY_BINARY"] = str(self.fake_rally)
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.tmp / "build-loop-apps")
        os.environ.pop("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", None)
        os.environ.pop("FAKE_RALLY_STATUS_FAIL", None)
        os.environ.pop("FAKE_RALLY_SAY_FAIL", None)
        os.environ.pop("FAKE_RALLY_SAY_MALFORMED", None)
        os.environ.pop("FAKE_RALLY_AMBIGUOUS", None)
        os.environ.pop("FAKE_RALLY_STATUS_SCHEMA_SKEW", None)
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
        for key, old in (
            ("BUILD_LOOP_APPS_ROOT", self._old_apps),
            ("FAKE_RALLY_STATUS_FAIL", self._old_status_fail),
            ("FAKE_RALLY_SAY_FAIL", self._old_say_fail),
            ("FAKE_RALLY_SAY_MALFORMED", self._old_say_malformed),
            ("FAKE_RALLY_AMBIGUOUS", self._old_ambiguous),
            ("FAKE_RALLY_STATUS_SCHEMA_SKEW", self._old_status_schema_skew),
        ):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
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
            payload={
                "message": "take this",
                "session_id": "s1",
                "ownership": {
                    "owns": ["src/example.py"],
                    "does_not_own": [],
                    "allowed_tools": [],
                    "denied_tools": [],
                    "interface_contract": "edit only the owned path",
                    "integration_checkpoint": "return a tested commit",
                },
            },
            workdir=self.repo,
        )

        self.assertEqual(seq, 7)
        # No flat-JSONL shadow write: the binary owns the ledger.
        self.assertFalse((self.channel / "changes.jsonl").exists())
        posted_args = json.loads(self.calls.read_text(encoding="utf-8").strip())
        self.assertEqual(posted_args[:2], ["say", "handoff"])
        self.assertIn("--json", posted_args)

    def test_post_without_workdir_refuses_realistic_dot_rally_room(self) -> None:
        """Backend-less callers cannot create a shadow Rally changes log."""
        (self.channel / "facts.db").write_bytes(b"")
        (self.channel / "log").mkdir()

        seq = post(
            channel_dir=self.channel,
            kind="artifact",
            tool="codex",
            model="gpt-5",
            run_id="run-without-backend-context",
            app_slug="repo",
            payload={"subject": "must not shadow Rally"},
        )

        self.assertIsNone(seq)
        self.assertFalse((self.channel / "changes.jsonl").exists())
        self.assertFalse((self.channel / "revision").exists())

    def test_unhealthy_rally_room_selects_build_loop_backend(self) -> None:
        os.environ["FAKE_RALLY_STATUS_FAIL"] = "1"
        discovery_bridge.clear_cache()

        envelope = discovery_bridge.resolve(self.repo)

        self.assertEqual(envelope.resolved_via, "build-loop-internal")
        self.assertEqual(envelope.backend, "build-loop-local")
        self.assertEqual(envelope.transport, "fact-v1")
        self.assertEqual(envelope.raw["fallback_reason"], "rally_unhealthy")
        self.assertNotEqual(Path(envelope.channel_dir), self.channel)

    def test_ambiguous_native_host_refuses_without_local_fallback(self) -> None:
        os.environ["FAKE_RALLY_AMBIGUOUS"] = "1"
        discovery_bridge.clear_cache()

        envelope = discovery_bridge.resolve(self.repo)
        outcome: dict = {}
        revision = post(
            channel_dir=self.channel,
            kind="artifact",
            tool="cursor",
            model="cursor-agent",
            run_id="ambiguous-host",
            app_slug="repo",
            payload={"subject": "must not fork"},
            workdir=self.repo,
            outcome=outcome,
        )

        self.assertEqual(envelope.coordination_unavailable, "ambiguous_host")
        self.assertIsNone(revision)
        self.assertEqual(outcome["status"], "refused")
        self.assertFalse((Path(os.environ["BUILD_LOOP_APPS_ROOT"]) / "repo" / "changes.jsonl").exists())

    def test_native_schema_skew_refuses_without_local_fallback(self) -> None:
        os.environ["FAKE_RALLY_STATUS_SCHEMA_SKEW"] = "1"
        discovery_bridge.clear_cache()

        envelope = discovery_bridge.resolve(self.repo)

        self.assertEqual(envelope.coordination_unavailable, "incompatible_protocol")
        self.assertEqual(envelope.backend, "unavailable")
        self.assertEqual(envelope.transport, "none")
        self.assertFalse((Path(os.environ["BUILD_LOOP_APPS_ROOT"]) / "repo" / "changes.jsonl").exists())

    def test_native_say_failure_does_not_dual_write(self) -> None:
        os.environ["FAKE_RALLY_SAY_FAIL"] = "1"
        discovery_bridge.clear_cache()

        outcome: dict = {}
        revision = post(
            channel_dir=self.channel,
            kind="artifact",
            tool="cursor",
            model="cursor-agent",
            run_id="run-fallback",
            app_slug="repo",
            payload={"subject": "preserve this event"},
            workdir=self.repo,
            outcome=outcome,
        )

        fallback = Path(os.environ["BUILD_LOOP_APPS_ROOT"]) / "repo"
        self.assertIsNone(revision)
        self.assertEqual(outcome["status"], "rejected")
        self.assertEqual(outcome["backend"], "rally")
        self.assertFalse((self.channel / "changes.jsonl").exists())
        self.assertFalse((fallback / "changes.jsonl").exists())

    def test_malformed_native_success_does_not_dual_write(self) -> None:
        os.environ["FAKE_RALLY_SAY_MALFORMED"] = "1"
        discovery_bridge.clear_cache()
        outcome: dict = {}

        revision = post(
            channel_dir=self.channel,
            kind="artifact",
            tool="cursor",
            model="cursor-agent",
            run_id="run-malformed-success",
            app_slug="repo",
            payload={"subject": "preserve malformed response event"},
            workdir=self.repo,
            outcome=outcome,
        )

        fallback = Path(os.environ["BUILD_LOOP_APPS_ROOT"]) / "repo"
        self.assertIsNone(revision)
        self.assertEqual(outcome["status"], "invalid")
        self.assertEqual(outcome["backend"], "rally")
        self.assertEqual(outcome["transport"], "rally-cli")
        self.assertFalse((self.channel / "changes.jsonl").exists())
        self.assertFalse((fallback / "changes.jsonl").exists())

    def test_codex_claude_and_cursor_share_native_rally_transport(self) -> None:
        for tool in ("codex", "claude_code", "cursor"):
            self.assertEqual(
                post(
                    channel_dir=self.channel,
                    kind="artifact",
                    tool=tool,
                    model=f"{tool}-model",
                    run_id="host-matrix-native",
                    app_slug="repo",
                    payload={"subject": f"{tool} native conformance"},
                    workdir=self.repo,
                ),
                7,
            )
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        tools = [call[call.index("--tool") + 1] for call in calls]
        self.assertEqual(tools, ["codex", "claude_code", "cursor"])

    def test_codex_claude_and_cursor_share_build_loop_fallback(self) -> None:
        os.environ["FAKE_RALLY_STATUS_FAIL"] = "1"
        discovery_bridge.clear_cache()
        envelope = discovery_bridge.resolve(self.repo)
        fallback = Path(envelope.channel_dir)

        for expected_revision, tool in enumerate(
            ("codex", "claude_code", "cursor"), start=1
        ):
            self.assertEqual(
                post(
                    channel_dir=fallback,
                    kind="artifact",
                    tool=tool,
                    model=f"{tool}-model",
                    run_id="host-matrix-fallback",
                    app_slug="repo",
                    payload={"subject": f"{tool} fallback conformance"},
                    workdir=self.repo,
                ),
                expected_revision,
            )
        facts = [
            json.loads(line)
            for line in (fallback / "changes.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [fact["tool"] for fact in facts],
            ["codex", "claude_code", "cursor"],
        )


if __name__ == "__main__":
    unittest.main()
