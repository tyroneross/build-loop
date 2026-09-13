#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Phase 6 Learn promotion floor cannot be satisfied by duplicate rows or a
confounded control arm — two audit findings (7 and 8) closed together because
they are the same class of defect: a promotion needs N DISTINCT runs of real,
uncontaminated evidence, not N rows.

Finding 7: ``write_run_entry.iohelpers.append_experiment_row`` upserted on
(event, run_id) by replacing only the FIRST matching line, so a ledger that
already held duplicate rows for one run_id (written before the upsert
existed, or by a race between two writers) kept every duplicate. The sweep
counted rows, so one run's duplicates could satisfy an 8-run floor alone.

Finding 8: ``learn.runner._control_mean`` only excluded runs that applied
THIS artifact, admitting runs that applied a DIFFERENT experimental artifact
into the control arm; and its floor was the constant ``CONTROL_FLOOR``
regardless of the treatment arm's size, so a large treatment arm could be
compared against a tiny, unscaled control population.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _runner():
    from learn import runner

    return runner


# ---------------------------------------------------------------------------
# Finding 7a — append_experiment_row dedupes ALL duplicate rows on write, not
# just the first match.
# ---------------------------------------------------------------------------


def test_append_experiment_row_collapses_preexisting_duplicates(tmp_path: Path) -> None:
    from write_run_entry.iohelpers import append_experiment_row

    log = tmp_path / "artifact.jsonl"
    # A ledger written before this upsert existed (or caught mid-race by two
    # writers) already holds 3 rows for run-A plus one row for run-B.
    rows = [
        {"event": "applied", "run_id": "run-A", "seq": 1},
        {"event": "applied", "run_id": "run-B", "seq": 1},
        {"event": "applied", "run_id": "run-A", "seq": 2},
        {"event": "applied", "run_id": "run-A", "seq": 3},
    ]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    append_experiment_row(log, {"event": "applied", "run_id": "run-A", "seq": 4})

    written = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    # Exactly one run-A row survives (the new write, at the FIRST match's
    # position) and the untouched run-B row keeps its relative order after it.
    assert [(r["run_id"], r.get("seq")) for r in written] == [
        ("run-A", 4),
        ("run-B", 1),
    ]


# ---------------------------------------------------------------------------
# Finding 7b — the promotion floor counts DISTINCT run_ids among applied
# rows, not rows.
# ---------------------------------------------------------------------------


def _skill(workdir: Path, name: str) -> None:
    skill = workdir / ".build-loop" / "skills" / "experimental" / name / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(f"---\nname: {name}\nuser-invocable: false\n---\n", encoding="utf-8")


def _created_row(artifact: str) -> dict:
    return {
        "event": "created",
        "artifact": artifact,
        "baseline_metric": "pass rate",
        "baseline_value": 0.5,
        "target_value": 0.8,
        "sample_size_target": 8,
    }


def _applied_row(run_id: str) -> dict:
    return {
        "event": "applied",
        "date": "2026-09-05T00:00:00Z",
        "run_id": run_id,
        "triggered": True,
        "metric_value": 1.0,
        "metric_source": "run_outcome",
        "metric_scale": [0.0, 1.0],
        "outcome": "pass",
        "co_applied_experimental_artifacts": [],
        "confounded": False,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _seed_state_and_controls(workdir: Path, background_count: int = 3, control_count: int = 8) -> str:
    """Background runs (also the run-id the sweep is invoked under) plus a
    clean, no-experiment control population. `partial` control runs keep the
    control mean below the all-`pass` treatment mean, matching the pattern
    the rest of the Learn test suite uses for a real, attributable delta.
    """
    runs = []
    for index in range(background_count):
        runs.append({
            "run_id": f"run-{index + 1}",
            "date": "2026-08-29T00:00:00Z",
            "goal": "exercise Learn",
            "outcome": "pass",
            "host": "test",
            "commit": "pending",
            "phases": {"execute": {"status": "pass"}},
            "manualInterventions": [],
            "diagnosticCommands": [],
            "filesTouched": [],
            "judge_decisions": [],
            "security_findings": [],
            "active_experimental_artifacts": [],
        })
    for index in range(control_count):
        runs.append({
            "run_id": f"control-{index}",
            "date": "2026-09-05T00:00:00Z",
            "goal": "control arm",
            "outcome": "partial",
            "host": "test",
            "commit": "pending",
            "phases": {"execute": {"status": "pass"}},
            "manualInterventions": [],
            "diagnosticCommands": [],
            "filesTouched": [],
            "judge_decisions": [],
            "security_findings": [],
            "active_experimental_artifacts": [],
        })
    state = workdir / ".build-loop" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"runs": runs}), encoding="utf-8")
    return runs[0]["run_id"]


