#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for systemic_rca_eval.py."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SCRIPT = HERE / "systemic_rca_eval.py"
GOLDEN_CORPUS = REPO / "docs/test-fixtures/systemic-rca/golden-corpus.json"
NEGATIVE_CORPUS = REPO / "docs/test-fixtures/systemic-rca/negative/shallow-actor-blame.json"
DOE_MATRIX = REPO / "docs/test-fixtures/systemic-rca/doe/systemic-rca-doe.json"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import systemic_rca_eval as rca  # noqa: E402


GOOD_REPORT = {
    "plain_language_failure": (
        "The app looked like it saved the setting, but the next screen could not use it."
    ),
    "why_it_happened": (
        "The screen sent the value, the handler accepted it, but the update path did not "
        "include the downstream reader. The plan had no caller-scope check."
    ),
    "failure_map": [
        "User sees a saved setting that does not apply.",
        "The downstream reader keeps the old value.",
        "The update only changed the writer path.",
        "The build plan lacked a caller-scope verifier for the changed interface.",
    ],
    "system_control_failure": (
        "Missing caller-scope verifier in the planning protocol for interface changes."
    ),
    "failure_classification": "scope-audit-gap",
    "root_cause": {
        "description": (
            "The changed interface had no mandatory caller-scope check, so a downstream "
            "reader was outside the owned files."
        )
    },
    "technical_details": {
        "evidence": [
            {"type": "code", "detail": "file lib/settings.ts changed without reader update"},
            {"type": "test", "detail": "pytest assertion proves reader kept old value"},
        ]
    },
    "pruned_causes": [
        {"hypothesis": "Database write failed", "evidence": "state file shows new value"}
    ],
    "tradeoffs": "The verifier adds planning cost but reduces under-scoped patches.",
    "impact": "Users see inconsistent settings; engineers risk repeated caller misses.",
    "prevention_control": "Add a plan verifier gate for modifies_api caller scope.",
}


class SystemicRcaEvalTests(unittest.TestCase):
    def test_good_report_scores_full(self) -> None:
        result = rca.evaluate_report(GOOD_REPORT)
        self.assertEqual(result["score"], 100.0)

    def test_actor_blame_without_control_fails(self) -> None:
        report = dict(GOOD_REPORT)
        report["system_control_failure"] = "Agent forgot to update the API route."
        report["root_cause"] = "Agent forgot to update the API route."
        result = rca.evaluate_report(report)
        failed = {row["rule_id"] for row in result["findings"] if not row["passed"]}
        self.assertIn("actor_blame_guard", failed)
        self.assertIn("system_control_failure", failed)

    def test_missing_classification_fails(self) -> None:
        report = dict(GOOD_REPORT)
        report.pop("failure_classification")
        result = rca.evaluate_report(report)
        failed = {row["rule_id"] for row in result["findings"] if not row["passed"]}
        self.assertIn("failure_classification", failed)

    def test_actor_blame_with_control_passes_guard(self) -> None:
        report = dict(GOOD_REPORT)
        report["system_control_failure"] = (
            "The agent missed the route because the handoff lacked a caller-scope "
            "verifier and route ownership contract."
        )
        result = rca.evaluate_report(report)
        by_id = {row["rule_id"]: row for row in result["findings"]}
        self.assertTrue(by_id["actor_blame_guard"]["passed"])
        self.assertTrue(by_id["system_control_failure"]["passed"])

    def test_actor_blame_in_plain_language_without_control_fails(self) -> None:
        report = dict(GOOD_REPORT)
        report["plain_language_failure"] = "The agent forgot to check the route."
        result = rca.evaluate_report(report)
        by_id = {row["rule_id"]: row for row in result["findings"]}
        self.assertFalse(by_id["actor_blame_guard"]["passed"])

    def test_golden_corpus_scores_full(self) -> None:
        result = rca.evaluate_paths([GOLDEN_CORPUS])
        self.assertEqual(result["summary"]["reports"], 10)
        self.assertEqual(result["summary"]["mean_score"], 100.0)

    def test_negative_corpus_fails_threshold(self) -> None:
        result = rca.evaluate_paths([NEGATIVE_CORPUS])
        self.assertLess(result["summary"]["mean_score"], 80.0)

    def test_doe_matrix_shape_is_fixed(self) -> None:
        data = json.loads(DOE_MATRIX.read_text(encoding="utf-8"))
        self.assertEqual(data["design"]["type"], "fractional")
        self.assertEqual(data["design"]["n_runs"], 8)
        self.assertEqual(data["design"]["n_factors"], 6)
        self.assertEqual(data["run_order"], [2, 4, 0, 6, 3, 5, 7, 1])
        self.assertEqual(len(data["runs"]), 8)

    def test_cli_score_only_outputs_number(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
            json.dump(GOOD_REPORT, tmp)
            tmp_path = tmp.name
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), tmp_path, "--score-only"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        finally:
            Path(tmp_path).unlink()
        self.assertEqual(result.stdout.strip(), "100.00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
