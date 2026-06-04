#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_update_ledger.py. Zero deps."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import memory_update_ledger as mul  # noqa: E402


class MemoryUpdateLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Path(self.tmp.name) / "memory"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_append_update_creates_global_ledger_row(self) -> None:
        decision = self.mem / "projects" / "build-loop" / "decisions" / "0001-test.md"
        decision.parent.mkdir(parents=True)
        decision.write_text("body\n")

        row = mul.append_update(
            memory_root=self.mem,
            action="write",
            path=decision,
            writer="test",
            run_id="run_1",
            source_commit="abc123",
            summary="test decision",
        )

        ledger = self.mem / "indexes" / "updates.jsonl"
        self.assertTrue(ledger.exists())
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], row["event_id"])
        self.assertEqual(rows[0]["project"], "build-loop")
        self.assertEqual(rows[0]["lane"], "decisions")
        self.assertEqual(rows[0]["path"], "projects/build-loop/decisions/0001-test.md")
        self.assertEqual(rows[0]["source_commit"], "abc123")
        self.assertIn("sha256", rows[0])

    def test_tail_filters_and_latest_project_update(self) -> None:
        mul.append_update(
            memory_root=self.mem,
            project="build-loop",
            lane="decisions",
            action="write",
            path="projects/build-loop/decisions/0001.md",
            writer="test",
            source_commit="old",
        )
        mul.append_update(
            memory_root=self.mem,
            project="build-loop",
            lane="milestones",
            action="append",
            path="projects/build-loop/milestones.jsonl",
            writer="test",
            source_commit="new",
        )
        mul.append_update(
            memory_root=self.mem,
            project="other",
            lane="decisions",
            action="write",
            path="projects/other/decisions/0001.md",
            writer="test",
            source_commit="other",
        )

        rows = mul.tail_updates(self.mem, project="build-loop", limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_commit"], "new")

        latest = mul.latest_project_update(self.mem, "build-loop")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["source_commit"], "new")

    def test_cli_append_json(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(HERE / "memory_update_ledger.py"),
                "--memory-root", str(self.mem),
                "append",
                "--project", "build-loop",
                "--lane", "decisions",
                "--action", "write",
                "--path", "projects/build-loop/decisions/0001.md",
                "--writer", "test",
                "--source-commit", "abc123",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row["project"], "build-loop")
        self.assertEqual(row["lane"], "decisions")

    def test_cli_relative_path_uses_configured_memory_root(self) -> None:
        env = dict(os.environ)
        env["BUILD_LOOP_MEMORY_STORE_ROOT"] = str(self.mem)
        result = subprocess.run(
            [
                sys.executable,
                str(HERE / "memory_update_ledger.py"),
                "append",
                "--action", "write",
                "--path", "projects/build-loop/decisions/0001.md",
                "--writer", "test",
                "--source-commit", "abc123",
                "--json",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue((self.mem / "indexes" / "updates.jsonl").exists())
        self.assertFalse((Path.cwd() / "indexes" / "updates.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
