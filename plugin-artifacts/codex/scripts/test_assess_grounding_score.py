#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for assess_grounding_score — the deterministic Assess-grounding scorer."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import assess_grounding_score as s

HERE = Path(__file__).resolve().parent


def test_trigger_recall_precision_perfect():
    gt = {"riskSurfaceChange": True}
    pred = {"riskSurfaceChange": True}
    assert s.score_triggers(pred, gt) == (1.0, 1.0)


def test_trigger_recall_miss():
    # ground truth had the risk; candidate missed it -> recall 0, precision 1 (nothing over-flagged)
    assert s.score_triggers({}, {"riskSurfaceChange": True}) == (0.0, 1.0)


def test_trigger_precision_overflag():
    # candidate flagged two; only one real -> recall 1.0, precision 0.5
    pred = {"riskSurfaceChange": True, "runtimeServer": True}
    gt = {"riskSurfaceChange": True}
    recall, precision = s.score_triggers(pred, gt)
    assert recall == 1.0 and precision == 0.5


def test_trigger_empty_both():
    # nothing mattered, nothing flagged -> both perfect
    assert s.score_triggers({"riskSurfaceChange": False}, {"riskSurfaceChange": False}) == (1.0, 1.0)


def test_trigger_missing_key_is_false():
    # missing key treated as False; sparse dicts are normal in state.json
    assert s.score_triggers({}, {}) == (1.0, 1.0)


def test_synthesis_calibration_exact():
    assert s.score_synthesis(6, True, 6, True) == 1.0


def test_synthesis_calibration_count_off():
    # off by 3 on a scale of 6 -> count_cal 0.5; escalation matches -> 1.0; avg 0.75
    assert s.score_synthesis(3, True, 6, True) == 0.75


def test_synthesis_escalation_mismatch():
    # exact count but wrong escalation -> avg(1.0, 0.0) = 0.5
    assert s.score_synthesis(6, False, 6, True) == 0.5


def test_synthesis_not_gradable_returns_none():
    assert s.score_synthesis(None, None, None, None) is None


def test_synthesis_none_pred_counts_as_zero():
    # candidate didn't predict a count; gt=6 -> off by 6 -> count_cal 0
    assert s.score_synthesis(None, None, 6, None) == 0.0


def test_files_recall_precision():
    recall, precision = s.score_files(["a.py", "b.py", "x.py"], ["a.py", "b.py"])
    assert recall == 1.0  # both real files predicted
    assert precision == round(2 / 3, 4)  # one of three predictions was spurious


def test_files_optional_none():
    assert s.score_files(None, ["a.py"]) == (None, None)
    assert s.score_files(["a.py"], None) == (None, None)


def test_score_candidate_shape_and_passthrough():
    cand = {
        "challenge_id": "c1", "variant": "G1", "rep": 0,
        "assessment": {"triggers": {"riskSurfaceChange": True}, "synthesis_count": 6, "synthesis_escalated": True},
        "cost": {"tokens": 12000, "latency_ms": 45000},
        "groundedness": 0.83,
    }
    gt = {"triggers": {"riskSurfaceChange": True}, "synthesis_count": 6, "synthesis_escalated": True, "goal_type": "feature"}
    row = s.score_candidate(cand, gt)
    assert row["trigger_recall"] == 1.0
    assert row["synthesis_calibration"] == 1.0
    assert row["groundedness"] == 0.83  # judge value passes through untouched
    assert row["cost_tokens"] == 12000
    assert row["goal_type"] == "feature"


def test_aggregate_cell_stability():
    rows = [
        {"challenge_id": "c1", "variant": "G1", "goal_type": "feature", "trigger_recall": 1.0,
         "trigger_precision": 1.0, "synthesis_calibration": 1.0, "file_recall": None,
         "file_precision": None, "groundedness": 0.8, "cost_tokens": 100, "latency_ms": 10},
        {"challenge_id": "c1", "variant": "G1", "goal_type": "feature", "trigger_recall": 0.0,
         "trigger_precision": 1.0, "synthesis_calibration": 1.0, "file_recall": None,
         "file_precision": None, "groundedness": 0.6, "cost_tokens": 200, "latency_ms": 20},
    ]
    agg = s.aggregate_cell(rows)
    assert agg["reps"] == 2
    assert agg["trigger_recall"] == 0.5  # mean of 1.0 and 0.0
    assert agg["trigger_precision"] == 1.0
    assert agg["stability"]["trigger_recall"] == 0.5  # pstdev of [1,0]
    assert agg["file_recall"] is None  # all-None objective stays None


