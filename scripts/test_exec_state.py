#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for exec_state.py — the item_iteration telemetry producer CLI.

Focus: the row records BOTH tier and the resolved model, so a tiered surface is
auditable after the fact. The headline case is `--tier frontier` resolving to
`fable` with no config (the build-loop default) — i.e. the instrument actually
captures "Fable was the tier" rather than leaving it null capacity.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "exec_state.py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "rally_point"))

from write_run_entry import update_execution_state  # type: ignore  # noqa: E402


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


class ExecStateItemIterationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".build-loop").mkdir(parents=True)
        self.state_path = self.root / ".build-loop" / "state.json"
        # item_iteration requires an existing execution block (run start first).
        update_execution_state(
            self.state_path,
            "start",
            run_id="bl-test-run",
            queued_chunks=["c0"],
            file_ownership={"c0": ["a.py"]},
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _last_attempt(self, item_id: str) -> dict:
        state = json.loads(self.state_path.read_text())
        return state["execution"]["item_iterations"][item_id][-1]

    def test_tier_frontier_resolves_to_fable_default(self) -> None:
        """--tier frontier with no config records tier=frontier, model=fable."""
        r = run_cli(
            "item-iteration", "--workdir", str(self.root),
            "--item-id", "q-7", "--status", "passed",
            "--validator", "independent-auditor", "--tier", "frontier",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        row = self._last_attempt("q-7")
        self.assertEqual(row["tier"], "frontier")
        self.assertEqual(row["model"], "fable")
        self.assertEqual(row["validator"], "independent-auditor")
        self.assertEqual(row["status"], "passed")

    def test_explicit_model_skips_resolution(self) -> None:
        """--model is recorded verbatim; tier omitted when not passed."""
        r = run_cli(
            "item-iteration", "--workdir", str(self.root),
            "--item-id", "q-8", "--model", "claude-opus-4-7",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        row = self._last_attempt("q-8")
        self.assertEqual(row["model"], "claude-opus-4-7")
        self.assertNotIn("tier", row)

    def test_config_override_wins_over_tier_default(self) -> None:
        """A repo config.json modelOverride for the tier is what gets recorded."""
        cfg = self.root / ".build-loop" / "config.json"
        cfg.write_text(json.dumps({"modelOverrides": {"code": "gpt-5-codex"}}))
        r = run_cli(
            "item-iteration", "--workdir", str(self.root),
            "--item-id", "q-9", "--tier", "code",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        row = self._last_attempt("q-9")
        self.assertEqual(row["tier"], "code")
        self.assertEqual(row["model"], "gpt-5-codex")

    def test_attempts_increment_per_item(self) -> None:
        for _ in range(2):
            run_cli("item-iteration", "--workdir", str(self.root), "--item-id", "q-10", "--tier", "code")
        state = json.loads(self.state_path.read_text())
        attempts = state["execution"]["item_iterations"]["q-10"]
        self.assertEqual([a["attempt"] for a in attempts], [1, 2])


if __name__ == "__main__":
    unittest.main()
