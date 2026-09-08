#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from token_efficiency_benchmark import compare, load_rows, measured_tokens  # noqa: E402


def _row(task: str, variant: str, *, tokens: int | None, passed: bool = True, snapshot: str = "sha1") -> dict:
    row = {
        "task_id": task,
        "variant": variant,
        "model": "gpt-5.6-terra",
        "snapshot": snapshot,
        "passed": passed,
        "escaped_defects": 0,
        "calls": 2,
        "duration_seconds": 10,
    }
    if tokens is not None:
        row["input_tokens"] = tokens - 100
        row["output_tokens"] = 100
    else:
        row["tokens_estimate"] = 99_999
    return row


def test_measured_tokens_excludes_estimates() -> None:
    assert measured_tokens({"tokens_estimate": 1000}) is None
    assert measured_tokens({"input_tokens": 700, "output_tokens": 300}) == 1000


def test_compare_uses_only_exact_repeat_pairs() -> None:
    rows = [
        _row("t1", "baseline", tokens=10_000),
        _row("t1", "candidate", tokens=6_000),
        _row("t2", "baseline", tokens=20_000, snapshot="sha-old"),
        _row("t2", "candidate", tokens=5_000, snapshot="sha-new"),
    ]
    result = compare(rows, baseline="baseline", candidate="candidate")
    paired = result["exact_repeat"]
    assert paired["pairs"] == 1
    assert paired["measured_pairs"] == 1
    assert paired["token_change_pct"] == -40.0
    assert paired["quality_non_inferior"] is True


def test_quality_regression_blocks_non_inferior_verdict() -> None:
    rows = [
        _row("t1", "baseline", tokens=10_000, passed=True),
        _row("t1", "candidate", tokens=4_000, passed=False),
    ]
    result = compare(rows, baseline="baseline", candidate="candidate")
    assert result["exact_repeat"]["token_change_pct"] == -60.0
    assert result["exact_repeat"]["quality_non_inferior"] is False


def test_unmeasured_pair_is_reported_without_token_claim() -> None:
    rows = [
        _row("t1", "baseline", tokens=None),
        _row("t1", "candidate", tokens=None),
    ]
    result = compare(rows, baseline="baseline", candidate="candidate")
    assert result["exact_repeat"]["pairs"] == 1
    assert result["exact_repeat"]["measured_pairs"] == 0
    assert result["exact_repeat"]["token_change_pct"] is None


def test_pilot_missing_defect_followup_does_not_become_zero() -> None:
    rows = [_pilot_row("baseline"), _pilot_row("candidate")]
    del rows[1]["escaped_defects"]
    report = _pilot(rows)
    assert "variants" not in report
    assert report["exact_repeat"]["candidate_escaped_defects"] is None
    assert report["pilot"]["strata"][0]["metrics"]["escaped_defects"]["change"] is None


