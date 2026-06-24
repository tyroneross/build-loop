#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the eval_accuracy harness.

Asserts:
1. run_eval() returns precision=1.0 and recall=1.0 per category on the boundary fixture.
2. Each boundary negative does NOT fire when run in isolation.
3. Each boundary positive DOES fire.
4. The eval exits non-zero if a label is perturbed (the bar actually gates).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Ensure the scripts package is on sys.path for direct invocation
_SCRIPTS = Path(__file__).parent.parent.parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from transcript_pattern_miner.accuracy.eval_accuracy import (
    run_eval,
    REQUIRED_PRECISION,
    REQUIRED_RECALL,
    _eval_cluster_corrections,
    _eval_repeated_tool_sequences,
    _eval_cross_project_files,
    _eval_manual_command_rituals,
    _eval_test_pattern_outcomes,
)
from transcript_pattern_miner.categories import (
    cluster_corrections,
    repeated_tool_sequences,
    cross_project_files,
    manual_command_rituals,
    # Aliased: the detector is named `test_pattern_outcomes` (it processes
    # test-command invocations). Importing it under its bare name binds a
    # `test_`-prefixed callable into this module's namespace, which pytest then
    # mis-collects as a test (no `aggs` fixture → collection ERROR). The alias
    # keeps it out of the collection glob.
    test_pattern_outcomes as detect_test_pattern_outcomes,
)
from transcript_pattern_miner.accuracy.fixtures import (
    make_cluster_corrections_fixture,
    make_repeated_tool_sequences_fixture,
    make_cross_project_files_fixture,
    make_manual_command_rituals_fixture,
    make_test_pattern_outcomes_fixture,
)


# ---------------------------------------------------------------------------
# 1. run_eval() returns precision/recall == 1.0 per category
# ---------------------------------------------------------------------------

class TestRunEvalReturnsFullAccuracy:
    def setup_method(self):
        self.results = run_eval()

    def test_all_categories_present(self):
        expected = {
            "cluster_corrections",
            "repeated_tool_sequences",
            "cross_project_files",
            "manual_command_rituals",
            "test_pattern_outcomes",
        }
        assert set(self.results.keys()) == expected

    def test_cluster_corrections_precision_recall(self):
        r = self.results["cluster_corrections"]
        assert r["precision"] == pytest.approx(REQUIRED_PRECISION), f"precision={r['precision']}"
        assert r["recall"] == pytest.approx(REQUIRED_RECALL), f"recall={r['recall']}"

    def test_repeated_tool_sequences_precision_recall(self):
        r = self.results["repeated_tool_sequences"]
        assert r["precision"] == pytest.approx(REQUIRED_PRECISION)
        assert r["recall"] == pytest.approx(REQUIRED_RECALL)

    def test_cross_project_files_precision_recall(self):
        r = self.results["cross_project_files"]
        assert r["precision"] == pytest.approx(REQUIRED_PRECISION)
        assert r["recall"] == pytest.approx(REQUIRED_RECALL)

    def test_manual_command_rituals_precision_recall(self):
        r = self.results["manual_command_rituals"]
        assert r["precision"] == pytest.approx(REQUIRED_PRECISION)
        assert r["recall"] == pytest.approx(REQUIRED_RECALL)

    def test_test_pattern_outcomes_precision_recall(self):
        r = self.results["test_pattern_outcomes"]
        assert r["precision"] == pytest.approx(REQUIRED_PRECISION)
        assert r["recall"] == pytest.approx(REQUIRED_RECALL)

    def test_all_results_have_required_fields(self):
        for cat, r in self.results.items():
            for field in ("precision", "recall", "tp", "fp", "fn"):
                assert field in r, f"{cat} missing field {field!r}"


# ---------------------------------------------------------------------------
# 2. Boundary negatives do NOT fire
# ---------------------------------------------------------------------------

