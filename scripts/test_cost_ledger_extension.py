# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Step 9 invocation-telemetry fields added to write_cost_ledger_row.py.

Covers the new additive, nullable fields:
    --called  (bool)
    --skipped-reason  (str)
    --failed  (bool)
    --issue-found  (bool)
    --elapsed-seconds  (float)
    --downstream-iterate-outcome  (enum)

Plus TASK_ID correlation between paired dispatch + return rows.

Run:
    uv run pytest scripts/test_cost_ledger_extension.py -v
    OR
    python3 -m unittest scripts/test_cost_ledger_extension.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT = Path(__file__).resolve().parent / "write_cost_ledger_row.py"


def _run(args: list[str]) -> tuple[int, str, str]:
    res = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True,
    )
    return res.returncode, res.stdout, res.stderr


def _base_args(ledger: Path, task_id: str = "t-deadbeef", status: str = "completed") -> list[str]:
    return [
        "--agent", "implementer",
        "--task-id", task_id,
        "--model", "sonnet",
        "--status", status,
        "--dispatch-mode", "fan-out",
        "--ledger-path", str(ledger),
    ]


def _read_rows(ledger: Path) -> list[dict]:
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]


class BackwardCompatTests(unittest.TestCase):
    def test_legacy_args_still_work(self):
        with TemporaryDirectory() as td:
            ledger = Path(td) / "cost-ledger.jsonl"
            rc, _, err = _run(_base_args(ledger))
            self.assertEqual(rc, 0, f"stderr: {err}")
            rows = _read_rows(ledger)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            # Existing keys preserved
            for key in ("ts", "source", "agent", "task_id", "model", "status",
                        "dispatch_mode", "tokens_estimate", "tokens_source"):
                self.assertIn(key, row)
            # New keys ABSENT when not supplied (additive contract)
            for key in ("called", "skipped_reason", "failed", "issue_found",
                        "elapsed_seconds", "downstream_iterate_outcome"):
                self.assertNotIn(key, row, f"new field {key!r} leaked into legacy row")


class CalledFieldTests(unittest.TestCase):
    def test_called_true(self):
        with TemporaryDirectory() as td:
            ledger = Path(td) / "cost-ledger.jsonl"
            rc, _, _ = _run(_base_args(ledger) + ["--called", "true"])
            self.assertEqual(rc, 0)
            self.assertEqual(_read_rows(ledger)[0]["called"], True)

    def test_called_false_with_skipped_reason(self):
        with TemporaryDirectory() as td:
            ledger = Path(td) / "cost-ledger.jsonl"
            rc, _, _ = _run(
                _base_args(ledger, status="dispatched")
                + ["--called", "false", "--skipped-reason", "trivial bypass"]
            )
            self.assertEqual(rc, 0)
            row = _read_rows(ledger)[0]
            self.assertEqual(row["called"], False)
            self.assertEqual(row["skipped_reason"], "trivial bypass")


class IssueFoundTests(unittest.TestCase):
    def test_issue_found_true(self):
        with TemporaryDirectory() as td:
            ledger = Path(td) / "cost-ledger.jsonl"
            rc, _, _ = _run(_base_args(ledger) + ["--issue-found", "true"])
            self.assertEqual(rc, 0)
            self.assertEqual(_read_rows(ledger)[0]["issue_found"], True)


class FailedFieldTests(unittest.TestCase):
    def test_failed_true(self):
        with TemporaryDirectory() as td:
            ledger = Path(td) / "cost-ledger.jsonl"
            rc, _, _ = _run(
                _base_args(ledger, status="failed") + ["--failed", "true"]
            )
            self.assertEqual(rc, 0)
            self.assertEqual(_read_rows(ledger)[0]["failed"], True)


class ElapsedSecondsTests(unittest.TestCase):
    def test_elapsed_seconds_float(self):
        with TemporaryDirectory() as td:
            ledger = Path(td) / "cost-ledger.jsonl"
            rc, _, _ = _run(_base_args(ledger) + ["--elapsed-seconds", "12.34"])
            self.assertEqual(rc, 0)
            self.assertEqual(_read_rows(ledger)[0]["elapsed_seconds"], 12.34)


class DownstreamIterateOutcomeTests(unittest.TestCase):
    def test_each_enum_value(self):
        valid = [
            "clean", "resolved-on-pass-1", "resolved-on-pass-2-or-later",
            "overflow-to-followup", "abandoned",
        ]
        for v in valid:
            with TemporaryDirectory() as td:
                ledger = Path(td) / "cost-ledger.jsonl"
                rc, _, err = _run(
                    _base_args(ledger) + ["--downstream-iterate-outcome", v]
                )
                self.assertEqual(rc, 0, f"value {v!r} rejected; stderr: {err}")
                self.assertEqual(
                    _read_rows(ledger)[0]["downstream_iterate_outcome"], v
                )

    def test_invalid_enum_rejected(self):
        with TemporaryDirectory() as td:
            ledger = Path(td) / "cost-ledger.jsonl"
            rc, _, _ = _run(
                _base_args(ledger) + ["--downstream-iterate-outcome", "bogus"]
            )
            # argparse choices enforcement: non-zero exit on invalid choice
            self.assertNotEqual(rc, 0)


class TaskIdCorrelationTests(unittest.TestCase):
    def test_dispatch_and_return_share_task_id(self):
        """Per Step 9 protocol: orchestrator emits a dispatched row and a return
        row sharing the same --task-id so consumers can join them."""
        with TemporaryDirectory() as td:
            ledger = Path(td) / "cost-ledger.jsonl"
            tid = "t-12345678"
            # Dispatch row
            rc, _, _ = _run(
                _base_args(ledger, task_id=tid, status="dispatched")
                + ["--called", "true", "--started-at", "2026-05-20T20:00:00Z"]
            )
            self.assertEqual(rc, 0)
            # Return row
            rc, _, _ = _run(
                _base_args(ledger, task_id=tid, status="completed")
                + ["--called", "true", "--failed", "false",
                   "--issue-found", "false", "--elapsed-seconds", "42.5",
                   "--completed-at", "2026-05-20T20:00:42Z"]
            )
            self.assertEqual(rc, 0)
            rows = _read_rows(ledger)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["task_id"], tid)
            self.assertEqual(rows[1]["task_id"], tid)
            self.assertEqual(rows[0]["status"], "dispatched")
            self.assertEqual(rows[1]["status"], "completed")
            self.assertEqual(rows[1]["elapsed_seconds"], 42.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