def test_cli_and_input_validation(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text(
        "\n".join(json.dumps(row) for row in (
            _row("t1", "baseline", tokens=1000),
            _row("t1", "candidate", tokens=800),
        )) + "\n",
        encoding="utf-8",
    )
    loaded = load_rows(results)
    assert len(loaded) == 2
    completed = subprocess.run(
        [
            sys.executable,
            str(HERE / "token_efficiency_benchmark.py"),
            "--results", str(results),
            "--baseline", "baseline",
            "--candidate", "candidate",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["exact_repeat"]["token_change_pct"] == -20.0


def test_missing_required_field_fails(tmp_path: Path) -> None:
    results = tmp_path / "bad.jsonl"
    results.write_text(json.dumps({"task_id": "t1"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing variant"):
        load_rows(results)


@pytest.mark.parametrize("rows", [[], [
    _row("different-task", "baseline", tokens=1000),
    _row("t1", "candidate", tokens=100),
]])
def test_no_pairs_cannot_establish_quality(rows: list[dict]) -> None:
    paired = compare(rows, baseline="baseline", candidate="candidate")["exact_repeat"]
    assert paired["evidence_status"] == "insufficient_evidence"
    assert paired["quality_non_inferior"] is None
    assert paired["token_change_pct"] is None


@pytest.mark.parametrize("field,value", [
    ("passed", "false"), ("passed", 1), ("model", None), ("trial_id", ""),
    ("input_tokens", True), ("input_tokens", -1), ("output_tokens", "100"),
    ("measured_total_tokens", False), ("escaped_defects", -1), ("calls", 1.5),
    ("duration_seconds", float("nan")), ("duration_seconds", float("inf")),
])
def test_invalid_receipt_rejected_at_both_entrypoints(tmp_path: Path, field: str, value: object) -> None:
    row = _row("t1", "baseline", tokens=1000)
    row[field] = value
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=field):
        load_rows(path)
    with pytest.raises(ValueError, match=field):
        compare([row], baseline="baseline", candidate="candidate")


@pytest.mark.parametrize("receipt", [
    {"output_tokens": 100}, {"input_tokens": 100}, {"cache_read_input_tokens": 100},
    {"measured_total_tokens": True}, {"input_tokens": True, "output_tokens": 100},
])
def test_partial_or_boolean_usage_is_not_a_measured_total(receipt: dict) -> None:
    assert measured_tokens(receipt) is None


def test_explicit_and_complete_totals_support_zero_and_cache_buckets() -> None:
    assert measured_tokens({"measured_total_tokens": 0}) == 0
    assert measured_tokens({"input_tokens": 0, "output_tokens": 0}) == 0
    assert measured_tokens({"input_tokens": 500, "output_tokens": 100, "cache_read_input_tokens": 200}) == 800


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_trials_are_rejected_independent_of_order(reverse: bool) -> None:
    candidates = [_row("t1", "candidate", tokens=100), _row("t1", "candidate", tokens=2000)]
    if reverse:
        candidates.reverse()
    with pytest.raises(ValueError, match="duplicate exact-repeat row"):
        compare([_row("t1", "baseline", tokens=1000), *candidates], baseline="baseline", candidate="candidate")


def test_explicit_trials_pair_without_dropping_repeats() -> None:
    rows = [
        dict(_row("t1", "baseline", tokens=1000), trial_id="1"),
        dict(_row("t1", "candidate", tokens=100), trial_id="1"),
        dict(_row("t1", "baseline", tokens=1000), trial_id="2"),
        dict(_row("t1", "candidate", tokens=2000), trial_id="2"),
    ]
    paired = compare(rows, baseline="baseline", candidate="candidate")["exact_repeat"]
    assert paired["pairs"] == 2
    assert paired["token_change_pct"] == 5.0
    assert paired["evidence_status"] == "complete"
    rows[-1]["trial_id"] = "unmatched"
    assert compare(rows, baseline="baseline", candidate="candidate")["exact_repeat"]["pairs"] == 1


def test_missing_measurement_cannot_make_partial_cost_look_like_a_win() -> None:
    rows = [
        _row("t1", "baseline", tokens=1000), _row("t1", "candidate", tokens=100),
        _row("t2", "baseline", tokens=1000), _row("t2", "candidate", tokens=None),
    ]
    result = compare(rows, baseline="baseline", candidate="candidate")
    assert result["exact_repeat"]["measured_pairs"] == 1
    assert result["exact_repeat"]["token_change_pct"] is None
    assert result["exact_repeat"]["evidence_status"] == "insufficient_evidence"
    assert result["variants"]["candidate"]["raw_tokens_per_passed_run"] is None


def test_same_variant_is_not_a_comparison() -> None:
    with pytest.raises(ValueError, match="different variants"):
        compare([_row("t1", "baseline", tokens=1000)], baseline="baseline", candidate="baseline")


def _pilot_row(variant: str, **updates: object) -> dict:
    return dict(_row("lookup", variant, tokens=1000), **{
        "trial_id": "1", "experiment_axis": "execution", "task_shape": "fact_lookup",
        "controls_id": "frozen-brief-rubric-budget-v1", "evidence_kind": "calibration",
        "user_interventions": 0, "lost_decisions": 0, "rework": 0,
        "grounded_claims": 2, "checked_claims": 2, "covered_items": 2, "expected_items": 3,
        **updates,
    })


def _pilot(rows: list[dict]) -> dict:
    return compare(rows, baseline="baseline", candidate="candidate", pilot=True)


def test_pilot_reports_paired_metrics_without_a_pooled_winner() -> None:
    result = _pilot([_pilot_row("baseline"), _pilot_row("candidate", duration_seconds=5)])
    assert result["pilot"]["status"] == "exploratory_only"
    metrics = result["pilot"]["strata"][0]["metrics"]
    assert metrics["duration_seconds"]["change"] == -5
    assert metrics["grounding"]["candidate"] == 1
    assert metrics["coverage"]["candidate"] == pytest.approx(2 / 3, abs=1e-6)
    assert metrics["raw_tokens"]["candidate"] == 1000
    assert result["exact_repeat"]["quality_non_inferior"] is None
    assert result["exact_repeat"]["token_change_pct"] is None


@pytest.mark.parametrize("field,value", [
    ("controls_id", "different-budget"), ("task_shape", "deep_synthesis"),
    ("experiment_axis", "context"), ("evidence_kind", "live"),
])
def test_pilot_excludes_confounded_pairs(field: str, value: str) -> None:
    result = _pilot([_pilot_row("baseline"), _pilot_row("candidate", **{field: value})])
    assert result["exact_repeat"]["pairs"] == 0
    assert result["pilot"]["strata"] == []
    assert result["pilot"]["confounded_pairs"][0]["mismatched_controls"] == [field]


@pytest.mark.parametrize("field", ["controls_id", "task_shape", "experiment_axis", "trial_id", "evidence_kind"])
def test_pilot_requires_controls(field: str) -> None:
    row = _pilot_row("baseline")
    del row[field]
    with pytest.raises(ValueError, match=field):
        _pilot([row])


@pytest.mark.parametrize("field,value", [
    ("user_interventions", True), ("lost_decisions", -1), ("rework", 1.5),
    ("grounded_claims", 3), ("checked_claims", "2"), ("covered_items", 4),
    ("experiment_axis", "everything"), ("evidence_kind", "estimated"),
])
def test_pilot_rejects_invalid_measurements(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _pilot([_pilot_row("baseline", **{field: value})])


@pytest.mark.parametrize("missing,metric", [
    ("duration_seconds", "duration_seconds"), ("lost_decisions", "lost_decisions"),
    ("checked_claims", "grounding"), ("expected_items", "coverage"),
    ("input_tokens", "raw_tokens"),
])
def test_pilot_never_treats_missing_measurements_as_zero(missing: str, metric: str) -> None:
    rows = [_pilot_row("baseline"), _pilot_row("candidate")]
    del rows[1][missing]
    measurement = _pilot(rows)["pilot"]["strata"][0]["metrics"][metric]
    assert measurement == {"measured_pairs": 0, "missing_pairs": 1, "baseline": None, "candidate": None, "change": None}


def test_pilot_zero_denominator_and_partial_pairs_remain_unknown() -> None:
    rows = [_pilot_row("baseline"), _pilot_row("candidate")]
    rows += [_pilot_row("baseline", trial_id="2"), _pilot_row("candidate", trial_id="2", grounded_claims=0, checked_claims=0)]
    metric = _pilot(rows)["pilot"]["strata"][0]["metrics"]["grounding"]
    assert metric["measured_pairs"] == 1
    assert metric["missing_pairs"] == 1
    assert metric["change"] is None


def test_pilot_separates_task_shapes_and_live_from_calibration() -> None:
    rows = [_pilot_row("baseline"), _pilot_row("candidate", passed=False)]
    for variant in ("baseline", "candidate"):
        rows.extend([
            _pilot_row(variant, trial_id="2", task_shape="breadth_survey"),
            _pilot_row(variant, trial_id="3", evidence_kind="live"),
        ])
    report = _pilot(rows)["pilot"]
    assert len(report["strata"]) == 3
    assert sum(s["regressed_pairs"] for s in report["strata"]) == 1
    assert report["unmatched_runs"] == 0
    rows[-1]["snapshot"] = "other-sha"
    assert _pilot(rows)["pilot"]["unmatched_runs"] == 2


def test_pilot_cli_requires_opt_in_and_reports_calibration(tmp_path: Path) -> None:
    path = tmp_path / "pilot.jsonl"
    path.write_text("\n".join(json.dumps(_pilot_row(v)) for v in ("baseline", "candidate")))
    cmd = [sys.executable, str(HERE / "token_efficiency_benchmark.py"), "--results", str(path), "--baseline", "baseline", "--candidate", "candidate"]
    legacy = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
    assert legacy.returncode == 0
    assert "pilot" not in json.loads(legacy.stdout)
    strict = subprocess.run([*cmd, "--pilot"], text=True, capture_output=True, timeout=30)
    assert strict.returncode == 0, strict.stderr
    assert json.loads(strict.stdout)["pilot"]["strata"][0]["evidence_kind"] == "calibration"
