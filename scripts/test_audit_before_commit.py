# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/audit_before_commit — the temporal-membership guard on the hook-path
judge-decision write (RCA 2026-07-11 site 3).

_record_runs_judge_entry must NOT attach a commit's audit packet to a stale runs[-1] whose
window doesn't contain the trigger time — it opens a fresh hook-run entry instead — while
still appending to runs[-1] on the normal same-day path.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import audit_before_commit as abc  # noqa: E402


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class RecordRunsJudgeEntryMembershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".build-loop").mkdir(parents=True)

    def _write_state(self, runs: list) -> None:
        (self.root / ".build-loop" / "state.json").write_text(
            json.dumps({"runs": runs}), encoding="utf-8"
        )

    def _read_runs(self) -> list:
        return json.loads(
            (self.root / ".build-loop" / "state.json").read_text(encoding="utf-8")
        )["runs"]

    def test_stale_last_run_gets_fresh_hook_entry(self) -> None:
        """A month-stale runs[-1] must NOT absorb today's packet — a fresh hook_ run opens."""
        self._write_state([
            {"run_id": "old", "date": "2026-06-01T00:00:00Z", "judge_decisions": []},
        ])
        abc._record_runs_judge_entry(self.root, "abc1234", "packet_emitted", "2 files staged")
        runs = self._read_runs()
        self.assertEqual(len(runs), 2, runs)
        self.assertEqual(runs[0]["run_id"], "old")
        self.assertEqual(runs[0]["judge_decisions"], [], "stale run must stay untouched")
        self.assertTrue(runs[-1]["run_id"].startswith("hook_"), runs[-1])
        targets = [d.get("target") for d in runs[-1]["judge_decisions"]]
        self.assertIn("abc1234", targets)

    def test_in_window_last_run_receives_packet(self) -> None:
        """A same-day runs[-1] is the correct owner — no fresh run, packet appends there."""
        self._write_state([
            {"run_id": "current", "date": _now_iso(), "judge_decisions": []},
        ])
        abc._record_runs_judge_entry(self.root, "def5678", "packet_emitted", "1 file staged")
        runs = self._read_runs()
        self.assertEqual(len(runs), 1, "same-day run must not spawn a fresh hook run")
        self.assertEqual(runs[0]["run_id"], "current")
        targets = [d.get("target") for d in runs[0]["judge_decisions"]]
        self.assertIn("def5678", targets)

    def test_no_runs_creates_hook_entry(self) -> None:
        """Preserved behavior: empty runs[] → one fresh hook run with the packet."""
        self._write_state([])
        abc._record_runs_judge_entry(self.root, "aaa0000", "packet_emitted", "x")
        runs = self._read_runs()
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0]["run_id"].startswith("hook_"))

    def test_missing_state_is_noop(self) -> None:
        """Fail-soft: no state.json → returns without raising, writes nothing."""
        empty = Path(self._tmp.name) / "nostate"
        empty.mkdir()
        abc._record_runs_judge_entry(empty, "zzz", "packet_emitted", "x")  # must not raise
        self.assertFalse((empty / ".build-loop" / "state.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
