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


if __name__ == "__main__":
    unittest.main(verbosity=2)
