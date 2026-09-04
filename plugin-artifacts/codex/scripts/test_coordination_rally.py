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
from types import SimpleNamespace
from unittest.mock import patch

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
        self._old_rally_fake_root = os.environ.get("RALLY_FAKE_ROOT")
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
        if self._old_rally_fake_root is None:
            os.environ.pop("RALLY_FAKE_ROOT", None)
        else:
            os.environ["RALLY_FAKE_ROOT"] = self._old_rally_fake_root
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
        # Pin the fake ledger inside this test's workdir. Without it the stub
        # refuses to run rather than falling back to the caller's cwd.
        os.environ["RALLY_FAKE_ROOT"] = str(self.workdir)
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
        # Native Rally may append presence/tier facts before the handoff; the
        # receipt must report the positive revision of the requested write.
        self.assertGreater(result["channel_revision"], 0)
        self.assertTrue((self.workdir / ".rally" / "log" / "repo.jsonl").exists())
        self.assertFalse(
            (self.workdir / ".rally" / "changes.jsonl").exists(),
            "repo-local native mode must not create an invisible changes.jsonl side channel",
        )
        self.assertFalse(
            self.apps.exists(),
            "repo-local native mode must not write to ~/.agent-rally-point/apps fallback",
        )

    def test_native_rally_uses_session_actor_but_keeps_host_metadata(self):
        native = SimpleNamespace(
            app_slug="example-ios-app",
            channel_dir=str(self.workdir / ".rally"),
            resolved_via="repo-local-rally-cli",
            backend="rally",
            transport="rally-cli",
        )
        context = SimpleNamespace(
            envelope=native,
            local_channel_dir=channel_paths.app_channel_dir("example-ios-app"),
            native=True,
        )
        presence_result = SimpleNamespace(
            ok=True,
            precommit_unavailable=False,
            status="ok",
            reason=None,
            backend="rally",
        )

        def native_post(**kwargs):
            kwargs["outcome"].update(
                status="posted", backend="rally", transport="rally-cli", revision=7
            )
            return 7

        with patch.object(cr, "resolve_context", return_value=context):
            with patch.object(
                cr, "write_backend_presence", return_value=presence_result
            ) as write_presence:
                with patch.object(cr, "post", side_effect=native_post) as write_post:
                    result = cr.rally(
                        workdir=self.workdir,
                        session_id="thread-a",
                        message="native actor identity",
                        tool="codex",
                        owns=["src/app.py"],
                    )

        self.assertEqual(write_presence.call_args.kwargs["tool"], "codex:thread-a")
        self.assertEqual(write_presence.call_args.kwargs["session_id"], "thread-a")
        posted = write_post.call_args.kwargs
        self.assertEqual(posted["tool"], "codex:thread-a")
        self.assertEqual(posted["payload"]["from"], "codex")
        self.assertEqual(posted["payload"]["host_tool"], "codex")
        self.assertEqual(posted["payload"]["session_id"], "thread-a")
        self.assertEqual(result["tool"], "codex")
        self.assertEqual(result["rally_tool"], "codex:thread-a")

    def test_post_routes_repo_local_channel_through_native_rally(self):
        fake = self.tmp / "bin" / "rally"
        self._write_fake_repo_local_rally(fake)
        os.environ["AGENT_RALLY_BINARY"] = str(fake)
        # Pin the fake ledger inside this test's workdir. Without it the stub
        # refuses to run rather than falling back to the caller's cwd.
        os.environ["RALLY_FAKE_ROOT"] = str(self.workdir)
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
                    "allowed_tools": [],
                    "denied_tools": [],
                    "interface_contract": "native handoff is visible in .rally",
                    "integration_checkpoint": "read .rally/log",
                },
            },
            workdir=self.workdir,
        )

        self.assertIsNotNone(seq)
        self.assertGreater(seq, 0)
        self.assertTrue((channel / "log" / "repo.jsonl").exists())
        self.assertFalse(
            (channel / "changes.jsonl").exists(),
            "post() must not create a build-loop-only side channel in native mode",
        )

    def test_result_reports_actual_backend_after_native_post_failover(self):
        native_log = self.workdir / ".rally" / "log" / "repo.jsonl"
        native_log.parent.mkdir(parents=True)
        native_log.write_text(
            json.dumps(
                {
                    "seq": 100,
                    "occurred_at": "2026-08-14T00:00:00Z",
                    "event_type": "artifact",
                    "payload": {"kind": "artifact", "tool": "peer", "seq": 100},
                }
            )
            + "\n"
        )
        native = SimpleNamespace(
            app_slug="example-ios-app",
            channel_dir=str(self.workdir / ".rally"),
            resolved_via="repo-local-rally-cli",
            backend="rally",
            transport="rally-cli",
            coordination_unavailable=None,
        )

        def fallback_post(**kwargs):
            fallback = channel_paths.app_channel_dir("example-ios-app")
            local_revision = post(
                channel_dir=fallback,
                kind=kwargs["kind"],
                tool=kwargs["tool"],
                model=kwargs["model"],
                run_id=kwargs["run_id"],
                app_slug=kwargs["app_slug"],
                payload=kwargs["payload"],
            )
            kwargs["outcome"].update(
                {
                    "status": "posted",
                    "backend": "build-loop-local",
                    "transport": "fact-v1",
                    "revision": local_revision,
                }
            )
            return local_revision

        context = SimpleNamespace(
            envelope=native,
            local_channel_dir=channel_paths.app_channel_dir("example-ios-app"),
            native=True,
        )
        presence_result = SimpleNamespace(
            ok=True,
            precommit_unavailable=False,
            status="ok",
            reason=None,
        )
        with patch.object(cr, "resolve_context", return_value=context):
            with patch.object(cr, "write_backend_presence", return_value=presence_result):
                with patch.object(cr, "post", side_effect=fallback_post):
                    result = cr.rally(
                        workdir=self.workdir,
                        session_id="codex-native-failover",
                        message="preserve this handoff",
                        tool="codex",
                        model="gpt-5",
                        owns=["src/app.py"],
                        verify=True,
                    )

        self.assertEqual(result["backend"], "build-loop-local")
        self.assertEqual(result["transport"], "fact-v1")
        self.assertEqual(result["resolved_via"], "build-loop-internal")
        self.assertTrue(result["posted"])
        self.assertEqual(result["verify"]["before_revision"], 0)
        self.assertEqual(result["verify"]["after_revision"], 1)

    def _write_fake_repo_local_rally(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys, datetime\n"
            "args = sys.argv[1:]\n"
            "if not args:\n"
            "    print('Usage: rally enter --tool <tool>')\n"
            "    print('       rally say <kind> --tool <tool> --subject <subject>')\n"
            "    print('       rally whoami [--tool <id>] [--json]')\n"
            "    raise SystemExit(0)\n"
            # NEVER Path.cwd(). This stub is invoked by production code that
            # does not always pass cwd=, so cwd is the pytest process's
            # directory -- the real repo -- while the test's workdir is a temp
            # dir. Rooting the ledger at cwd wrote synthetic fixture facts
            # straight into the developer's own .rally/log/repo.jsonl, and the
            # suite still reported all green. RALLY_FAKE_ROOT is set by the
            # test; refuse to run without it rather than guess.
            "_root = os.environ.get('RALLY_FAKE_ROOT')\n"
            "if not _root:\n"
            "    print('fake rally: RALLY_FAKE_ROOT unset; refusing to write outside the fixture', file=sys.stderr)\n"
            "    raise SystemExit(3)\n"
            "repo = pathlib.Path(_root)\n"
            "rally = repo / '.rally'\n"
            "log = rally / 'log' / 'repo.jsonl'\n"
            "def opt(name, default=None):\n"
            "    if name in args:\n"
            "        i = args.index(name)\n"
            "        if i + 1 < len(args):\n"
            "            return args[i + 1]\n"
            "    return default\n"
            "def opts(name):\n"
            "    return [args[i + 1] for i, value in enumerate(args[:-1]) if value == name]\n"
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
            "        'scope': ['file:' + p.removeprefix('file:').removeprefix('./') for p in opts('--path')],\n"
            "        'evidence': opts('--evidence'), 'ref': opt('--ref'), 'ref_id': opt('--ref'),\n"
            "        'from_session_id': os.environ.get('RALLY_SESSION_ID'),\n"
            "        'seq': 0, 'schema': 'agent-rally.fact.v1'}\n"
            "    row = {'seq': seq, 'occurred_at': '2026-06-01T00:00:00Z',\n"
            "           'event_type': kind, 'payload': fact, 'engagement': repo.name}\n"
            "    with log.open('a', encoding='utf-8') as fh:\n"
            "        fh.write(json.dumps(row, separators=(',', ':')) + '\\n')\n"
            "    return seq, fact\n"
            "if args == ['version', '--json']:\n"
            "    print(json.dumps({'ok': True, 'product': 'rally',\n"
            "      'schema': 'agent-rally.command.version.v1',\n"
            "      'data': {'version': {'version': 'test', 'build_id': 'test-local'}}}))\n"
            "    raise SystemExit(0)\n"
            "if args == ['status', '--json', 'read', '--tool', 'build_loop:discovery']:\n"
            "    print(json.dumps({'ok': True, 'product': 'rally',\n"
            "      'schema': 'agent-rally.command.status_read.v1',\n"
            "      'data': {'status_read': {'states': []}}}))\n"
            "    raise SystemExit(0)\n"
            "if args == ['whoami', '--json']:\n"
            "    print(json.dumps({'ok': True, 'product': 'rally',\n"
            "      'schema': 'agent-rally.command.whoami.v1', 'data': {'whoami': {\n"
            "        'repo_root': str(repo), 'repo_id': repo.name,\n"
            "        'room_id': repo.name + '-room', 'worktree': str(repo),\n"
            "        'cwd': str(repo), 'build_id': 'test-local',\n"
            "        'host_runtime': {'ambiguous': False}}}}))\n"
            "    raise SystemExit(0)\n"
            "if args == ['room', '--json']:\n"
            "    facts = []\n"
            "    if log.exists():\n"
            "        facts = [json.loads(line)['payload'] for line in log.read_text().splitlines() if line.strip()]\n"
            "    closed = {f.get('ref') or f.get('ref_id') for f in facts if f.get('kind') in {'release', 'resolve', 'receipt'}}\n"
            "    active = [f for f in facts if f.get('kind') == 'claim' and f.get('event_id') not in closed]\n"
            "    print(json.dumps({'ok': True, 'product': 'rally',\n"
            "      'schema': 'agent-rally.command.room.v1',\n"
            "      'data': {'room': {'active_claims': active}}}))\n"
            "    raise SystemExit(0)\n"
            "if args and args[0] == 'enter':\n"
            "    rally.mkdir(parents=True, exist_ok=True)\n"
            "    presence = {'kind': 'presence', 'tool': opt('--tool'),\n"
            "        'from_session_id': os.environ.get('RALLY_SESSION_ID'),\n"
            "        'subject': 'presence: ' + opt('--tool', 'unknown'),\n"
            "        'seq': 1, 'event_id': 'presence_1'}\n"
            "    print(json.dumps({'ok': True, 'product': 'rally',\n"
            "      'schema': 'agent-rally.command.enter.v1',\n"
            "      'data': {'enter': {'tool': opt('--tool'), 'session_id': opt('--session-id')},\n"
            "               'append_outcomes': [{'fact': presence}]}}))\n"
            "    raise SystemExit(0)\n"
            # The kind-capability gate probes `<binary> say --help` before every
            # native post (scripts/rally_point/kind_capability.py). Real rally
            # answers it with its fact-kind vocabulary and writes nothing. This
            # stub used to fall through to the generic `say` branch and append a
            # fact whose kind was the literal string "--help", with every text
            # field null -- the exact rows that repeatedly poisoned
            # .rally/log/repo.jsonl and disabled rally for the whole repo.
            "if args[:2] == ['say', '--help']:\n"
            "    print('Available positional items:')\n"
            "    print('    KIND   fact kind to post; one of: claim, release,')\n"
            "    print('           blocker, resolve, decision, artifact, handoff,')\n"
            "    print('           risk, lesson, session, wake, presence, receipt')\n"
            "    raise SystemExit(0)\n"
            # Belt and braces: a flag can never be a fact kind. Even if a new
            # probe shape reaches here, refuse rather than write a null row.
            "if len(args) >= 2 and args[0] == 'say' and args[1].startswith('-'):\n"
            "    print('rally: not a fact kind: ' + args[1], file=sys.stderr)\n"
            "    raise SystemExit(2)\n"
            "if len(args) >= 2 and args[0] == 'say':\n"
            "    seq, fact = append(args[1])\n"
            "    fact['seq'] = seq\n"
            "    print(json.dumps({'ok': True, 'product': 'rally',\n"
            "      'schema': 'agent-rally.command.say.v1',\n"
            "      'data': {'say': {'fact': fact}, 'verified': {'seq': seq}}}))\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(2)\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)


if __name__ == "__main__":
    unittest.main()


class RepoLedgerIsNeverTouchedByTests(unittest.TestCase):
    """The suite must not write into the repo's own .rally ledger.

    This class exists because the leak it guards was silent: the fake rally
    stub rooted its ledger at Path.cwd(), so running pytest from the repo root
    appended synthetic facts (kind "--help", every text field null) straight
    into .rally/log/repo.jsonl while reporting all green. rally's reader then
    refused the whole segment on the first null subject, which disabled every
    handoff, claim, and room read in this repo -- twice, on 2026-09-01 and
    again on 2026-09-02 after the first cleanup.
    """

    def test_running_this_module_does_not_append_to_the_repo_ledger(self):
        repo_root = Path(__file__).resolve().parents[1]
        ledger = repo_root / ".rally" / "log" / "repo.jsonl"
        before = ledger.read_bytes() if ledger.exists() else b""

        env = dict(os.environ)
        env.pop("RALLY_FAKE_ROOT", None)
        subprocess.run(
            # Run the WHOLE module, not a -k subset. An earlier version
            # selected two names and missed the call site that actually leaked,
            # so this guard passed with the defect replanted. Exclude only this
            # guard class, to avoid recursing into ourselves.
            [sys.executable, "-m", "pytest", __file__,
             "-q", "-k", "not RepoLedgerIsNeverTouchedByTests"],
            cwd=str(repo_root), env=env, capture_output=True, text=True,
        )

        after = ledger.read_bytes() if ledger.exists() else b""
        self.assertEqual(
            before, after,
            "the test suite mutated the repo's real .rally/log/repo.jsonl; the "
            "fake rally stub must root its ledger at RALLY_FAKE_ROOT, never cwd",
        )

    def test_stub_refuses_a_flag_shaped_fact_kind(self):
        """`say --help` is a capability probe, not a fact. It must never append."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = root / "bin" / "rally"
            CoordinationRallyTests._write_fake_repo_local_rally(self, fake)
            fake.chmod(0o755)
            env = dict(os.environ)
            env["RALLY_FAKE_ROOT"] = str(root)
            run = subprocess.run([str(fake), "say", "--help"],
                                 capture_output=True, text=True, env=env)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("KIND", run.stdout)
            self.assertFalse(
                (root / ".rally" / "log" / "repo.jsonl").exists(),
                "the capability probe appended a fact",
            )
