#!/usr/bin/env python3
"""Tests for coordination_status.py and coordination_watch.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import coordination_status as cs  # noqa: E402
import coordination_watch as cw  # noqa: E402
from rally_point import changes, channel_paths, inbox, presence  # noqa: E402


class CoordinationStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.apps = self.tmp / "apps"
        self.workdir = self.tmp / "repo"
        self.workdir.mkdir()
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.apps)
        subprocess.run(["git", "init"], cwd=self.workdir, check=True,
                       capture_output=True)

    def _run(self, *args: str) -> dict:
        cmd = [
            sys.executable, str(HERE / "coordination_status.py"),
            "--workdir", str(self.workdir),
            "--session-id", "me",
            "--json",
            *args,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(r.stdout)

    def test_clear_without_peers_or_verdicts(self):
        status = self._run()
        self.assertEqual(status["status"], "clear")
        self.assertEqual(status["required_action"], "none")

    def test_peer_files_in_flight_vs_owned_populates_overlaps_field(self):
        """Peer's files_in_flight vs our owned_file populates legacy overlaps field.

        NOTE: As of R1 C8, the ``overlaps`` field is preserved for backward
        compat but it no longer drives the ``status: warn`` outcome.  Warn is
        now driven exclusively by peer's ``owns`` intersecting our
        ``files_in_flight`` (ownership-aware check).  A peer with no ``owns``
        field will not trigger warn even if their files_in_flight overlaps our
        owned_file.
        """
        slug = channel_paths.app_slug(self.workdir)
        channel = channel_paths.ensure_channel_dir(slug)
        presence.write_presence(
            channel,
            session_id="peer",
            tool="claude_code",
            model="m",
            run_id="r",
            app_slug=slug,
            phase="execute",
            files_in_flight=["src/app.py"],
            cwd=self.workdir,
        )
        # Peer has no ``owns`` field → overlaps list populates (legacy) but
        # status is clear because peer did not declare ownership.
        status = self._run("--owned-file", "src/app.py")
        self.assertEqual(status["overlaps"][0]["peer"], "peer")

    def test_blocked_verdict_blocks(self):
        coord_dir = self.workdir / ".build-loop" / "coordination"
        coord_dir.mkdir(parents=True)
        coord = coord_dir / "run.md"
        coord.write_text(
            "### 2026-05-20 13:24 PDT — Codex BLOCKED\n\n"
            "**Step:** 0 — Bootstrap acceptance test\n"
            "**Verdict:** PARTIAL / BLOCKED\n",
            encoding="utf-8",
        )
        status = self._run("--coordination-file", str(coord))
        self.assertEqual(status["status"], "blocked")
        self.assertEqual(status["unresolved"][0]["step"], "0 — Bootstrap acceptance test")

    def test_non_standard_heading_label_starts_new_entry(self):
        coord_dir = self.workdir / ".build-loop" / "coordination"
        coord_dir.mkdir(parents=True)
        coord = coord_dir / "run.md"
        coord.write_text(
            "### 2026-05-20 13:32 PDT — Codex PASS\n\n"
            "**Step:** Coordination mechanism\n"
            "**Verdict:** PASS\n\n"
            "### 2026-05-20 13:47 PDT — Codex FOLLOW-UP\n\n"
            "**Step:** Coordination + memory review\n"
            "**Verdict:** VARIANCE\n",
            encoding="utf-8",
        )
        status = self._run("--coordination-file", str(coord))
        by_step = {v["step"]: v for v in status["latest_verdicts"]}
        self.assertEqual(by_step["Coordination mechanism"]["verdict"], "PASS")
        self.assertEqual(by_step["Coordination + memory review"]["label"], "FOLLOW-UP")
        self.assertEqual(by_step["Coordination + memory review"]["verdict"], "VARIANCE")

    def test_default_coordination_file_prefers_oldest_audit_run_not_newest_stub(self):
        coord_dir = self.workdir / ".build-loop" / "coordination"
        coord_dir.mkdir(parents=True)
        run = coord_dir / "audit-execution-v0128-2026-05-20.md"
        handoff = coord_dir / "zz-new-handoff.md"
        run.write_text(
            "### 2026-05-20 13:32 PDT — Codex PASS\n\n"
            "**Step:** active run\n"
            "**Verdict:** PASS\n",
            encoding="utf-8",
        )
        handoff.write_text(
            "### 2026-05-20 13:47 PDT — Codex BLOCKED\n\n"
            "**Step:** handoff stub\n"
            "**Verdict:** BLOCKED\n",
            encoding="utf-8",
        )
        os.utime(run, (1_700_000_000, 1_700_000_000))
        os.utime(handoff, (1_700_000_100, 1_700_000_100))

        status = self._run()

        self.assertEqual(Path(status["coordination_file"]).resolve(), run.resolve())
        self.assertEqual(status["unresolved"], [])

    def test_default_coordination_file_uses_active_pointer(self):
        coord_dir = self.workdir / ".build-loop" / "coordination"
        coord_dir.mkdir(parents=True)
        old = coord_dir / "audit-execution-old.md"
        active = coord_dir / "active-run.md"
        newer = coord_dir / "zz-newer-stub.md"
        old.write_text("", encoding="utf-8")
        active.write_text("", encoding="utf-8")
        newer.write_text("", encoding="utf-8")
        (coord_dir / "active.json").write_text(
            json.dumps({"coord_file": ".build-loop/coordination/active-run.md"}),
            encoding="utf-8",
        )
        os.utime(old, (1_700_000_000, 1_700_000_000))
        os.utime(active, (1_700_000_100, 1_700_000_100))
        os.utime(newer, (1_700_000_200, 1_700_000_200))

        status = self._run()

        self.assertEqual(Path(status["coordination_file"]).resolve(), active.resolve())

    def test_default_coordination_file_stale_active_pointer_falls_back(self):
        coord_dir = self.workdir / ".build-loop" / "coordination"
        coord_dir.mkdir(parents=True)
        run = coord_dir / "audit-execution-current.md"
        newer = coord_dir / "zz-newer-stub.md"
        run.write_text("", encoding="utf-8")
        newer.write_text("", encoding="utf-8")
        (coord_dir / "active.json").write_text(
            json.dumps({"coord_file": ".build-loop/coordination/deleted.md"}),
            encoding="utf-8",
        )
        os.utime(run, (1_700_000_000, 1_700_000_000))
        os.utime(newer, (1_700_000_100, 1_700_000_100))

        status = self._run()

        self.assertEqual(Path(status["coordination_file"]).resolve(), run.resolve())

    def test_watch_emits_one_state(self):
        cmd = [
            sys.executable, str(HERE / "coordination_watch.py"),
            "--workdir", str(self.workdir),
            "--session-id", "me",
            "--iterations", "1",
            "--interval", "0.1",
            "--jsonl",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, check=True)
        event = json.loads(r.stdout.strip())
        self.assertEqual(event["event"], "coordination_state_changed")
        self.assertEqual(event["status"], "clear")

    def test_watch_accepts_codex_tool_and_surfaces_inbox_unread_count(self):
        slug = channel_paths.app_slug(self.workdir)
        channel = channel_paths.ensure_channel_dir(slug)
        inbox.write_message(
            channel,
            sender="claude_code",
            recipient="codex",
            payload={"summary": "codex should see this"},
        )
        cmd = [
            sys.executable, str(HERE / "coordination_watch.py"),
            "--workdir", str(self.workdir),
            "--session-id", "me",
            "--tool", "codex",
            "--iterations", "1",
            "--interval", "0.1",
            "--jsonl",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, check=True)
        event = json.loads(r.stdout.strip())
        self.assertEqual(event["inbox_unread_count"], 1)
        self.assertEqual(event["direct_inbox_unread_count"], 1)
        self.assertEqual(event["broadcast_inbox_unread_count"], 0)

    def test_watch_signature_changes_when_inbox_count_changes_without_revision(self):
        base = {"status": "clear", "required_action": "none", "revision": 1}
        self.assertNotEqual(cw._signature({**base, "inbox_unread_count": 0}),
                            cw._signature({**base, "inbox_unread_count": 1}))

    # ------------------------------------------------------------------
    # Ownership-aware warn tests (R1 C8)
    # ------------------------------------------------------------------

    def test_peers_with_empty_owns_and_our_files_in_flight_is_clear(self):
        """3 peers with owns: [] and our files_in_flight: ["a.py"] -> clear.

        This is the false-positive regression: the old code warned whenever
        peers existed; now warn fires only on actual ownership intersection.
        """
        slug = channel_paths.app_slug(self.workdir)
        channel = channel_paths.ensure_channel_dir(slug)
        for i in range(3):
            presence.write_presence(
                channel,
                session_id=f"peer{i}",
                tool="claude_code",
                model="m",
                run_id="r",
                app_slug=slug,
                phase="execute",
                files_in_flight=["a.py"],
                cwd=self.workdir,
            )
            # Patch presence file to add empty ``owns`` field.
            import json as _json
            from rally_point.presence import _presence_path
            p = _presence_path(channel, f"peer{i}")
            rec = _json.loads(p.read_text())
            rec["owns"] = []
            p.write_text(_json.dumps(rec))

        status = self._run("--files-in-flight", "a.py")
        self.assertEqual(status["status"], "clear",
                         f"Expected clear but got {status['status']}; "
                         f"peer_overlap_files={status.get('peer_overlap_files')}")
        self.assertEqual(status["peer_overlap_files"], [])

    def test_peer_owns_intersection_with_our_files_in_flight_warns(self):
        """Peer owns b.py and our files_in_flight includes b.py -> warn.

        peer_overlap_files must list b.py; status must be warn.
        """
        slug = channel_paths.app_slug(self.workdir)
        channel = channel_paths.ensure_channel_dir(slug)
        presence.write_presence(
            channel,
            session_id="peer0",
            tool="claude_code",
            model="m",
            run_id="r",
            app_slug=slug,
            phase="execute",
            files_in_flight=[],
            cwd=self.workdir,
        )
        # Patch peer presence to add owns: ["b.py"].
        import json as _json
        from rally_point.presence import _presence_path
        p = _presence_path(channel, "peer0")
        rec = _json.loads(p.read_text())
        rec["owns"] = ["b.py"]
        p.write_text(_json.dumps(rec))

        # Second peer with owns: [] — must not contribute to overlap.
        presence.write_presence(
            channel,
            session_id="peer1",
            tool="claude_code",
            model="m",
            run_id="r",
            app_slug=slug,
            phase="execute",
            files_in_flight=[],
            cwd=self.workdir,
        )
        p1 = _presence_path(channel, "peer1")
        rec1 = _json.loads(p1.read_text())
        rec1["owns"] = []
        p1.write_text(_json.dumps(rec1))

        status = self._run("--files-in-flight", "b.py")
        self.assertEqual(status["status"], "warn",
                         f"Expected warn but got {status['status']}")
        self.assertIn("b.py", status["peer_overlap_files"])

    def test_backward_compat_existing_fields_present(self):
        """Existing fields are still present in output JSON after refactor."""
        status = self._run()
        required_fields = [
            "schema_version", "status", "required_action", "workdir",
            "app_slug", "channel_dir", "session_id", "revision",
            "active_peers", "overlaps", "coordination_file",
            "latest_verdicts", "unresolved", "dirty_files",
            "dirty_outside_owned", "new_changes",
            # New fields
            "peer_overlap_files", "direct_inbox_unread_count",
            "broadcast_inbox_unread_count", "inbox_unread_count",
            "inbox_unread_counts",
        ]
        for field in required_fields:
            self.assertIn(field, status, f"Missing field: {field}")

    def test_inbox_unread_count_zero_when_no_inbox(self):
        """inbox_unread_count is 0 when inbox file doesn't exist."""
        status = self._run()
        self.assertEqual(status["inbox_unread_count"], 0)

    def test_inbox_unread_count_counts_nonempty_lines(self):
        """inbox_unread_count counts non-blank lines in the inbox jsonl."""
        slug = channel_paths.app_slug(self.workdir)
        channel = channel_paths.ensure_channel_dir(slug)
        inbox.write_message(channel, sender="codex", recipient="claude_code", payload={"msg": "a"})
        inbox.write_message(channel, sender="codex", recipient="claude_code", payload={"msg": "b"})

        status = self._run()
        self.assertEqual(status["inbox_unread_count"], 2)
        self.assertEqual(status["direct_inbox_unread_count"], 2)
        self.assertEqual(status["broadcast_inbox_unread_count"], 0)

    def test_inbox_unread_count_is_tool_scoped(self):
        slug = channel_paths.app_slug(self.workdir)
        channel = channel_paths.ensure_channel_dir(slug)
        inbox.write_message(channel, sender="claude_code", recipient="codex", payload={"msg": "a"})
        inbox.write_message(channel, sender="codex", recipient="claude_code", payload={"msg": "b"})

        codex_status = self._run("--tool", "codex")
        claude_status = self._run("--tool", "claude_code")

        self.assertEqual(codex_status["inbox_unread_count"], 1)
        self.assertEqual(claude_status["inbox_unread_count"], 1)

    def test_inbox_broadcast_all_is_visible_to_every_tool(self):
        slug = channel_paths.app_slug(self.workdir)
        channel = channel_paths.ensure_channel_dir(slug)
        inbox.write_message(
            channel,
            sender="claude_code",
            recipient="all",
            payload={"msg": "broadcast"},
        )
        inbox.write_message(
            channel,
            sender="claude_code",
            recipient="codex",
            payload={"msg": "direct"},
        )

        codex_status = self._run("--tool", "codex")
        claude_status = self._run("--tool", "claude_code")

        self.assertEqual(codex_status["direct_inbox_unread_count"], 1)
        self.assertEqual(codex_status["broadcast_inbox_unread_count"], 1)
        self.assertEqual(codex_status["inbox_unread_count"], 2)
        self.assertEqual(claude_status["direct_inbox_unread_count"], 0)
        self.assertEqual(claude_status["broadcast_inbox_unread_count"], 1)
        self.assertEqual(claude_status["inbox_unread_count"], 1)

        codex_messages = inbox.read_tool(channel, tool="codex")
        self.assertEqual([m["payload"]["msg"] for m in codex_messages],
                         ["direct", "broadcast"])

    def test_send_to_tool_dual_writes_inbox_and_channel(self):
        slug = channel_paths.app_slug(self.workdir)
        channel = channel_paths.ensure_channel_dir(slug)

        result = inbox.send_to_tool(
            channel,
            sender="claude_code",
            recipient="codex",
            payload={"summary": "please review"},
            model="test-model",
            run_id="run-1",
            app_slug=slug,
        )

        self.assertTrue(result["written"])
        self.assertEqual(result["channel_revision"], 1)
        self.assertEqual(inbox.unread_count(channel, "codex"), 1)
        records, _ = changes.read_changes_since(channel, 0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "message")
        self.assertEqual(records[0]["payload"]["to"], "codex")

    def test_rejection_count_zero_when_no_rejections_file(self):
        slug = channel_paths.app_slug(self.workdir)
        channel_paths.ensure_channel_dir(slug)
        out = self._run()
        self.assertEqual(out["rejection_count"], 0)

    def test_rejection_count_counts_nonempty_jsonl_lines(self):
        slug = channel_paths.app_slug(self.workdir)
        channel = channel_paths.ensure_channel_dir(slug)
        rej_file = channel / "rejections.jsonl"
        # Three rejection records + one blank line — blank should be ignored.
        rej_file.write_text(
            json.dumps({"reason": "missing_mece_fields", "tool": "codex"}) + "\n"
            + json.dumps({"reason": "missing_mece_fields", "tool": "codex"}) + "\n"
            + "\n"
            + json.dumps({"reason": "empty_required_string", "tool": "claude_code"}) + "\n",
            encoding="utf-8",
        )
        out = self._run()
        self.assertEqual(out["rejection_count"], 3)


if __name__ == "__main__":
    unittest.main()
