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
from app_pulse import channel_paths, presence  # noqa: E402


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

    def test_peer_overlap_warns(self):
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
        status = self._run("--owned-file", "src/app.py")
        self.assertEqual(status["status"], "warn")
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


if __name__ == "__main__":
    unittest.main()
