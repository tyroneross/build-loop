#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for dedupe_run_ledger.py. Zero deps. Run: python3 test_dedupe_run_ledger.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "dedupe_run_ledger.py"


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)] + args, capture_output=True, text=True)


class DedupeRunLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.state = self.workdir / ".build-loop" / "state.json"
        self.state.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, runs: list) -> None:
        self.state.write_text(json.dumps({"preBuildSha": "abc123", "runs": runs}, indent=2))

    def _report(self, *extra: str) -> dict:
        result = run(["--workdir", str(self.workdir), "--json", *extra])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return json.loads(result.stdout)

    def test_reports_duplicates_without_writing_by_default(self) -> None:
        self._write([
            {"run_id": "r1", "outcome": "fail"},
            {"run_id": "r1", "outcome": "pass"},
        ])
        report = self._report()
        self.assertEqual(report["rows_before"], 2)
        self.assertEqual(report["rows_after"], 1)
        self.assertEqual(report["duplicate_run_ids"], ["r1"])
        self.assertFalse(report["applied"])
        self.assertEqual(len(json.loads(self.state.read_text())["runs"]), 2,
                         "report-only mode must not touch the ledger")

    def test_apply_collapses_to_one_row_keeping_later_values(self) -> None:
        self._write([
            {"run_id": "r1", "outcome": "fail", "goal": "old"},
            {"run_id": "r2", "outcome": "pass"},
            {"run_id": "r1", "outcome": "pass", "goal": "corrected"},
        ])
        report = self._report("--apply")
        self.assertTrue(report["applied"])
        self.assertEqual((report["rows_before"], report["rows_after"]), (3, 2))

        runs = json.loads(self.state.read_text())["runs"]
        self.assertEqual([r["run_id"] for r in runs], ["r1", "r2"],
                         "the surviving row keeps the first occurrence's position")
        self.assertEqual(runs[0]["goal"], "corrected")
        self.assertEqual(runs[0]["outcome"], "pass")

    def test_apply_carries_evidence_the_later_row_omits(self) -> None:
        self._write([
            {"run_id": "r1", "outcome": "pass",
             "judge_decisions": [{"judge_id": "independent-auditor", "verdict": "approve"}]},
            {"run_id": "r1", "outcome": "pass", "goal": "corrected"},
        ])
        self._report("--apply")
        runs = json.loads(self.state.read_text())["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["judge_decisions"][0]["judge_id"], "independent-auditor")

    def test_repair_does_not_apply_the_pre_fix_writers_own_wipe(self) -> None:
        """The exact legacy duplicate shape this tool exists to heal. The pre-fix
        writer always emitted `filesTouched: []` and `phases: {}` on a goal-only
        correction, so the later row's empty collections carry no information -
        and letting them win would delete the file set, the phase map, and the
        auditor verdict scoped to them while repairing the duplicate."""
        self._write([
            {
                "run_id": "r1", "goal": "original", "outcome": "pass",
                "filesTouched": ["a.py", "b.py"],
                "phases": {"assess": {"status": "pass"}},
                "diagnosticCommands": ["pytest -q"],
                "judge_decisions": [{"judge_id": "independent-auditor", "verdict": "nay"}],
            },
            {
                "run_id": "r1", "goal": "corrected", "outcome": "pass",
                "filesTouched": [], "phases": {}, "diagnosticCommands": [],
            },
        ])
        report = self._report("--apply")
        self.assertEqual((report["rows_before"], report["rows_after"]), (2, 1))

        row = json.loads(self.state.read_text())["runs"][0]
        self.assertEqual(row["goal"], "corrected", "the later row still wins on supplied fields")
        self.assertEqual(row["filesTouched"], ["a.py", "b.py"])
        self.assertEqual(row["phases"], {"assess": {"status": "pass"}})
        self.assertEqual(row["diagnosticCommands"], ["pytest -q"])
        self.assertEqual(row["judge_decisions"][0]["verdict"], "nay",
                         "a blocking verdict must survive the repair")

    def test_repair_still_takes_a_later_non_empty_collection(self) -> None:
        """Precision: the guard protects EMPTY collections, not every later value."""
        self._write([
            {"run_id": "r1", "filesTouched": ["a.py"]},
            {"run_id": "r1", "filesTouched": ["c.py", "d.py"]},
        ])
        self._report("--apply")
        row = json.loads(self.state.read_text())["runs"][0]
        self.assertEqual(row["filesTouched"], ["c.py", "d.py"])

    def test_clean_ledger_is_a_no_op(self) -> None:
        self._write([{"run_id": "r1"}, {"run_id": "r2"}])
        before = self.state.read_text()
        report = self._report("--apply")
        self.assertEqual(report["rows_removed"], 0)
        self.assertEqual(report["duplicate_run_ids"], [])
        self.assertFalse(report["applied"], "nothing to repair means nothing is rewritten")
        self.assertEqual(self.state.read_text(), before)

    def test_rows_without_run_id_are_preserved(self) -> None:
        self._write([{"note": "malformed"}, {"run_id": "r1"}, {"run_id": "r1"}])
        self._report("--apply")
        runs = json.loads(self.state.read_text())["runs"]
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0], {"note": "malformed"})

    def test_missing_state_errors(self) -> None:
        result = run(["--workdir", str(self.workdir), "--json"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", json.loads(result.stdout)["error"])

    def test_non_runs_keys_are_untouched(self) -> None:
        self._write([{"run_id": "r1"}, {"run_id": "r1"}])
        self._report("--apply")
        state = json.loads(self.state.read_text())
        self.assertEqual(state["preBuildSha"], "abc123")


class LockedReadFailureTests(unittest.TestCase):
    """The re-read under the lock owes the same error contract as the first
    read: a file that became unparseable in between exits with a message, not a
    traceback."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.state.write_text(json.dumps({"runs": [{"run_id": "r1"}, {"run_id": "r1"}]}))
        sys.path.insert(0, str(HERE))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_corruption_between_reads_returns_an_error(self) -> None:
        import dedupe_run_ledger

        state_path = self.state

        class _CorruptingLock:
            def __init__(self, path):
                self.path = path

            def __enter__(self):
                state_path.write_text("{not json")
                return self

            def __exit__(self, *exc):
                return False

        original = dedupe_run_ledger.LockedFile
        dedupe_run_ledger.LockedFile = _CorruptingLock
        try:
            result = dedupe_run_ledger.repair(state_path, apply=True)
        finally:
            dedupe_run_ledger.LockedFile = original

        self.assertIn("unreadable state.json under the lock", result["error"])
        self.assertFalse(result["applied"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
