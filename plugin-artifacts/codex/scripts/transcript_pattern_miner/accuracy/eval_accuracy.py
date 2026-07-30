#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""eval_accuracy — precision/recall harness for the 5 transcript-pattern-miner detectors.

Run as a script to print a per-category table and exit non-zero if any category
drops below the documented accuracy bar (precision=1.0, recall=1.0 on boundary fixture).

Expose run_eval() -> dict for tests to assert on.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Support running as `python -m scripts.transcript_pattern_miner.accuracy.eval_accuracy`
# and also direct script execution.
_HERE = Path(__file__).parent
_SCRIPTS = _HERE.parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from transcript_pattern_miner.categories import (
    cluster_corrections,
    repeated_tool_sequences,
    cross_project_files,
    manual_command_rituals,
    test_pattern_outcomes,
)
from transcript_pattern_miner.accuracy.fixtures import (
    make_cluster_corrections_fixture,
    make_repeated_tool_sequences_fixture,
    make_cross_project_files_fixture,
    make_manual_command_rituals_fixture,
    make_test_pattern_outcomes_fixture,
)

# ---------------------------------------------------------------------------
# Accuracy bar: boundary fixture is deterministic, so we expect perfect scores.
# ---------------------------------------------------------------------------
REQUIRED_PRECISION: float = 1.0
REQUIRED_RECALL: float = 1.0


# ---------------------------------------------------------------------------
# Per-category evaluation helpers
# ---------------------------------------------------------------------------

