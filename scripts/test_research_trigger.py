#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for research_trigger.py."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "research_trigger.py"


def run_trigger(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


class ResearchTriggerTests(unittest.TestCase):
    def test_novel_integration_records_research_packet_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_trigger(
                "--workdir", td,
                "--task", "Add Stripe API checkout integration",
                "--effort", "M",
                "--json",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["research_required"])
        self.assertEqual(payload["depth"], "standard")
        self.assertIn("new_dependency", payload["triggers"])
        self.assertIn(".build-loop/research/", payload["packet_path"])
        self.assertTrue(payload["requires_citations_or_unavailable_note"])

    def test_trivial_local_edit_does_not_trigger_research(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_trigger(
                "--workdir", td,
                "--task", "Rename local helper variable in one test",
                "--effort", "XS",
                "--json",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["research_required"])
        self.assertEqual(payload["depth"], "none")
        self.assertIsNone(payload["packet_path"])
        self.assertFalse(payload["blocks_final_claims"])

    def test_current_external_api_blocks_uncited_final_claims(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".build-loop").mkdir()
            result = run_trigger(
                "--workdir", td,
                "--task", "Use the latest OpenAI Responses API behavior",
                "--effort", "S",
                "--cache-into-state",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["research_required"])
            self.assertEqual(payload["depth"], "standard")
            self.assertIn("current_external", payload["triggers"])
            self.assertTrue(payload["blocks_final_claims"])
            self.assertTrue(payload["requires_citations_or_unavailable_note"])

            state = json.loads((root / ".build-loop" / "state.json").read_text())
            self.assertEqual(state["researchGate"]["packet_path"], payload["packet_path"])

    def test_large_research_architecture_task_escalates_to_deep(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_trigger(
                "--workdir", td,
                "--task", "Evaluate memory architecture options for future use",
                "--effort", "L",
                "--json",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["depth"], "deep")
        self.assertEqual(payload["memory_recall_depth"], "deep")


if __name__ == "__main__":
    unittest.main(verbosity=2)
