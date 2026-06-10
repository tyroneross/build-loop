#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the agent-activity ledger (scripts/agent_ledger.py).

Covers the instrument's contract: canonical field shape, vocab validation,
append-only durability (incl. a torn final line), summarize aggregation, and
the CLI surface the orchestrator shells out to.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "agent_ledger.py"

sys.path.insert(0, str(HERE))
import agent_ledger  # noqa: E402


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LEDGER), *args],
        check=False,
        capture_output=True,
        text=True,
    )


class BuildRowTests(unittest.TestCase):
    def test_canonical_fields_present_and_ordered(self) -> None:
        row = agent_ledger.build_row(run_id="r1", agent="advisor", action="author")
        self.assertEqual(tuple(row.keys()), agent_ledger.LEDGER_FIELDS)

    def test_required_fields_enforced(self) -> None:
        with self.assertRaises(ValueError):
            agent_ledger.build_row(run_id="", agent="advisor", action="author")
        with self.assertRaises(ValueError):
            agent_ledger.build_row(run_id="r1", agent="", action="author")

    def test_unknown_action_rejected(self) -> None:
        with self.assertRaises(ValueError):
            agent_ledger.build_row(run_id="r1", agent="x", action="bogus")

    def test_unknown_status_rejected(self) -> None:
        with self.assertRaises(ValueError):
            agent_ledger.build_row(run_id="r1", agent="x", action="execute", status="great")

    def test_rung_bounds_enforced(self) -> None:
        with self.assertRaises(ValueError):
            agent_ledger.build_row(run_id="r1", agent="x", action="execute", rung=4)
        # valid rungs do not raise
        for r in (0, 1, 2, 3):
            agent_ledger.build_row(run_id="r1", agent="x", action="execute", rung=r)

    def test_ts_autostamped_when_absent(self) -> None:
        row = agent_ledger.build_row(run_id="r1", agent="x", action="author")
        self.assertTrue(row["ts"].endswith("Z"))


class AppendReadTests(unittest.TestCase):
    def test_append_then_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".build-loop" / "agent-ledger.jsonl"
            row = agent_ledger.build_row(
                run_id="r1", agent="advisor", action="author",
                phase="2", tier="frontier", model="fable", rung=1, status="pass",
                trigger="riskSurfaceChange", refs={"output": "docs/plans/x.md"},
            )
            env = agent_ledger.append(path, row)
            self.assertTrue(env["ok"], env)
            rows = agent_ledger.read(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["agent"], "advisor")
            self.assertEqual(rows[0]["model"], "fable")
            self.assertEqual(rows[0]["rung"], 1)

    def test_append_is_additive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.jsonl"
            for i in range(3):
                agent_ledger.append(
                    path,
                    agent_ledger.build_row(run_id="r1", agent="implementer", action="execute", chunk_id=f"c{i}"),
                )
            self.assertEqual(len(agent_ledger.read(path)), 3)

    def test_read_tolerates_torn_final_line(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.jsonl"
            agent_ledger.append(path, agent_ledger.build_row(run_id="r1", agent="x", action="author"))
            # Simulate a crash mid-append: a partial JSON line with no newline.
            with path.open("a", encoding="utf-8") as fh:
                fh.write('{"ts": "2026-06-10T00:00:00Z", "run_id": "r1"')  # truncated
            rows = agent_ledger.read(path)
            self.assertEqual(len(rows), 1, "torn final line must be skipped, valid row kept")

    def test_read_missing_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(agent_ledger.read(Path(td) / "nope.jsonl"), [])

    def test_append_fail_open_on_bad_path(self) -> None:
        # A path whose parent cannot be created (a file in the way) fails open,
        # returning ok=False rather than raising.
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            path = blocker / "sub" / "ledger.jsonl"  # parent is a file
            env = agent_ledger.append(path, agent_ledger.build_row(run_id="r1", agent="x", action="author"))
            self.assertFalse(env["ok"])
            self.assertIsNotNone(env["error"])


class SummarizeTests(unittest.TestCase):
    def test_summarize_aggregates_by_action_status_rung_and_advisor(self) -> None:
        rows = [
            agent_ledger.build_row(run_id="r1", agent="advisor", action="author", model="fable", rung=1, status="pass"),
            agent_ledger.build_row(run_id="r1", agent="implementer", action="execute", model="sonnet", rung=0, status="pass"),
            agent_ledger.build_row(run_id="r1", agent="implementer", action="execute", model="sonnet", rung=0, status="fail"),
            agent_ledger.build_row(run_id="r1", agent="advisor", action="re-plan", model="fable", rung=2, status="pass"),
        ]
        s = agent_ledger.summarize(rows)
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["by_action"]["execute"], 2)
        self.assertEqual(s["by_status"]["pass"], 3)
        self.assertEqual(s["by_status"]["fail"], 1)
        self.assertEqual(s["by_agent_model"]["advisor:fable"], 2)
        self.assertEqual(s["by_rung"]["0"], 2)
        self.assertEqual(s["advisor_invocations"], 2)


class CliTests(unittest.TestCase):
    def test_cli_append_read_summarize(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "ledger.jsonl")
            r = run_cli(
                "--path", path, "append",
                "--run-id", "r1", "--agent", "advisor", "--action", "author",
                "--tier", "frontier", "--model", "fable", "--rung", "1", "--status", "pass",
                "--refs", json.dumps({"output": "docs/plans/x.md"}),
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(json.loads(r.stdout)["ok"])

            r2 = run_cli("--path", path, "read")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            rows = json.loads(r2.stdout)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["refs"]["output"], "docs/plans/x.md")

            r3 = run_cli("--path", path, "summarize")
            self.assertEqual(r3.returncode, 0, r3.stderr)
            self.assertEqual(json.loads(r3.stdout)["advisor_invocations"], 1)

    def test_cli_rejects_bad_action(self) -> None:
        r = run_cli("--path", "/tmp/x.jsonl", "append", "--run-id", "r1", "--agent", "x", "--action", "bogus")
        self.assertNotEqual(r.returncode, 0)

    def test_cli_append_io_failure_is_fail_open(self) -> None:
        # An I/O write failure (a file where the parent dir should be) must exit 0
        # with ok:false — a telemetry outage never wedges the build. Input/caller
        # errors (above) still exit nonzero; only runtime write failures fail open.
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            bad_path = str(blocker / "sub" / "ledger.jsonl")  # parent is a file
            r = run_cli("--path", bad_path, "append", "--run-id", "r1", "--agent", "x", "--action", "author")
            self.assertEqual(r.returncode, 0, "I/O write failure must fail open (exit 0)")
            self.assertFalse(json.loads(r.stdout)["ok"])

    def test_cli_rejects_non_object_refs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(
                "--path", str(Path(td) / "l.jsonl"), "append",
                "--run-id", "r1", "--agent", "x", "--action", "author", "--refs", "[1,2,3]",
            )
            self.assertNotEqual(r.returncode, 0, "a non-object refs value must be rejected as a caller error")

    def test_cli_rejects_bad_refs_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(
                "--path", str(Path(td) / "l.jsonl"), "append",
                "--run-id", "r1", "--agent", "x", "--action", "author", "--refs", "{not json",
            )
            self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
