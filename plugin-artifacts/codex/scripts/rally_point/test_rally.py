#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for rally/current.json helpers."""
from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rally_point import changes, rally  # noqa: E402
from rally_point.post import post  # noqa: E402


class RallyPointerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rally-point-"))
        self.channel = self.tmp / "apps" / "demo"
        self.channel.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _record(self, revision: int, *, phase: str = "rally-start") -> dict:
        return changes.make_record(
            kind="phase",
            tool="codex",
            model="gpt-5",
            run_id="run-1",
            app_slug="demo",
            payload={
                "phase": phase,
                "session_id": f"s-{revision}",
                "coord_file": ".build-loop/coordination/run.md",
            },
            revision=revision,
        )

    def test_write_and_read_current_round_trip(self):
        written = rally.write_current(self.channel, self._record(3))
        read = rally.read_current(self.channel)

        self.assertEqual(read, written)
        self.assertEqual(read["schema_version"], "1.0")
        self.assertEqual(read["app_slug"], "demo")
        self.assertEqual(read["run_id"], "run-1")
        self.assertEqual(read["latest_session_id"], "s-3")
        self.assertEqual(read["latest_revision"], 3)
        self.assertEqual(read["latest_phase"], "rally-start")
        self.assertEqual(read["coord_file"], ".build-loop/coordination/run.md")
        self.assertEqual(read["status"], "active")

    def test_concurrent_writers_preserve_highest_revision(self):
        threads = [
            threading.Thread(target=rally.write_current, args=(self.channel, self._record(i)))
            for i in range(1, 25)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        read = rally.read_current(self.channel)
        self.assertEqual(read["latest_revision"], 24)
        self.assertEqual(read["latest_session_id"], "s-24")

    def test_post_rally_start_updates_current_pointer(self):
        rev = post(
            channel_dir=self.channel,
            kind="phase",
            tool="claude_code",
            model="claude-opus-4-7",
            run_id="run-2",
            app_slug="demo",
            payload={
                "phase": "rally-start",
                "session_id": "claude-session",
                "coord_file": ".build-loop/coordination/r2.md",
            },
        )
        read = rally.read_current(self.channel)

        self.assertEqual(rev, 1)
        self.assertEqual(read["run_id"], "run-2")
        self.assertEqual(read["tool"], "claude_code")
        self.assertEqual(read["latest_session_id"], "claude-session")
        self.assertEqual(read["latest_revision"], 1)

    def test_rebuild_current_uses_latest_rally_start(self):
        changes.append_change(self.channel, self._record(1))
        changes.append_change(self.channel, self._record(2, phase="review"))
        changes.append_change(self.channel, self._record(3))

        rebuilt = rally.rebuild_current(self.channel)

        self.assertEqual(rebuilt["latest_revision"], 3)
        self.assertEqual(rally.read_current(self.channel)["latest_revision"], 3)


if __name__ == "__main__":
    unittest.main()
