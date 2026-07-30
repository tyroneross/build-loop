#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for rally_poll_gate.py — the poll-after-post enforcement gate."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "rally_poll_gate.py"
sys.path.insert(0, str(HERE))

from rally_poll_gate import mine_open  # type: ignore  # noqa: E402


def _room(handoffs: list[dict]) -> dict:
    return {"data": {"room": {"open_handoffs": handoffs}}}


def run_cli(room: dict, *args: str) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(room, f)
        path = f.name
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--room-json", path],
        check=False, capture_output=True, text=True,
    )


class MineOpenTests(unittest.TestCase):
    def test_filters_by_author_tool(self) -> None:
        hs = [
            {"event_id": "a", "tool": "claude_code:assist", "target": "codex"},
            {"event_id": "b", "tool": "codex", "target": "claude_code:assist"},
        ]
        mine = mine_open(hs, "claude_code:assist")
        self.assertEqual([h["event_id"] for h in mine], ["a"])

    def test_empty(self) -> None:
        self.assertEqual(mine_open([], "x"), [])
        self.assertEqual(mine_open(None, "x"), [])


class CheckGateTests(unittest.TestCase):
    def test_gate_fails_on_own_open_handoff(self) -> None:
        room = _room([{"event_id": "f9ce", "tool": "claude_code:assist",
                       "target": "codex", "subject": "ack needed"}])
        r = run_cli(room, "check", "--tool", "claude_code:assist")
        self.assertEqual(r.returncode, 3, r.stderr)  # fail-closed on finding
        env = json.loads(r.stdout)
        self.assertTrue(env["gated"])
        self.assertEqual(env["mine_open"][0]["event_id"], "f9ce")

    def test_gate_passes_when_only_others_handoffs_open(self) -> None:
        room = _room([{"event_id": "x", "tool": "codex", "target": "claude_code:assist"}])
        r = run_cli(room, "check", "--tool", "claude_code:assist")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(json.loads(r.stdout)["gated"])

    def test_gate_passes_when_none_open(self) -> None:
        r = run_cli(_room([]), "check", "--tool", "claude_code:assist")
        self.assertEqual(r.returncode, 0, r.stderr)


class WaitTests(unittest.TestCase):
    def test_wait_returns_resolved_when_none_mine(self) -> None:
        r = run_cli(_room([]), "wait", "--tool", "claude_code:assist", "--timeout", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(json.loads(r.stdout)["resolved"])

    def test_wait_times_out_on_static_fixture_with_open_handoff(self) -> None:
        room = _room([{"event_id": "f9ce", "tool": "claude_code:assist", "target": "codex"}])
        r = run_cli(room, "wait", "--tool", "claude_code:assist", "--timeout", "1", "--interval", "1")
        self.assertEqual(r.returncode, 4, r.stderr)  # timeout → caller falls to fallback_plan
        self.assertEqual(json.loads(r.stdout)["reason"], "timeout")


class DisposeTests(unittest.TestCase):
    """Poster-side fallback: rally won't let the poster resolve a handoff, so a
    disposed marker must let `check` pass without deadlocking closeout."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self.tmp.name)
        (self.wd / ".build-loop").mkdir()
        self.room = _room([{"event_id": "f9ce", "tool": "claude_code:assist", "target": "codex"}])
        self.room_file = self.wd / "room.json"
        self.room_file.write_text(json.dumps(self.room))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT), *args], check=False,
                              capture_output=True, text=True)

    def test_dispose_then_check_passes(self) -> None:
        d = self._cli("dispose", "--tool", "claude_code:assist", "--workdir", str(self.wd),
                      "--event-id", "f9ce")
        self.assertEqual(d.returncode, 0, d.stderr)
        c = self._cli("check", "--tool", "claude_code:assist", "--workdir", str(self.wd),
                      "--room-json", str(self.room_file))
        self.assertEqual(c.returncode, 0, c.stdout)  # no longer deadlocks
        self.assertFalse(json.loads(c.stdout)["gated"])

    def test_wait_timeout_auto_disposes(self) -> None:
        w = self._cli("wait", "--tool", "claude_code:assist", "--workdir", str(self.wd),
                      "--event-id", "f9ce", "--timeout", "1", "--interval", "1",
                      "--room-json", str(self.room_file))
        self.assertEqual(w.returncode, 4, w.stderr)
        # auto-recorded so a later check won't block
        c = self._cli("check", "--tool", "claude_code:assist", "--workdir", str(self.wd),
                      "--room-json", str(self.room_file))
        self.assertFalse(json.loads(c.stdout)["gated"])


if __name__ == "__main__":
    unittest.main()