def _score(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    return {"precision": precision, "recall": recall, "tp": tp, "fp": fp, "fn": fn}


def _eval_cluster_corrections() -> dict[str, Any]:
    """Evaluate cluster_corrections against labeled fixture.

    Negative groups are tested in isolation to avoid accidentally combining
    different negative text spines into a spurious cluster.
    """
    fixture = make_cluster_corrections_fixture()

    # True positive: the 5-member cluster from positive sessions fires
    pos_result = cluster_corrections(fixture["positives"])
    tp = 1 if len(pos_result) >= 1 else 0
    fn = 1 - tp

    # False positives: each negative group tested in isolation
    fp = 0
    for group_key in ("neg_two", "neg_oneoff", "neg_short", "neg_normal"):
        group = fixture[group_key]
        group_result = cluster_corrections(group)
        if len(group_result) >= 1:
            fp += 1

    # fp is count of groups that incorrectly fired; cap at 1 for binary precision calc
    fp = min(fp, 1)

    return _score(tp, fp, fn)


def _eval_repeated_tool_sequences() -> dict[str, Any]:
    """Evaluate repeated_tool_sequences against labeled fixture."""
    fixture = make_repeated_tool_sequences_fixture()

    # Positive case: the same 3-tool sequence across 3 sessions fires
    pos_result = repeated_tool_sequences(fixture["positives"])
    tp = 1 if len(pos_result) >= 1 else 0
    fn = 1 - tp

    # Negative case: only-2-session sessions must NOT fire
    neg_two_sessions = [s for s in fixture["negatives"] if s.session_id.startswith("seq-neg2-")]
    neg_two_result = repeated_tool_sequences(neg_two_sessions)
    fp_two = 1 if len(neg_two_result) >= 1 else 0

    # Negative case: uniform sequence must NOT fire
    neg_uniform = [s for s in fixture["negatives"] if "uniform" in s.session_id]
    # Repeat across 3 sessions to see if the all-identical guard holds
    neg_uniform_3 = neg_uniform * 3
    neg_uniform_result = repeated_tool_sequences(neg_uniform_3)
    fp_uniform = 1 if len(neg_uniform_result) >= 1 else 0

    fp = max(fp_two, fp_uniform)

    return _score(tp, fp, fn)


def _eval_cross_project_files() -> dict[str, Any]:
    """Evaluate cross_project_files against labeled fixture. Tests both cross and churn."""
    fixture = make_cross_project_files_fixture()

    # --- Cross sub-case ---
    all_cross_sessions = fixture["positives_cross"] + fixture["negatives_cross"]
    cross_result, _ = cross_project_files(all_cross_sessions)
    cross_files = {r["file"] for r in cross_result}

    tp_cross = 1 if fixture["cross_file"] in cross_files else 0
    fn_cross = 1 - tp_cross

    # False positive: the 2-project negative file must not appear in cross_result
    neg_cross_files = {"/some/unique/file.json"}
    fp_cross = len(neg_cross_files & cross_files)

    # --- Churn sub-case ---
    all_churn_sessions = fixture["positives_churn"] + fixture["negatives_churn"]
    _, churn_result = cross_project_files(all_churn_sessions)
    churn_entries = {(r["project"], r["file"]) for r in churn_result}

    pos_churn_key = (fixture["churn_proj"], fixture["churn_file"])
    tp_churn = 1 if pos_churn_key in churn_entries else 0
    fn_churn = 1 - tp_churn

    # False positive: 4-touch file must not appear
    neg_churn_key = ("proj-B", "/some/project/rarely-touched.ts")
    fp_churn = 1 if neg_churn_key in churn_entries else 0

    # Combine cross + churn into a single TP/FP/FN
    tp = tp_cross + tp_churn
    fp = fp_cross + fp_churn
    fn = fn_cross + fn_churn

    # Precision/recall over combined expected positives (2)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    return {"precision": precision, "recall": recall, "tp": tp, "fp": fp, "fn": fn}


def _eval_manual_command_rituals() -> dict[str, Any]:
    """Evaluate manual_command_rituals against labeled fixture."""
    fixture = make_manual_command_rituals_fixture()

    # Positive case: same shape 5 times fires
    pos_result = manual_command_rituals(fixture["positives"])
    shapes = {r["command_shape"] for r in pos_result}
    tp = 1 if any("git" in s and "status" in s for s in shapes) else 0
    fn = 1 - tp

    # Negative case A: only 4 occurrences must NOT fire
    neg_four = [s for s in fixture["negatives"] if s.session_id.startswith("ritual-neg4-")]
    neg_four_result = manual_command_rituals(neg_four)
    neg_four_shapes = {r["command_shape"] for r in neg_four_result}
    fp_four = 1 if any("git" in s and "status" in s for s in neg_four_shapes) else 0

    # Negative case B: varied shapes (none repeated ≥5) must NOT fire anything
    neg_varied = [s for s in fixture["negatives"] if s.session_id.startswith("ritual-neg-varied-")]
    # each shape appears once — check they don't appear in results if we run them
    neg_varied_result = manual_command_rituals(neg_varied)
    fp_varied = 1 if len(neg_varied_result) >= 1 else 0

    fp = max(fp_four, fp_varied)
    return _score(tp, fp, fn)


def _eval_test_pattern_outcomes() -> dict[str, Any]:
    """Evaluate test_pattern_outcomes against labeled fixture."""
    fixture = make_test_pattern_outcomes_fixture()

    # Positive: session with test_invocations produces non-empty per_invocation rows
    per_inv_pos, table_pos = test_pattern_outcomes(fixture["positives"])
    tp = 1 if len(per_inv_pos) >= 1 else 0
    fn = 1 - tp

    # Negative: empty test_invocations produces empty rows
    per_inv_neg, _ = test_pattern_outcomes(fixture["negatives"])
    fp = 1 if len(per_inv_neg) >= 1 else 0

    return _score(tp, fp, fn)


# ---------------------------------------------------------------------------
# Main eval orchestrator
# ---------------------------------------------------------------------------

def run_eval() -> dict[str, dict[str, Any]]:
    """Run all 5 detector evaluations. Returns per-category {precision, recall, tp, fp, fn}."""
    return {
        "cluster_corrections": _eval_cluster_corrections(),
        "repeated_tool_sequences": _eval_repeated_tool_sequences(),
        "cross_project_files": _eval_cross_project_files(),
        "manual_command_rituals": _eval_manual_command_rituals(),
        "test_pattern_outcomes": _eval_test_pattern_outcomes(),
    }


def _passes_bar(result: dict[str, Any]) -> bool:
    return (
        result["precision"] >= REQUIRED_PRECISION
        and result["recall"] >= REQUIRED_RECALL
    )


def main() -> int:
    results = run_eval()
    col_w = 28
    print(f"\n{'Category':<{col_w}}  {'Precision':>10}  {'Recall':>7}  {'TP':>4}  {'FP':>4}  {'FN':>4}  {'Status':>8}")
    print("-" * (col_w + 50))
    all_pass = True
    for cat, r in results.items():
        passes = _passes_bar(r)
        if not passes:
            all_pass = False
        status = "PASS" if passes else "FAIL"
        print(
            f"{cat:<{col_w}}  {r['precision']:>10.4f}  {r['recall']:>7.4f}"
            f"  {r['tp']:>4}  {r['fp']:>4}  {r['fn']:>4}  {status:>8}"
        )
    print()
    if all_pass:
        print(f"All categories meet bar (precision={REQUIRED_PRECISION}, recall={REQUIRED_RECALL}).")
    else:
        failing = [c for c, r in results.items() if not _passes_bar(r)]
        print(f"FAIL: categories below bar: {', '.join(failing)}")
    print()
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
