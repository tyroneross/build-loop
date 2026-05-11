#!/usr/bin/env python3
"""Tests for session_registry.py. Zero deps. Run: python3 test_session_registry.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import session_registry as sr  # noqa: E402


def _make_session(
    sessions_dir: Path,
    run_id: str,
    workdir: str,
    phase: str = "assess",
    files_owned: list[str] | None = None,
    host: str = "claude_code",
    heartbeat_age_minutes: float = 0.0,
) -> Path:
    """Create a presence file directly with a possibly-stale heartbeat."""
    sr.register(
        sessions_dir, run_id=run_id, host=host, workdir=workdir,
        pid=os.getpid(), phase=phase, files_owned=files_owned,
    )
    if heartbeat_age_minutes > 0:
        path = sr._presence_path(sessions_dir, run_id)
        payload = json.loads(path.read_text())
        old = datetime.now(timezone.utc) - timedelta(minutes=heartbeat_age_minutes)
        payload["last_heartbeat_at"] = old.strftime("%Y-%m-%dT%H:%M:%SZ")
        path.write_text(json.dumps(payload))
    return sr._presence_path(sessions_dir, run_id)


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sessions = self.tmp / "sessions"

    def test_register_writes_presence_file(self):
        path = sr.register(
            self.sessions, run_id="run_test_1", host="claude_code",
            workdir=str(self.tmp), pid=12345, phase="execute",
        )
        self.assertTrue(path.exists())
        payload = json.loads(path.read_text())
        self.assertEqual(payload["run_id"], "run_test_1")
        self.assertEqual(payload["host"], "claude_code")
        self.assertEqual(payload["phase"], "execute")
        self.assertEqual(payload["pid"], 12345)
        self.assertIn("started_at", payload)
        self.assertIn("last_heartbeat_at", payload)

    def test_register_rejects_invalid_host(self):
        with self.assertRaises(ValueError):
            sr.register(
                self.sessions, run_id="r", host="my_custom_thing",
                workdir=str(self.tmp), pid=1, phase="assess",
            )

    def test_register_rejects_invalid_phase(self):
        with self.assertRaises(ValueError):
            sr.register(
                self.sessions, run_id="r", host="codex",
                workdir=str(self.tmp), pid=1, phase="warmup",
            )

    def test_register_safe_id_strips_path_separators(self):
        path = sr.register(
            self.sessions, run_id="evil/../../escape", host="other",
            workdir=str(self.tmp), pid=1, phase="assess",
        )
        # File must stay inside sessions_dir.
        self.assertEqual(path.parent.resolve(), self.sessions.resolve())


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sessions = self.tmp / "sessions"

    def test_heartbeat_updates_timestamp(self):
        _make_session(self.sessions, "r1", str(self.tmp))
        path = sr._presence_path(self.sessions, "r1")
        original = json.loads(path.read_text())["last_heartbeat_at"]
        time.sleep(1.1)
        ok = sr.heartbeat(self.sessions, "r1")
        self.assertTrue(ok)
        updated = json.loads(path.read_text())["last_heartbeat_at"]
        self.assertNotEqual(original, updated)

    def test_heartbeat_optionally_updates_phase(self):
        _make_session(self.sessions, "r1", str(self.tmp), phase="assess")
        sr.heartbeat(self.sessions, "r1", phase="execute")
        payload = json.loads(sr._presence_path(self.sessions, "r1").read_text())
        self.assertEqual(payload["phase"], "execute")

    def test_heartbeat_missing_returns_false(self):
        ok = sr.heartbeat(self.sessions, "nonexistent")
        self.assertFalse(ok)

    def test_heartbeat_updates_files_owned(self):
        _make_session(self.sessions, "r1", str(self.tmp))
        sr.heartbeat(self.sessions, "r1", files_owned=["a.py", "b.py"])
        payload = json.loads(sr._presence_path(self.sessions, "r1").read_text())
        self.assertEqual(payload["files_owned"], ["a.py", "b.py"])


class StaleSweepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sessions = self.tmp / "sessions"

    def test_fresh_session_not_swept(self):
        _make_session(self.sessions, "fresh", str(self.tmp), heartbeat_age_minutes=1)
        sr.sweep_stale(self.sessions, staleness_minutes=5)
        self.assertTrue(sr._presence_path(self.sessions, "fresh").exists())
        self.assertFalse((self.sessions / "dead" / "fresh.json").exists())

    def test_stale_session_swept_to_dead(self):
        _make_session(self.sessions, "old", str(self.tmp), heartbeat_age_minutes=10)
        moved = sr.sweep_stale(self.sessions, staleness_minutes=5)
        self.assertIn("old", moved)
        self.assertFalse(sr._presence_path(self.sessions, "old").exists())
        self.assertTrue((self.sessions / "dead" / "old.json").exists())

    def test_scan_excludes_stale(self):
        _make_session(self.sessions, "fresh", str(self.tmp), heartbeat_age_minutes=1)
        _make_session(self.sessions, "stale", str(self.tmp), heartbeat_age_minutes=10)
        peers = sr.scan_active(self.sessions, staleness_minutes=5)
        run_ids = {p["run_id"] for p in peers}
        self.assertIn("fresh", run_ids)
        self.assertNotIn("stale", run_ids)


class CollisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sessions = self.tmp / "sessions"
        self.workdir_a = str(self.tmp / "project_a")
        self.workdir_b = str(self.tmp / "project_b")
        Path(self.workdir_a).mkdir(parents=True)
        Path(self.workdir_b).mkdir(parents=True)

    def test_collision_LOW_different_workdir(self):
        _make_session(self.sessions, "peer", self.workdir_b, phase="execute")
        result = sr.check_collision(
            self.sessions, workdir=self.workdir_a, run_id="me",
            phase="execute", files_owned=["a.py"],
        )
        self.assertEqual(result["tier"], "LOW")
        self.assertEqual(len(result["same_workdir"]), 0)

    def test_collision_MEDIUM_same_workdir_different_phases(self):
        _make_session(self.sessions, "peer", self.workdir_a, phase="assess")
        result = sr.check_collision(
            self.sessions, workdir=self.workdir_a, run_id="me",
            phase="execute", files_owned=["a.py"],
        )
        self.assertEqual(result["tier"], "MEDIUM")
        self.assertEqual(len(result["same_workdir"]), 1)
        self.assertEqual(len(result["execute_collisions"]), 0)

    def test_collision_HIGH_same_workdir_both_execute(self):
        _make_session(
            self.sessions, "peer", self.workdir_a,
            phase="execute", files_owned=["other.py"],
        )
        result = sr.check_collision(
            self.sessions, workdir=self.workdir_a, run_id="me",
            phase="execute", files_owned=["mine.py"],
        )
        self.assertEqual(result["tier"], "HIGH")
        self.assertEqual(len(result["execute_collisions"]), 1)
        self.assertEqual(result["files_overlap"], {})

    def test_collision_CRITICAL_files_overlap(self):
        _make_session(
            self.sessions, "peer", self.workdir_a,
            phase="execute", files_owned=["shared.py", "their_only.py"],
        )
        result = sr.check_collision(
            self.sessions, workdir=self.workdir_a, run_id="me",
            phase="execute", files_owned=["shared.py", "my_only.py"],
        )
        self.assertEqual(result["tier"], "CRITICAL")
        self.assertEqual(result["files_overlap"], {"peer": ["shared.py"]})

    def test_collision_iterate_counts_as_execute(self):
        _make_session(
            self.sessions, "peer", self.workdir_a,
            phase="iterate", files_owned=["x.py"],
        )
        result = sr.check_collision(
            self.sessions, workdir=self.workdir_a, run_id="me",
            phase="execute", files_owned=["x.py"],
        )
        self.assertEqual(result["tier"], "CRITICAL")

    def test_collision_excludes_self(self):
        _make_session(
            self.sessions, "me", self.workdir_a,
            phase="execute", files_owned=["a.py"],
        )
        result = sr.check_collision(
            self.sessions, workdir=self.workdir_a, run_id="me",
            phase="execute", files_owned=["a.py"],
        )
        self.assertEqual(result["tier"], "LOW")
        self.assertEqual(result["peers"], [])


class UnregisterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sessions = self.tmp / "sessions"

    def test_unregister_moves_to_dead(self):
        _make_session(self.sessions, "r1", str(self.tmp))
        ok = sr.unregister(self.sessions, "r1")
        self.assertTrue(ok)
        self.assertFalse(sr._presence_path(self.sessions, "r1").exists())
        self.assertTrue((self.sessions / "dead" / "r1.json").exists())

    def test_unregister_nonexistent_returns_false(self):
        ok = sr.unregister(self.sessions, "ghost")
        self.assertFalse(ok)


class SafeStopSentinelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_writes_sentinel_in_build_loop_dir(self):
        workdir = self.tmp / "proj"
        workdir.mkdir()
        path = sr.write_safe_stop_sentinel(
            workdir, "peer_run_abc", "files_owned overlap on a.py",
        )
        self.assertTrue(path.exists())
        self.assertIn("SAFE-STOP-collision-peer_run_abc.md", path.name)
        self.assertEqual(path.parent.name, ".build-loop")
        body = path.read_text()
        self.assertIn("CRITICAL collision", body)
        self.assertIn("a.py", body)


class CLITests(unittest.TestCase):
    """End-to-end CLI tests — exercises argparse + dispatch."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sessions = str(self.tmp / "sessions")
        self.workdir = str(self.tmp / "proj")
        Path(self.workdir).mkdir(parents=True)

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(HERE / "session_registry.py"),
             "--sessions-dir", self.sessions, *args],
            capture_output=True, text=True,
        )

    def test_register_and_scan_roundtrip(self):
        r = self._cli(
            "register", "--run-id", "r1", "--host", "claude_code",
            "--workdir", self.workdir, "--pid", "9999", "--phase", "execute",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self._cli("scan", "--json")
        self.assertEqual(s.returncode, 0)
        peers = json.loads(s.stdout)
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]["run_id"], "r1")

    def test_check_returns_tier_exit_code(self):
        # No peers — LOW = exit 0
        r = self._cli(
            "check", "--run-id", "me", "--workdir", self.workdir,
            "--phase", "execute", "--json",
        )
        self.assertEqual(r.returncode, 0)
        result = json.loads(r.stdout)
        self.assertEqual(result["tier"], "LOW")

    def test_check_CRITICAL_returns_exit_3(self):
        self._cli(
            "register", "--run-id", "peer", "--host", "codex",
            "--workdir", self.workdir, "--pid", "1234", "--phase", "execute",
            "--files-owned", "shared.py",
        )
        r = self._cli(
            "check", "--run-id", "me", "--workdir", self.workdir,
            "--phase", "execute", "--files-owned", "shared.py", "--json",
        )
        self.assertEqual(r.returncode, 3)
        result = json.loads(r.stdout)
        self.assertEqual(result["tier"], "CRITICAL")
        self.assertIn("peer", result["files_overlap"])


if __name__ == "__main__":
    unittest.main()