class TestBoundaryNegativesDoNotFire:
    """Each boundary negative group, run in isolation, must produce no output."""

    def test_correction_cluster_2_members_does_not_fire(self):
        fixture = make_cluster_corrections_fixture()
        result = cluster_corrections(fixture["neg_two"])
        assert result == [], f"2-member cluster should not fire, got: {result}"

    def test_correction_oneoff_does_not_fire(self):
        fixture = make_cluster_corrections_fixture()
        result = cluster_corrections(fixture["neg_oneoff"])
        assert result == [], f"one-off correction should not form a cluster: {result}"

    def test_correction_short_text_does_not_fire(self):
        """Text with <3 tokens must be excluded by token gate."""
        fixture = make_cluster_corrections_fixture()
        result = cluster_corrections(fixture["neg_short"])
        assert result == [], f"<3 token correction must not fire: {result}"

    def test_correction_no_correction_re_does_not_fire(self):
        """Normal user message without CORRECTION_RE signal must not fire."""
        fixture = make_cluster_corrections_fixture()
        result = cluster_corrections(fixture["neg_normal"])
        assert result == [], f"non-correction message must not fire: {result}"

    def test_sequence_2_sessions_does_not_fire(self):
        """Same 3-tool sequence in only 2 sessions is below the ≥3 threshold."""
        fixture = make_repeated_tool_sequences_fixture()
        neg_two = [s for s in fixture["negatives"] if s.session_id.startswith("seq-neg2-")]
        result = repeated_tool_sequences(neg_two)
        assert result == [], f"2-session sequence should not fire: {result}"

    def test_uniform_sequence_does_not_fire(self):
        """Window where all tools are identical is skipped by the set(window)==1 guard."""
        fixture = make_repeated_tool_sequences_fixture()
        neg_uniform = [s for s in fixture["negatives"] if "uniform" in s.session_id]
        # Replicate across 3 sessions to verify the guard holds
        result = repeated_tool_sequences(neg_uniform * 3)
        assert result == [], f"uniform tool sequence should not fire: {result}"

    def test_cross_2_projects_does_not_fire(self):
        """File in only 2 projects is below the ≥3 project threshold."""
        fixture = make_cross_project_files_fixture()
        cross_result, _ = cross_project_files(fixture["negatives_cross"])
        cross_files = {r["file"] for r in cross_result}
        assert "/some/unique/file.json" not in cross_files, (
            f"2-project file must not appear in cross result: {cross_result}"
        )

    def test_churn_4_touches_does_not_fire(self):
        """File with 4 touches is just below the ≥5 churn threshold."""
        fixture = make_cross_project_files_fixture()
        _, churn_result = cross_project_files(fixture["negatives_churn"])
        churn_keys = {(r["project"], r["file"]) for r in churn_result}
        neg_key = ("proj-B", "/some/project/rarely-touched.ts")
        assert neg_key not in churn_keys, (
            f"4-touch file must not appear in churn result: {churn_result}"
        )

    def test_ritual_4_occurrences_does_not_fire(self):
        """Same command shape appearing only 4 times is below the ≥5 threshold."""
        fixture = make_manual_command_rituals_fixture()
        neg_four = [s for s in fixture["negatives"] if s.session_id.startswith("ritual-neg4-")]
        result = manual_command_rituals(neg_four)
        shapes = {r["command_shape"] for r in result}
        assert not any("git" in s and "status" in s for s in shapes), (
            f"4-occurrence ritual must not fire: {result}"
        )

    def test_outcomes_empty_invocations_produces_no_rows(self):
        """Session with empty test_invocations must produce no per-invocation rows."""
        fixture = make_test_pattern_outcomes_fixture()
        per_inv, _ = detect_test_pattern_outcomes(fixture["negatives"])
        assert per_inv == [], f"empty test_invocations must produce no rows: {per_inv}"


# ---------------------------------------------------------------------------
# 3. Boundary positives DO fire
# ---------------------------------------------------------------------------