def test_pareto_dominance_and_front():
    # b is dominated by a (better recall, same/lower cost); c trades recall for cost
    a = {"variant": "A", "trigger_recall": 1.0, "trigger_precision": 1.0,
         "synthesis_calibration": 1.0, "file_recall": None, "file_precision": None,
         "groundedness": 0.9, "cost_tokens": 100, "latency_ms": 10}
    b = {"variant": "B", "trigger_recall": 0.5, "trigger_precision": 1.0,
         "synthesis_calibration": 1.0, "file_recall": None, "file_precision": None,
         "groundedness": 0.9, "cost_tokens": 100, "latency_ms": 10}
    c = {"variant": "C", "trigger_recall": 0.5, "trigger_precision": 1.0,
         "synthesis_calibration": 1.0, "file_recall": None, "file_precision": None,
         "groundedness": 0.9, "cost_tokens": 50, "latency_ms": 10}
    assert s._dominates(a, b) is True
    assert s._dominates(a, c) is False  # a cheaper-no, c lower recall-no -> incomparable
    front = {x["variant"] for x in s.pareto_front([a, b, c])}
    assert front == {"A", "C"}  # B dominated out


def test_cli_end_to_end(tmp_path: Path):
    challenges = tmp_path / "ch.jsonl"
    candidates = tmp_path / "cand.jsonl"
    challenges.write_text(json.dumps({
        "id": "c1", "goal": "x", "sha": "deadbee", "goal_type": "feature",
        "ground_truth": {"triggers": {"riskSurfaceChange": True}, "synthesis_count": 6,
                         "synthesis_escalated": True, "files_touched": ["a.py"], "outcome": "pass"},
    }) + "\n", encoding="utf-8")
    candidates.write_text(
        json.dumps({"challenge_id": "c1", "variant": "G0", "rep": 0,
                    "assessment": {"triggers": {}, "synthesis_count": 2}, "cost": {"tokens": 9000}}) + "\n"
        + json.dumps({"challenge_id": "c1", "variant": "G1", "rep": 0,
                      "assessment": {"triggers": {"riskSurfaceChange": True}, "synthesis_count": 6,
                                     "synthesis_escalated": True}, "cost": {"tokens": 14000}, "groundedness": 0.9}) + "\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        [sys.executable, str(HERE / "assess_grounding_score.py"),
         "--candidates", str(candidates), "--challenges", str(challenges)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    assert len(res["rows"]) == 2
    g0 = next(r for r in res["rows"] if r["variant"] == "G0")
    g1 = next(r for r in res["rows"] if r["variant"] == "G1")
    assert g0["trigger_recall"] == 0.0 and g1["trigger_recall"] == 1.0  # G1 caught the real risk
    assert "G1" in res["pareto_variants"]  # the grounded variant is on the frontier


def _run_pytest() -> int:
    try:
        import pytest
    except ImportError:
        # Minimal no-pytest fallback: run every test_* in this module.
        mod = sys.modules[__name__]
        fns = [getattr(mod, n) for n in dir(mod) if n.startswith("test_") and callable(getattr(mod, n))]
        import inspect, tempfile
        failed = 0
        for fn in fns:
            try:
                if "tmp_path" in inspect.signature(fn).parameters:
                    with tempfile.TemporaryDirectory() as d:
                        fn(Path(d))
                else:
                    fn()
                print(f"PASS {fn.__name__}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {fn.__name__}: {e}")
        print(f"\n{len(fns) - failed}/{len(fns)} passed")
        return 1 if failed else 0
    return pytest.main([str(Path(__file__).resolve()), "-q"])


if __name__ == "__main__":
    raise SystemExit(_run_pytest())
