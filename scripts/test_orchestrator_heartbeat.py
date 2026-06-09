# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for orchestrator_heartbeat.py — state liveness + rally presence beat.

Locks the bl-orchestrator-heartbeat-rally-presence contract:

- a phase-boundary beat refreshes state.execution.last_heartbeat_at (the
  user-flagged "last_heartbeat=None" defect);
- the beat writes a rally presence record any watcher can read;
- everything is fail-open — a beat never wedges the run, exit code always 0.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "orchestrator_heartbeat.py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "rally_point"))
sys.path.insert(0, str(HERE / "write_run_entry"))

import orchestrator_heartbeat as ohb  # noqa: E402
from write_run_entry import update_execution_state  # noqa: E402


def _project(tmp: str) -> Path:
    root = Path(tmp)
    (root / ".build-loop").mkdir(parents=True)
    return root


def _seed_execution(root: Path, run_id: str = "run_T") -> Path:
    """Create a started execution block, then back-date its heartbeat so a fresh
    beat is observably newer."""
    sp = root / ".build-loop" / "state.json"
    sp.write_text("{}", encoding="utf-8")
    update_execution_state(sp, "start", run_id=run_id, queued_chunks=["c1"], file_ownership={"c1": ["a.py"]})
    # Back-date last_heartbeat_at so the next beat is strictly newer.
    state = json.loads(sp.read_text())
    state["execution"]["last_heartbeat_at"] = "2000-01-01T00:00:00Z"
    state["execution"]["current_session_id"] = "sess-1"
    sp.write_text(json.dumps(state), encoding="utf-8")
    return sp


def _last_hb(sp: Path) -> str:
    return json.loads(sp.read_text())["execution"]["last_heartbeat_at"]


class TestStateHeartbeat(unittest.TestCase):
    def test_heartbeat_action_refreshes_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            sp = _seed_execution(root)
            before = _last_hb(sp)
            update_execution_state(sp, "heartbeat")
            after = _last_hb(sp)
            self.assertNotEqual(before, after)
            self.assertGreater(after, before)  # ISO-8601 sorts lexicographically

    def test_heartbeat_action_mutates_nothing_else(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            sp = _seed_execution(root)
            before = json.loads(sp.read_text())["execution"]
            update_execution_state(sp, "heartbeat")
            after = json.loads(sp.read_text())["execution"]
            # Only last_heartbeat_at changes.
            for key in ("phase", "queued_chunks", "in_flight_chunks", "iterate_attempt", "run_id"):
                self.assertEqual(before[key], after[key], f"{key} must not change on heartbeat")


class TestPhaseBoundaryBeat(unittest.TestCase):
    def test_phase_transition_then_beat_writes_fresh_heartbeat(self) -> None:
        """Spec-required: a phase transition writes a fresh heartbeat."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            sp = _seed_execution(root)
            # Simulate the orchestrator crossing a phase boundary then beating.
            update_execution_state(sp, "phase_transition", phase="review")
            self.assertEqual(json.loads(sp.read_text())["execution"]["phase"], "review")
            stale = "2000-01-01T00:00:00Z"
            # Force the heartbeat back to stale, then beat via the wrapper.
            state = json.loads(sp.read_text())
            state["execution"]["last_heartbeat_at"] = stale
            sp.write_text(json.dumps(state), encoding="utf-8")

            env = ohb.beat(root, phase="review", label="Review-A start")
            self.assertEqual(env["state_heartbeat"], "ok")
            self.assertNotEqual(_last_hb(sp), stale)
            # The fresh stamp is recent (within the last minute).
            fresh = datetime.fromisoformat(_last_hb(sp).replace("Z", "+00:00"))
            self.assertLess((datetime.now(timezone.utc) - fresh).total_seconds(), 60)

    def test_beat_writes_presence_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _seed_execution(root)
            env = ohb.beat(root, phase="execute", label="chunk c1", files=["a.py"])
            # Presence beat either lands (ok) or is cleanly skipped with a reason;
            # never an exception. When it lands, a session file exists.
            self.assertIn(env["presence_beat"], ("ok", "skipped"))
            if env["presence_beat"] == "ok":
                channel_dir = Path(env["channel_dir"])
                sessions = list((channel_dir / "sessions").glob("*.json")) if (channel_dir / "sessions").exists() else []
                # The session id we wrote should be present somewhere under the channel.
                self.assertTrue(
                    sessions or any(channel_dir.rglob("*sess-1*")),
                    "presence beat claimed ok but wrote no session record",
                )


class TestFailOpen(unittest.TestCase):
    def test_beat_without_execution_block_skips_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / ".build-loop" / "state.json").write_text("{}", encoding="utf-8")
            env = ohb.beat(root, phase="assess")
            # No execution block → state heartbeat skipped, presence skipped, no raise.
            self.assertEqual(env["presence_beat"], "skipped")
            self.assertEqual(env["state_heartbeat"], "skipped")

    def test_beat_on_missing_state_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)  # .build-loop exists, no state.json
            env = ohb.beat(root, phase="assess")
            self.assertEqual(env["state_heartbeat"], "skipped")

    def test_cli_always_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            _seed_execution(root)
            cp = subprocess.run(
                [sys.executable, str(SCRIPT), "--workdir", str(root), "--phase", "review", "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(cp.returncode, 0)
            data = json.loads(cp.stdout)
            self.assertEqual(data["phase"], "review")

    def test_cli_exit_zero_on_empty_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cp = subprocess.run(
                [sys.executable, str(SCRIPT), "--workdir", tmp, "--phase", "assess"],
                capture_output=True, text=True,
            )
            self.assertEqual(cp.returncode, 0)


if __name__ == "__main__":
    unittest.main()
