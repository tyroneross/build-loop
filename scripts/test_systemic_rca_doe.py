#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for systemic_rca_doe.py."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DESIGN = REPO / "docs/test-fixtures/systemic-rca/doe/systemic-rca-doe.json"
CORPUS = REPO / "docs/test-fixtures/systemic-rca/golden-corpus.json"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import systemic_rca_doe as doe  # noqa: E402


class SystemicRcaDoeTests(unittest.TestCase):
    def test_build_packets_uses_all_runs_and_cases(self) -> None:
        packets = doe.build_packets(DESIGN, CORPUS)
        self.assertEqual(len(packets), 8)
        self.assertEqual(len(packets[0]["cases"]), 10)
        self.assertEqual(packets[0]["run_id"], 0)
        self.assertIn("protocol", packets[0])

    def test_packets_do_not_leak_answers(self) -> None:
        packets = doe.build_packets(DESIGN, CORPUS)
        forbidden = {
            "system_control_failure",
            "root_cause",
            "failure_map",
            "prevention_control",
            "tradeoffs",
            "impact",
        }
        for packet in packets:
            for case in packet["cases"]:
                self.assertTrue(forbidden.isdisjoint(case.keys()))

    def test_write_packets_and_score_results(self) -> None:
        packets = doe.build_packets(DESIGN, CORPUS)
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp) / "packets"
            doe.write_packets(packets[:2], outdir)
            self.assertTrue((outdir / "run-00.json").exists())
            results_dir = Path(tmp) / "results"
            results_dir.mkdir()
            corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
            (results_dir / "run-00.json").write_text(
                json.dumps(corpus, indent=2) + "\n",
                encoding="utf-8",
            )
            rows = doe.score_results(results_dir)
            self.assertEqual(rows, [{"run_id": 0, "value": 100.0, "reports": 10, "passed": 10}])

    def test_score_results_rejects_incomplete_design_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results_dir = Path(tmp)
            corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
            (results_dir / "run-00.json").write_text(
                json.dumps(corpus, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "missing=\\[1\\]"):
                doe.score_results(results_dir, expected_ids={0, 1})

    def test_score_results_rejects_empty_results_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "no run-\\*.json"):
                doe.score_results(Path(tmp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