class TestBoundaryPositivesFire:
    """Each boundary positive (at-threshold) must fire."""

    def test_correction_cluster_3_plus_members_fires(self):
        """5-message cluster with shared 3-grams must produce ≥1 result."""
        fixture = make_cluster_corrections_fixture()
        result = cluster_corrections(fixture["positives"])
        assert len(result) >= 1, "5-member correction cluster must fire"
        assert result[0]["count"] >= 3

    def test_sequence_3_sessions_fires(self):
        """Same 3-tool sequence in exactly 3 sessions must fire."""
        fixture = make_repeated_tool_sequences_fixture()
        result = repeated_tool_sequences(fixture["positives"])
        assert len(result) >= 1, "3-session tool sequence must fire"

    def test_cross_3_projects_fires(self):
        """File in exactly 3 projects must appear in cross result."""
        fixture = make_cross_project_files_fixture()
        cross_result, _ = cross_project_files(fixture["positives_cross"])
        cross_files = {r["file"] for r in cross_result}
        assert fixture["cross_file"] in cross_files, (
            f"file in 3 projects must fire; got: {cross_files}"
        )

    def test_churn_5_touches_fires(self):
        """File with exactly 5 touches in one project must appear in churn result."""
        fixture = make_cross_project_files_fixture()
        _, churn_result = cross_project_files(fixture["positives_churn"])
        churn_keys = {(r["project"], r["file"]) for r in churn_result}
        pos_key = (fixture["churn_proj"], fixture["churn_file"])
        assert pos_key in churn_keys, (
            f"5-touch file must fire in churn; got: {churn_keys}"
        )

    def test_ritual_5_occurrences_fires(self):
        """Same command shape appearing exactly 5 times must fire."""
        fixture = make_manual_command_rituals_fixture()
        result = manual_command_rituals(fixture["positives"])
        assert len(result) >= 1, "5-occurrence ritual must fire"

    def test_outcomes_with_invocations_produces_rows(self):
        """Session with test_invocations must produce ≥1 per-invocation row."""
        fixture = make_test_pattern_outcomes_fixture()
        per_inv, _ = detect_test_pattern_outcomes(fixture["positives"])
        assert len(per_inv) >= 1, "test invocations must produce output rows"


# ---------------------------------------------------------------------------
# 4. Bar actually gates: eval exits non-zero when a category would fail
# ---------------------------------------------------------------------------

class TestBarActuallyGates:
    """Verify that _passes_bar logic enforces the bar — directly test that
    a sub-1.0 result would fail the bar check."""

    def test_failing_precision_is_detected(self):
        from transcript_pattern_miner.accuracy.eval_accuracy import _passes_bar
        # Simulate a result where FP pushed precision below 1.0
        assert not _passes_bar({"precision": 0.5, "recall": 1.0})

    def test_failing_recall_is_detected(self):
        from transcript_pattern_miner.accuracy.eval_accuracy import _passes_bar
        assert not _passes_bar({"precision": 1.0, "recall": 0.5})

    def test_perfect_result_passes(self):
        from transcript_pattern_miner.accuracy.eval_accuracy import _passes_bar
        assert _passes_bar({"precision": 1.0, "recall": 1.0})

    def test_main_exits_nonzero_when_category_fails(self, tmp_path):
        """Run eval_accuracy.py as a script with a perturbed env to confirm exit-1.

        We test the exit-code contract by calling the module main() with mocked
        run_eval rather than subprocess (avoids path/env issues in the test runner).
        """
        from unittest.mock import patch
        from transcript_pattern_miner.accuracy.eval_accuracy import main

        # Pretend cluster_corrections has FP = 1 (precision 0.5)
        perturbed = {
            "cluster_corrections": {"precision": 0.5, "recall": 1.0, "tp": 1, "fp": 1, "fn": 0},
            "repeated_tool_sequences": {"precision": 1.0, "recall": 1.0, "tp": 1, "fp": 0, "fn": 0},
            "cross_project_files": {"precision": 1.0, "recall": 1.0, "tp": 2, "fp": 0, "fn": 0},
            "manual_command_rituals": {"precision": 1.0, "recall": 1.0, "tp": 1, "fp": 0, "fn": 0},
            "test_pattern_outcomes": {"precision": 1.0, "recall": 1.0, "tp": 1, "fp": 0, "fn": 0},
        }
        with patch(
            "transcript_pattern_miner.accuracy.eval_accuracy.run_eval",
            return_value=perturbed,
        ):
            rc = main()
        assert rc != 0, "main() must exit non-zero when a category fails the bar"
