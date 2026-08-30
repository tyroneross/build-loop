#!/usr/bin/env python3
"""Tests for the executable Phase 6 Learn runner."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _write_state(workdir: Path, count: int, *, cause: str | None = None) -> str:
    runs = []
    for index in range(count):
        phase = {"status": "fail" if cause else "pass"}
        if cause:
            phase["root_cause"] = cause
        runs.append(
            {
                "run_id": f"run-{index + 1}",
                "date": "2026-08-29T00:00:00Z",
                "goal": "exercise Learn",
                "outcome": "fail" if cause else "pass",
                "host": "test",
                "commit": "pending",
                "phases": {"execute": phase},
                "manualInterventions": [],
                "diagnosticCommands": [],
                "filesTouched": [],
                "judge_decisions": [],
                "security_findings": [],
                "active_experimental_artifacts": [],
            }
        )
    state = workdir / ".build-loop" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"runs": runs}), encoding="utf-8")
    return runs[-1]["run_id"]


def _runner():
    from learn import runner

    return runner


def test_accruing_writes_receipt_and_run_learn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _write_state(tmp_path, 2)
    runner = _runner()
    monkeypatch.setattr(runner.learn_accruing, "fire", lambda *_a, **_k: {"fired": True, "candidates": 0})

    result = runner.run(tmp_path, run_id=run_id, source="test", comment="Cold-read note")

    assert result["outcome"] == "accruing"
    assert result["status"] == "complete"
    assert result["learn_line"] == "Learn: accruing (2/3 runs)"
    receipt = json.loads((tmp_path / ".build-loop" / "learn" / f"{run_id}.json").read_text())
    state = json.loads((tmp_path / ".build-loop" / "state.json").read_text())
    assert receipt["stages"]["accrue"]["status"] == "complete"
    assert receipt["stages"]["notify"]["status"] == "complete"
    assert receipt["comments"][0]["text"] == "Cold-read note"
    assert state["runs"][-1]["learn"]["receipt"] == f".build-loop/learn/{run_id}.json"


def test_repeated_unchanged_run_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _write_state(tmp_path, 2)
    runner = _runner()
    calls = 0

    def fire(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"fired": True, "candidates": 0}

    monkeypatch.setattr(runner.learn_accruing, "fire", fire)
    first = runner.run(tmp_path, run_id=run_id, source="test")
    second = runner.run(tmp_path, run_id=run_id, source="test")

    assert first["already"] is False
    assert second["already"] is True
    assert calls == 1
    assert list((tmp_path / ".build-loop" / "learn").glob(f"{run_id}.json")) == [
        tmp_path / ".build-loop" / "learn" / f"{run_id}.json"
    ]


def test_stop_cannot_downgrade_completed_review_g_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _write_state(tmp_path, 2)
    runner = _runner()
    monkeypatch.setattr(runner.learn_accruing, "fire", lambda *_a, **_k: {"fired": True})

    completed = runner.run(tmp_path, run_id=run_id, source="review-g", accrue=True)
    stopped = runner.run(tmp_path, run_id=run_id, source="stop", accrue=False)

    assert completed["status"] == "complete"
    assert stopped["status"] == "complete"
    assert stopped["already"] is True
    state = json.loads((tmp_path / ".build-loop" / "state.json").read_text())
    current = next(item for item in state["runs"] if item["run_id"] == run_id)
    assert current["learn"]["status"] == "complete"


def test_stop_latency_boundary_can_be_completed_by_manual_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _write_state(tmp_path, 2)
    runner = _runner()
    calls = 0

    def fire(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"fired": True, "candidates": 0}

    monkeypatch.setattr(runner.learn_accruing, "fire", fire)
    stop_receipt = runner.run(tmp_path, run_id=run_id, source="stop", accrue=False)
    manual_receipt = runner.run(tmp_path, run_id=run_id, source="manual")

    assert stop_receipt["status"] == "pending"
    assert stop_receipt["stages"]["accrue"]["status"] == "pending"
    assert manual_receipt["status"] == "complete"
    assert manual_receipt["already"] is False
    assert calls == 1


def test_repeated_root_cause_emits_architect_work_order(tmp_path: Path) -> None:
    run_id = _write_state(tmp_path, 3, cause="closeout skipped Learn")
    result = _runner().run(tmp_path, run_id=run_id, source="test")

    assert result["outcome"] == "full"
    assert result["status"] == "awaiting_agents"
    orders = result["work_orders"]
    assert len(orders) == 1
    assert orders[0]["role"] == "self-improvement-architect"
    assert orders[0]["pattern_key"] == "closeout-skipped-learn"
    assert orders[0]["status"] == "pending"
    assert result["learn_line"] == "Learn: 1 pattern awaiting draft review"


def test_repeated_manual_intervention_is_detected_without_llm(tmp_path: Path) -> None:
    run_id = _write_state(tmp_path, 3)
    state_path = tmp_path / ".build-loop" / "state.json"
    state = json.loads(state_path.read_text())
    state["runs"][0]["manualInterventions"] = [{"phase": "execute", "note": "user restored Learn"}]
    state["runs"][1]["manualInterventions"] = [{"phase": "execute", "note": "user restored Learn"}]
    state_path.write_text(json.dumps(state))

    result = _runner().run(tmp_path, run_id=run_id, source="test")

    order = next(item for item in result["work_orders"] if item["source"] == "manual-intervention")
    assert order["role"] == "self-improvement-architect"
    assert order["pattern"]["count"] == 2


def test_attestation_closes_architect_and_reviewer_chain(tmp_path: Path) -> None:
    run_id = _write_state(tmp_path, 3, cause="closeout skipped Learn")
    runner = _runner()
    receipt = runner.run(tmp_path, run_id=run_id, source="test")
    architect_id = receipt["work_orders"][0]["id"]
    artifact = tmp_path / ".build-loop" / "skills" / "experimental" / "closeout" / "SKILL.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("---\nname: closeout\nuser-invocable: false\n---\n", encoding="utf-8")

    after_architect = runner.attest(
        tmp_path,
        run_id=run_id,
        work_order_id=architect_id,
        status="complete",
        artifact=str(artifact.relative_to(tmp_path)),
    )
    reviewer = next(order for order in after_architect["work_orders"] if order["role"] == "promotion-reviewer")
    assert after_architect["status"] == "awaiting_agents"

    final = runner.attest(
        tmp_path,
        run_id=run_id,
        work_order_id=reviewer["id"],
        status="complete",
        verdict="approve",
    )
    assert final["status"] == "complete"
    assert final["learn_line"] == "Learn: 1 pattern drafted and reviewed"
    state = json.loads((tmp_path / ".build-loop" / "state.json").read_text())
    assert state["runs"][-1]["learn"]["status"] == "complete"


def test_stage_failure_is_receipted_and_returns_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _write_state(tmp_path, 3)
    runner = _runner()
    monkeypatch.setattr(runner.procedural_governance, "detect_patterns", lambda *_a, **_k: 2)

    result = runner.run(tmp_path, run_id=run_id, source="test")

    assert result["status"] == "error"
    assert result["stages"]["detect"]["status"] == "error"
    assert result["errors"]
    assert (tmp_path / ".build-loop" / "learn" / f"{run_id}.json").exists()


def test_deferred_runs_deterministic_stages_and_writes_marker(tmp_path: Path) -> None:
    run_id = _write_state(tmp_path, 3, cause="repeat")
    result = _runner().run(
        tmp_path,
        run_id=run_id,
        source="test",
        defer_reason="budget exhausted",
        budget_action="finalize_and_stop",
    )

    assert result["outcome"] == "deferred"
    assert result["status"] == "complete"
    assert result["stages"]["detect"]["status"] == "complete"
    assert result["work_orders"] == []
    marker = tmp_path / ".build-loop" / "proposals" / f"learn-deferred-{run_id}.md"
    assert marker.exists()
    assert "budget exhausted" in marker.read_text()


def test_sample_sweep_emits_reviewer_order_only_for_eligible_artifact(tmp_path: Path) -> None:
    run_id = _write_state(tmp_path, 3)
    config = tmp_path / ".build-loop" / "config.json"
    config.write_text(json.dumps({"autoPromote": True}), encoding="utf-8")
    skill = tmp_path / ".build-loop" / "skills" / "experimental" / "steady" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: steady\nuser-invocable: false\n---\n", encoding="utf-8")
    experiments = tmp_path / ".build-loop" / "experiments"
    experiments.mkdir(parents=True)
    rows = [
        {
            "event": "created",
            "artifact": "steady",
            "baseline_metric": "pass rate",
            "baseline_value": 0.5,
            "target_value": 0.8,
            "sample_size_target": 8,
        }
    ]
    rows.extend(
        {"event": "applied", "run_id": f"sample-{index}", "metric_value": 0.9, "confounded": False}
        for index in range(8)
    )
    (experiments / "steady.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    result = _runner().run(tmp_path, run_id=run_id, source="test")

    reviewer = next(order for order in result["work_orders"] if order["role"] == "promotion-reviewer")
    assert reviewer["pattern_key"] == "steady"
    assert reviewer["sample_size"] == 8
    assert result["stages"]["sample_sweep"]["eligible"] == 1


def test_oversized_experiment_preserves_created_row_and_changes_digest_on_append(tmp_path: Path) -> None:
    runner = _runner()
    run_id = _write_state(tmp_path, 3)
    config = tmp_path / ".build-loop" / "config.json"
    config.write_text(json.dumps({"autoPromote": True}), encoding="utf-8")
    skill = tmp_path / ".build-loop" / "skills" / "experimental" / "large" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: large\nuser-invocable: false\n---\n", encoding="utf-8")
    log = tmp_path / ".build-loop" / "experiments" / "large.jsonl"
    log.parent.mkdir(parents=True)
    created = {
        "event": "created", "artifact": "large", "baseline_metric": "pass rate",
        "baseline_value": 0.5, "target_value": 0.8, "sample_size_target": 8,
    }
    filler = {"event": "ignored", "detail": "x" * 500}
    applied = {"event": "applied", "metric_value": 0.9, "confounded": False}
    rows = [created, *(filler for _ in range(1_100)), *(applied for _ in range(8))]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    first = runner.run(tmp_path, run_id=run_id, source="test")
    reviewer = next(order for order in first["work_orders"] if order["role"] == "promotion-reviewer")
    assert reviewer["pattern_key"] == "large"
    assert str(log) in first["stages"]["sample_sweep"]["truncated_inputs"]

    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(applied) + "\n")
    second = runner.run(tmp_path, run_id=run_id, source="test")

    assert second["already"] is False
    assert second["input_digest"] != first["input_digest"]


def test_cli_emits_json_and_nonzero_on_missing_run(tmp_path: Path) -> None:
    state = tmp_path / ".build-loop" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"runs": []}', encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "learn" / "__main__.py"),
            "run",
            "--workdir",
            str(tmp_path),
            "--run-id",
            "missing",
            "--source",
            "test",
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert payload["status"] == "error"
    assert "missing" in payload["errors"][0]


def test_run_id_cannot_escape_the_learn_receipt_directory(tmp_path: Path) -> None:
    _write_state(tmp_path, 1)

    with pytest.raises(ValueError, match="single"):
        _runner().run(tmp_path, run_id="../escape", source="test")

    assert not (tmp_path / ".build-loop" / "escape.json").exists()


def test_architect_attestation_rejects_artifact_outside_repository(tmp_path: Path) -> None:
    run_id = _write_state(tmp_path, 3, cause="closeout skipped Learn")
    runner = _runner()
    receipt = runner.run(tmp_path, run_id=run_id, source="test")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-proof.md"
    outside.write_text("outside", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="inside the repository"):
            runner.attest(
                tmp_path,
                run_id=run_id,
                work_order_id=receipt["work_orders"][0]["id"],
                status="complete",
                artifact=f"../{outside.name}",
            )
    finally:
        outside.unlink(missing_ok=True)


def test_large_inputs_are_bounded_and_reported(tmp_path: Path) -> None:
    run_id = _write_state(tmp_path, 3)
    runner = _runner()
    traces = tmp_path / ".build-loop" / "telemetry" / "tool-traces.jsonl"
    traces.parent.mkdir(parents=True)
    row = json.dumps({"tool": "repeat-call", "detail": "x" * 400}) + "\n"
    traces.write_text(row * (runner.MAX_JSONL_ROWS + 50), encoding="utf-8")
    experiments = tmp_path / ".build-loop" / "experiments"
    experiments.mkdir(parents=True)
    oversized = experiments / "large.jsonl"
    oversized.write_text("x" * (runner.MAX_DIGEST_FILE_BYTES + 50), encoding="utf-8")

    result = runner.run(tmp_path, run_id=run_id, source="test")

    assert str(traces) in result["stages"]["collect"]["truncated_inputs"]
    assert ".build-loop/experiments/large.jsonl" in result["input_limits"]["truncated_files"]
    assert result["patterns_count"] <= runner.PATTERN_CAP
