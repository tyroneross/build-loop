#!/usr/bin/env python3
"""Tests for scripts/app_pulse/lifecycle.py.

Stdlib only. Run from repo root:
    python3 scripts/app_pulse/test_lifecycle.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from rally_point import lifecycle  # noqa: E402


def _make_session(sd: Path, sid: str, age_seconds: float = 0) -> Path:
    """Create a presence file for ``sid`` with mtime = now - age_seconds."""
    sd.mkdir(parents=True, exist_ok=True)
    p = sd / f"{sid}.json"
    p.write_text(json.dumps({"session_id": sid, "heartbeat_ts": time.time()}))
    if age_seconds:
        target = time.time() - age_seconds
        os.utime(p, (target, target))
    return p


class ReapMySessionsTests(unittest.TestCase):
    def test_reaps_named_session(self):
        with tempfile.TemporaryDirectory() as d:
            ch = Path(d)
            sd = ch / "sessions"
            _make_session(sd, "alpha")
            _make_session(sd, "beta")
            n = lifecycle.reap_my_sessions(ch, "alpha")
            self.assertEqual(n, 1)
            self.assertFalse((sd / "alpha.json").exists())
            self.assertTrue((sd / "beta.json").exists())

    def test_idempotent_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            ch = Path(d)
            (ch / "sessions").mkdir()
            n = lifecycle.reap_my_sessions(ch, "never-existed")
            self.assertEqual(n, 0)

    def test_no_crash_on_missing_dir(self):
        with tempfile.TemporaryDirectory() as d:
            ch = Path(d) / "does-not-exist"
            n = lifecycle.reap_my_sessions(ch, "alpha")
            self.assertEqual(n, 0)


class ReapStaleSessionsTests(unittest.TestCase):
    def test_reaps_stale_keeps_active(self):
        with tempfile.TemporaryDirectory() as d:
            ch = Path(d)
            sd = ch / "sessions"
            _make_session(sd, "fresh", age_seconds=10)         # 10s old
            _make_session(sd, "stale-30m", age_seconds=1900)    # 31m
            _make_session(sd, "stale-2h", age_seconds=2 * 3600)  # 2h
            n = lifecycle.reap_stale_sessions(ch, stale_after_seconds=3600)
            self.assertEqual(n, 1, "only the 2h session should be reaped")
            self.assertTrue((sd / "fresh.json").exists())
            self.assertTrue((sd / "stale-30m.json").exists())
            self.assertFalse((sd / "stale-2h.json").exists())

    def test_custom_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            ch = Path(d)
            sd = ch / "sessions"
            _make_session(sd, "five-min", age_seconds=300)
            n = lifecycle.reap_stale_sessions(ch, stale_after_seconds=60)
            self.assertEqual(n, 1)

    def test_no_sessions_dir_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            n = lifecycle.reap_stale_sessions(Path(d))
            self.assertEqual(n, 0)


class RotateChangesLogTests(unittest.TestCase):
    def test_rotates_when_over_mb_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            ch = Path(d)
            log = ch / "changes.jsonl"
            log.write_text("x" * (2 * 1024 * 1024))  # 2 MB
            target = lifecycle.rotate_changes_log(ch, max_mb=1, max_entries=10**9)
            self.assertIsNotNone(target)
            self.assertFalse(log.exists())
            self.assertTrue(target.is_file())
            self.assertEqual(target.name.split(".")[1], "jsonl")

    def test_rotates_when_over_entry_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            ch = Path(d)
            log = ch / "changes.jsonl"
            log.write_text("\n".join(["{}"] * 600) + "\n")  # 600 entries, tiny size
            target = lifecycle.rotate_changes_log(ch, max_mb=999, max_entries=500)
            self.assertIsNotNone(target)
            self.assertFalse(log.exists())

    def test_no_op_when_under_thresholds(self):
        with tempfile.TemporaryDirectory() as d:
            ch = Path(d)
            log = ch / "changes.jsonl"
            log.write_text("\n".join(["{}"] * 10) + "\n")
            target = lifecycle.rotate_changes_log(ch, max_mb=10, max_entries=1000)
            self.assertIsNone(target)
            self.assertTrue(log.exists())

    def test_no_log_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            ch = Path(d)
            target = lifecycle.rotate_changes_log(ch)
            self.assertIsNone(target)

    def test_same_day_rotation_collision_handled(self):
        """If today's rotated file already exists, append a numeric suffix."""
        with tempfile.TemporaryDirectory() as d:
            ch = Path(d)
            log = ch / "changes.jsonl"
            log.write_text("x" * (2 * 1024 * 1024))
            # Pre-create today's rotated file to force suffix
            import datetime as _dt
            today = _dt.date.today().isoformat()
            (ch / f"changes.jsonl.{today}").write_text("previously rotated")
            target = lifecycle.rotate_changes_log(ch, max_mb=1)
            self.assertIsNotNone(target)
            self.assertTrue(target.is_file())
            # Suffix-2 should land
            self.assertEqual(target.name, f"changes.jsonl.{today}.2")


class FireAndForgetTests(unittest.TestCase):
    """All entry points swallow errors and return safe defaults."""

    def test_reap_my_with_permission_error_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            ch = Path(d)
            sd = ch / "sessions"
            _make_session(sd, "alpha")
            with mock.patch.object(Path, "unlink", side_effect=OSError("denied")):
                self.assertEqual(lifecycle.reap_my_sessions(ch, "alpha"), 0)

    def test_rotate_with_rename_error_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            ch = Path(d)
            (ch / "changes.jsonl").write_text("x" * (2 * 1024 * 1024))
            with mock.patch.object(lifecycle.os, "rename", side_effect=OSError("denied")):
                self.assertIsNone(lifecycle.rotate_changes_log(ch, max_mb=1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