def test_floor_rejects_rows_sharing_two_run_ids_and_accepts_eight_distinct(tmp_path: Path) -> None:
    config = tmp_path / ".build-loop" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"autoPromote": True}), encoding="utf-8")

    run_id = _seed_state_and_controls(tmp_path)

    # 8 rows, only 2 distinct run_ids -- the shape a pre-fix duplicate ledger
    # (or a hand-crafted / migrated log) can still produce.
    _skill(tmp_path, "dup-run")
    dup_run_ids = ["t1", "t1", "t1", "t1", "t2", "t2", "t2", "t2"]
    _write_jsonl(
        tmp_path / ".build-loop" / "experiments" / "dup-run.jsonl",
        [_created_row("dup-run"), *(_applied_row(rid) for rid in dup_run_ids)],
    )

    # 8 rows, 8 distinct run_ids -- everything else equal.
    _skill(tmp_path, "distinct-run")
    distinct_run_ids = [f"u{i}" for i in range(8)]
    _write_jsonl(
        tmp_path / ".build-loop" / "experiments" / "distinct-run.jsonl",
        [_created_row("distinct-run"), *(_applied_row(rid) for rid in distinct_run_ids)],
    )

    result = _runner().run(tmp_path, run_id=run_id, source="test")

    reviewer_keys = {
        order["pattern_key"]
        for order in result["work_orders"]
        if order["role"] == "promotion-reviewer"
    }
    assert "dup-run" not in reviewer_keys, "2 distinct run_ids must not satisfy an 8-run floor"
    assert "distinct-run" in reviewer_keys, "8 distinct run_ids must satisfy the floor"

    distinct_order = next(
        order for order in result["work_orders"]
        if order["role"] == "promotion-reviewer" and order["pattern_key"] == "distinct-run"
    )
    assert distinct_order["sample_size"] == 8
    assert result["stages"]["sample_sweep"]["eligible"] == 1


# ---------------------------------------------------------------------------
# Finding 8a — _control_mean excludes a run that applied ANY experimental
# artifact, not only THIS one.
# ---------------------------------------------------------------------------


def test_control_mean_excludes_run_with_a_different_experimental_artifact(tmp_path: Path) -> None:
    runner = _runner()

    clean_controls = [
        {"run_id": f"clean-{i}", "outcome": "pass", "active_experimental_artifacts": []}
        for i in range(8)
    ]
    contaminated = {
        "run_id": "contaminated-1",
        "outcome": "fail",  # a different metric value, so inclusion would be visible
        "active_experimental_artifacts": ["some-other-artifact"],
    }
    runs = [*clean_controls, contaminated]

    mean, count = runner._control_mean(runs, set(), since=None)

    assert count == 8, "the contaminated run must not be counted in the control population"
    assert mean == 1.0, "the contaminated run's outcome must not be averaged into the control mean"


# ---------------------------------------------------------------------------
# Finding 8b — the control floor scales with the treatment arm via
# `min_controls`; a treatment arm bigger than the available control
# population must not be compared at all.
# ---------------------------------------------------------------------------


def test_control_mean_returns_none_when_treatment_exceeds_available_controls(tmp_path: Path) -> None:
    runner = _runner()

    # 10 eligible controls -- enough to clear the bare CONTROL_FLOOR of 8,
    # not enough to clear a 12-run treatment arm.
    runs = [
        {"run_id": f"clean-{i}", "outcome": "pass", "active_experimental_artifacts": []}
        for i in range(10)
    ]

    mean, count = runner._control_mean(runs, set(), since=None, min_controls=12)

    assert mean is None, "10 controls must not be treated as sufficient for a 12-run treatment arm"
    assert count == 10
