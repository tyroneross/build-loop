#!/usr/bin/env python3
"""Tests for the executable Phase 6 Learn runner."""
from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _append_applied_rows(
    workdir: Path, artifact: str, *, count: int, start: int = 0, outcome: str = "pass"
) -> None:
    """Write applied rows with the PRODUCTION writer, never a fixture.

    The A/B promotion gate was inert for exactly this reason: the sole
    production writer of applied rows hardcoded `metric_value: None`, which
    fails this consumer's numeric filter, and the tests injected 0.9 into row
    fixtures no writer produced. A consumer test that builds its own input
    cannot see a writer that stopped producing it.
    """
    from write_run_entry.iohelpers import append_experiment_rows

    experiments = workdir / ".build-loop" / "experiments"
    experiments.mkdir(parents=True, exist_ok=True)
    for index in range(start, start + count):
        append_experiment_rows(
            experiments,
            f"sample-{index}",
            [artifact],
            outcome,
            "2026-09-12T00:00:00Z",
        )


def _seed_control_runs(workdir: Path, count: int = 8, outcome: str = "partial") -> None:
    """Append runs that did NOT apply any experimental artifact.

    The sweep now gates on the DELTA between the treatment arm and a control
    arm, so a promotion test without a control population tests the
    control-missing branch, not the promotion. `partial` (0.5) is the default so
    an all-`pass` treatment arm produces a real, attributable delta.
    """
    path = workdir / ".build-loop" / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    for index in range(count):
        state["runs"].append({
            "run_id": f"control-{index}",
            "date": "2026-09-12T00:00:00Z",
            "goal": "control arm",
            "outcome": outcome,
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
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


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


def _tool_trace():
    import tool_trace

    return tool_trace


def _error_span_rows(workdir: Path, tool_name: str, count: int, *, is_error: bool = True) -> str:
    """``count`` real spans for ``tool_name``, built by the sole production writer.

    Uses ``tool_trace.build_span`` so fixtures match what ``.build-loop/telemetry/
    tool-traces.jsonl`` actually contains: no top-level ``tool``/``operation`` key,
    the real tool name only at ``attributes["gen_ai.tool.name"]``, and
    ``status.code`` set from ``is_error``.
    """
    tool_trace = _tool_trace()
    rows = [
        json.dumps(
            tool_trace.build_span(
                workdir=workdir,
                session_id="test-session",
                tool_name=tool_name,
                tool_use_id=f"call-{index}",
                phase="end",
                is_error=is_error,
                ordinal=index,
            )
        )
        for index in range(count)
    ]
    return "\n".join(rows) + "\n"


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


def test_legacy_intervention_rows_are_clustered_and_empty_stop_hook_notices_are_dropped() -> None:
    runner = _runner()
    patterns, manual_count, security_count = runner._recurring_run_patterns([
        {"manualInterventions": [{"phase": "execute", "intervention": "user restored Learn"}]},
        {"manualInterventions": [{"phase": "execute", "intervention": "user restored Learn"}]},
        {"manualInterventions": [{"phase": "6", "note": "fired-by-stop-hook"}]},
        {"manualInterventions": [{"phase": "6", "note": "closeout:fired-by-stop-hook (inline run did not reach Review-G)"}]},
    ])

    assert [item["payload"] for item in patterns] == [{
        "type": "manual_intervention",
        "signature": "user restored Learn",
        "count": 2,
    }]
    assert manual_count == 1
    assert security_count == 0


def test_stop_hook_notice_with_causal_detail_remains_clusterable() -> None:
    runner = _runner()
    patterns, manual_count, _security_count = runner._recurring_run_patterns([
        {"manualInterventions": [{"note": "closeout:fired-by-stop-hook: timeout waiting for Review-G"}]},
        {"manualInterventions": [{"note": "closeout:fired-by-stop-hook: timeout waiting for Review-G"}]},
    ])

    assert patterns[0]["payload"]["signature"] == "closeout:fired-by-stop-hook: timeout waiting for Review-G"
    assert manual_count == 1


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


def test_revised_architect_artifact_rebinds_and_reopens_existing_review(tmp_path: Path) -> None:
    run_id = _write_state(tmp_path, 3, cause="closeout skipped Learn")
    runner = _runner()
    receipt = runner.run(tmp_path, run_id=run_id, source="test")
    architect_id = receipt["work_orders"][0]["id"]
    artifacts = tmp_path / ".build-loop" / "skills" / "experimental"
    artifacts.mkdir(parents=True)
    first = artifacts / "first.md"
    second = artifacts / "second.md"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")

    after_first = runner.attest(
        tmp_path, run_id=run_id, work_order_id=architect_id,
        status="complete", artifact=str(first.relative_to(tmp_path)),
    )
    reviewer = next(order for order in after_first["work_orders"] if order["role"] == "promotion-reviewer")
    runner.attest(
        tmp_path, run_id=run_id, work_order_id=reviewer["id"],
        status="complete", verdict="revise",
    )

    revised = runner.attest(
        tmp_path, run_id=run_id, work_order_id=architect_id,
        status="complete", artifact=str(second.relative_to(tmp_path)),
    )
    rebound = next(order for order in revised["work_orders"] if order["id"] == reviewer["id"])
    assert rebound["artifact_path"] == str(second.relative_to(tmp_path))
    assert rebound["artifact_sha256"] == hashlib.sha256(b"second\n").hexdigest()
    assert rebound["status"] == "pending"
    assert "verdict" not in rebound
    prior = rebound["artifact_revisions"][0]
    assert prior["artifact_path"] == str(first.relative_to(tmp_path))
    assert prior["artifact_sha256"] == hashlib.sha256(b"first\n").hexdigest()
    assert prior["status"] == "complete"
    assert prior["attested_at"]
    assert prior["verdict"] == "revise"
    assert revised["status"] == "awaiting_agents"


def test_same_path_content_revision_reopens_existing_review(tmp_path: Path) -> None:
    run_id = _write_state(tmp_path, 3, cause="closeout skipped Learn")
    runner = _runner()
    receipt = runner.run(tmp_path, run_id=run_id, source="test")
    architect_id = receipt["work_orders"][0]["id"]
    artifact = tmp_path / ".build-loop" / "skills" / "experimental" / "draft.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("version one\n", encoding="utf-8")

    first = runner.attest(
        tmp_path, run_id=run_id, work_order_id=architect_id,
        status="complete", artifact=str(artifact.relative_to(tmp_path)),
    )
    reviewer = next(order for order in first["work_orders"] if order["role"] == "promotion-reviewer")
    runner.attest(
        tmp_path, run_id=run_id, work_order_id=reviewer["id"],
        status="complete", verdict="revise",
    )
    artifact.write_text("version two\n", encoding="utf-8")

    revised = runner.attest(
        tmp_path, run_id=run_id, work_order_id=architect_id,
        status="complete", artifact=str(artifact.relative_to(tmp_path)),
    )
    rebound = next(order for order in revised["work_orders"] if order["id"] == reviewer["id"])
    assert rebound["status"] == "pending"
    assert rebound["artifact_sha256"] == hashlib.sha256(b"version two\n").hexdigest()
    assert rebound["artifact_revisions"][0]["artifact_sha256"] == hashlib.sha256(b"version one\n").hexdigest()
    assert rebound["artifact_revisions"][0]["verdict"] == "revise"
    assert revised["status"] == "awaiting_agents"


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
    (experiments / "steady.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    # Applied rows come from the PRODUCTION writer, not a fixture. Injecting
    # metric_value 0.9 here is what hid the defect: the sole production writer
    # hardcoded `metric_value: None`, which fails this consumer's numeric
    # filter, so the promotion path was exercised only against data no writer
    # produced and `len(applied)` was always 0 on a real run.
    _append_applied_rows(tmp_path, "steady", count=8)

    _seed_control_runs(tmp_path)
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
    rows = [created, *(filler for _ in range(1_100))]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    _append_applied_rows(tmp_path, "large", count=8)

    _seed_control_runs(tmp_path)
    first = runner.run(tmp_path, run_id=run_id, source="test")
    reviewer = next(order for order in first["work_orders"] if order["role"] == "promotion-reviewer")
    assert reviewer["pattern_key"] == "large"
    assert str(log) in first["stages"]["sample_sweep"]["truncated_inputs"]

    _append_applied_rows(tmp_path, "large", count=1, start=8)
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


def test_colon_qualified_run_id_writes_exact_receipt_and_unsafe_ids_stay_rejected(tmp_path: Path) -> None:
    run_id = "bl-20260830T103035Z-codex:terra-groundwork-state-refs-971132"
    _write_state(tmp_path, 1)
    state_path = tmp_path / ".build-loop" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["runs"][-1]["run_id"] = run_id
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = _runner().run(tmp_path, run_id=run_id, source="test")

    receipt_path = tmp_path / ".build-loop" / "learn" / f"{run_id}.json"
    assert result["run_id"] == run_id
    assert receipt_path.is_file()
    assert receipt_path.parent == tmp_path / ".build-loop" / "learn"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["run_id"] == run_id
    assert json.loads(state_path.read_text(encoding="utf-8"))["runs"][-1]["learn"]["receipt"] == (
        f".build-loop/learn/{run_id}.json"
    )

    for unsafe in ("../escape", "a/b", r"a\b", ".", "..", "", "a" * 129):
        with pytest.raises(ValueError, match="single"):
            _runner().run(tmp_path, run_id=unsafe, source="test")

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


def test_repeated_tool_error_emits_pattern_under_the_real_tool_name(tmp_path: Path) -> None:
    """The defect this file exists to catch: on today's un-fixed code this is RED.

    Production rows are OTel spans whose ``name`` is the per-invocation label
    ``"execute_tool Bash"``; the real tool identity lives at
    ``attributes["gen_ai.tool.name"]``. A repeated FAILURE on the same tool must
    surface as a pattern keyed on that real name, with the invocation-label noise
    stripped out — not as zero patterns (old bug: filtered everything) and not as
    a pattern keyed on the raw span label (the original, pre-filter bug).
    """
    runner = _runner()
    traces = tmp_path / ".build-loop" / "telemetry" / "tool-traces.jsonl"
    traces.parent.mkdir(parents=True)
    traces.write_text(_error_span_rows(tmp_path, "Bash", 5, is_error=True), encoding="utf-8")

    patterns, count = runner._tool_trace_patterns(tmp_path, [])

    assert count == 1
    assert len(patterns) == 1
    assert patterns[0]["key"] == "retry-bash"
    assert patterns[0]["payload"]["signature"] == "Bash"
    assert patterns[0]["payload"]["count"] == 5


def test_repeated_tool_invocations_without_errors_emit_no_pattern(tmp_path: Path) -> None:
    """Invocation volume alone is not a pattern — only a repeated ERROR is."""
    runner = _runner()
    traces = tmp_path / ".build-loop" / "telemetry" / "tool-traces.jsonl"
    traces.parent.mkdir(parents=True)
    traces.write_text(_error_span_rows(tmp_path, "Bash", 5, is_error=False), encoding="utf-8")

    patterns, count = runner._tool_trace_patterns(tmp_path, [])

    assert patterns == []
    assert count == 0


def test_execute_tool_span_labels_are_excluded_from_tool_trace_patterns(tmp_path: Path) -> None:
    """Original filed acceptance criterion: a bare legacy row never becomes a pattern."""
    runner = _runner()
    traces = tmp_path / ".build-loop" / "telemetry" / "tool-traces.jsonl"
    traces.parent.mkdir(parents=True)
    row = json.dumps({"name": "execute_tool exec"}) + "\n"
    traces.write_text(row * 5, encoding="utf-8")

    patterns, count = runner._tool_trace_patterns(tmp_path, [])

    assert patterns == []
    assert count == 0


def test_legacy_tool_field_row_is_defensive_not_production(tmp_path: Path) -> None:
    """Legacy/defensive shape only.

    Production ``tool_trace.build_span()`` rows never carry a top-level ``tool``
    key — the real name only ever appears at ``attributes["gen_ai.tool.name"]``.
    This exercises ``_resolve_tool_signature()``'s highest-preference branch for
    any pre-OTel or hand-authored row that does carry one, paired with an ERROR
    status so the exclusion/resolution logic actually runs instead of being
    short-circuited by the error gate.
    """
    runner = _runner()
    traces = tmp_path / ".build-loop" / "telemetry" / "tool-traces.jsonl"
    traces.parent.mkdir(parents=True)
    row = json.dumps({"tool": "repeat-call", "status": {"code": "ERROR"}}) + "\n"
    traces.write_text(row * 5, encoding="utf-8")

    patterns, count = runner._tool_trace_patterns(tmp_path, [])

    assert count == 1
    assert len(patterns) == 1
    assert patterns[0]["key"] == "retry-repeat-call"


def test_mixed_execute_tool_and_real_signature_yields_only_the_real_pattern(tmp_path: Path) -> None:
    runner = _runner()
    traces = tmp_path / ".build-loop" / "telemetry" / "tool-traces.jsonl"
    traces.parent.mkdir(parents=True)
    excluded_rows = [
        json.dumps({"name": "execute_tool exec", "status": {"code": "ERROR"}}) for _ in range(5)
    ]
    real_rows = _error_span_rows(tmp_path, "repeat-call", 5, is_error=True).splitlines()
    traces.write_text("\n".join(excluded_rows + real_rows) + "\n", encoding="utf-8")

    patterns, count = runner._tool_trace_patterns(tmp_path, [])

    assert count == 1
    assert len(patterns) == 1
    assert patterns[0]["key"] == "retry-repeat-call"


def test_execute_tool_signature_does_not_reach_patterns_count_end_to_end(tmp_path: Path) -> None:
    run_id = _write_state(tmp_path, 3)
    runner = _runner()
    traces = tmp_path / ".build-loop" / "telemetry" / "tool-traces.jsonl"
    traces.parent.mkdir(parents=True)
    row = json.dumps({"name": "execute_tool exec", "status": {"code": "ERROR"}}) + "\n"
    traces.write_text(row * 5, encoding="utf-8")

    result = runner.run(tmp_path, run_id=run_id, source="test")

    assert result["stages"]["collect"]["tool_trace_patterns"] == 0
    assert result["patterns_count"] == 0


def test_exclusion_is_case_insensitive(tmp_path: Path) -> None:
    """Mutation-resistance fixture: deleting ``.lower()`` from the exclusion check
    must turn this RED. A mixed-case span label that still resolves to the
    execute_tool sentinel must stay excluded regardless of case."""
    runner = _runner()
    traces = tmp_path / ".build-loop" / "telemetry" / "tool-traces.jsonl"
    traces.parent.mkdir(parents=True)
    row = json.dumps({"name": "Execute_Tool Exec", "status": {"code": "ERROR"}}) + "\n"
    traces.write_text(row * 5, encoding="utf-8")

    patterns, count = runner._tool_trace_patterns(tmp_path, [])

    assert patterns == []
    assert count == 0


def test_real_tool_named_execute_tool_wrapper_survives_exclusion(tmp_path: Path) -> None:
    """Mutation-resistance fixture: dropping the trailing space from
    ``EXECUTE_TOOL_SPAN_PREFIX`` must turn this RED. A genuine tool whose name
    merely starts with ``execute_tool`` (no separating space) must NOT be
    excluded — the prefix check requires the literal sentinel or the sentinel
    plus a space, not an arbitrary shared prefix."""
    runner = _runner()
    traces = tmp_path / ".build-loop" / "telemetry" / "tool-traces.jsonl"
    traces.parent.mkdir(parents=True)
    traces.write_text(
        _error_span_rows(tmp_path, "execute_tool_wrapper", 5, is_error=True), encoding="utf-8"
    )

    patterns, count = runner._tool_trace_patterns(tmp_path, [])

    assert count == 1
    assert len(patterns) == 1
    assert patterns[0]["key"] == "retry-execute-tool-wrapper"
    assert patterns[0]["payload"]["signature"] == "execute_tool_wrapper"


def test_actual_legacy_rows_do_not_collapse_into_a_none_pattern() -> None:
    # Exact four legacy intervention rows from the 2026-09-07 main-state audit.
    rows = [{'intervention': 'fixed _py_imports to emit submodule edges before package edges; one iterate pass', 'phase': 'execute'}, {'intervention': 'capture_arch_violation.py: accept short-form rule shape (rule, component_id) emitted by native engine; previously dropped all 77 violations as empty rule_id', 'phase': 'execute'}, {'intervention': 'memory_facade: read both legacy .episodic/decisions and post-cutover ~/dev/git-folder/build-loop-memory/decisions/<project>/ paths', 'phase': 'execute'}, {'intervention': '3-of-7 dead scripts moved to attic; 4 supposedly-dead were live (embed_backend, _test_helpers, transcript-pattern-miner, build_acp/slice_acp); reported in commit body', 'phase': 'execute'}]
    runs = [{"manualInterventions": rows}, {"manualInterventions": [{"note": "fired-by-stop-hook (inline run did not reach Review-G)"}]}, {"manualInterventions": [{"note": "fired-by-stop-hook (inline run did not reach Review-G)"}]}]
    patterns, count, _ = _runner()._recurring_run_patterns(runs)
    assert patterns == []
    assert count == 0


def test_generic_notice_filter_preserves_real_causal_detail() -> None:
    note = "fired-by-stop-hook (inline run; Fable session later corrected the auditor floor)"
    assert _runner()._manual_intervention_signature({"note": note}) == note


# ---------------------------------------------------------------------------
# Writer -> consumer, end to end.
#
# Every A/B promotion gate was inert and no test saw it, because the writer and
# the consumer were only ever tested apart: the writer's tests asserted a row
# was appended without asserting it was MEASURABLE, and the consumer's tests
# built their own rows with metric_value 0.9. These two tests run the real
# production writer (`scripts/write_run_entry/__main__.py`, the CLI Review-G
# actually invokes) and then the real consumer, so the seam is covered.
# ---------------------------------------------------------------------------

WRITE_RUN_ENTRY = SCRIPTS / "write_run_entry" / "__main__.py"


def _seed_experiment(workdir: Path, name: str, *, target: float = 0.8) -> Path:
    skill = workdir / ".build-loop" / "skills" / "experimental" / name / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(f"---\nname: {name}\nuser-invocable: false\n---\n", encoding="utf-8")
    (workdir / ".build-loop" / "config.json").write_text(
        json.dumps({"autoPromote": True}), encoding="utf-8"
    )
    log = workdir / ".build-loop" / "experiments" / f"{name}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({
            "event": "created", "artifact": name, "baseline_metric": "run outcome",
            "baseline_value": 0.5, "target_value": target, "sample_size_target": 8,
        }) + "\n",
        encoding="utf-8",
    )
    return log


def _write_run_entry_cli(
    workdir: Path, artifact: str, *, run_id: str, outcome: str = "pass"
) -> None:
    proc = subprocess.run(
        [
            sys.executable, str(WRITE_RUN_ENTRY),
            "--workdir", str(workdir),
            "--run-id", run_id,
            "--goal", "end-to-end metric check",
            "--outcome", outcome,
            "--phases-json", '{"assess":{"status":"pass"}}',
            "--active-experimental-artifacts", artifact,
        ],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_production_writer_rows_survive_the_consumer_metric_filter(tmp_path: Path) -> None:
    """The whole defect, in one assertion.

    `scripts/learn/runner.py` keeps only applied rows whose `metric_value` is
    numeric; `scripts/write_run_entry` is the sole production writer of those
    rows and wrote None, so `len(applied)` was always 0, always below the floor
    of `max(8, sample_size_target)`, and the sample-sweep promotion never fired
    from a real run.
    """
    _seed_experiment(tmp_path, "e2e")
    for index in range(8):
        _write_run_entry_cli(tmp_path, "e2e", run_id=f"e2e-run-{index}")

    run_id = _write_state(tmp_path, 3)
    _seed_control_runs(tmp_path)
    result = _runner().run(tmp_path, run_id=run_id, source="test")

    sweep = result["stages"]["sample_sweep"]
    assert sweep["eligible"] == 1, sweep
    assert sweep["metric_missing"] == 0, sweep
    reviewer = next(o for o in result["work_orders"] if o["role"] == "promotion-reviewer")
    assert reviewer["pattern_key"] == "e2e"
    assert reviewer["target_metric"]["observed"] == 1.0


def test_unmeasured_rows_are_reported_as_metric_missing_not_silently_dropped(
    tmp_path: Path,
) -> None:
    """An unmeasurable sample and a too-small sample must not look the same.

    Both used to exit the sweep through one `continue`, so a gate that could
    never fire reported exactly what a young experiment reports.
    """
    log = _seed_experiment(tmp_path, "unmeasured")
    with log.open("a", encoding="utf-8") as handle:
        for index in range(8):
            handle.write(json.dumps({
                "event": "applied", "run_id": f"legacy-{index}",
                "metric_value": None, "confounded": False,
            }) + "\n")

    run_id = _write_state(tmp_path, 3)
    sweep = _runner().run(tmp_path, run_id=run_id, source="test")["stages"]["sample_sweep"]

    assert sweep["eligible"] == 0
    assert sweep["metric_missing"] == 8
    detail = sweep["metric_missing_detail"][0]
    assert detail["artifact"] == "unmeasured"
    assert detail["non_confounded_applied"] == 8
    assert detail["measured"] == 0


def test_one_run_contributes_one_sample_however_often_it_is_rewritten(tmp_path: Path) -> None:
    """A rewrite corrects a run's row; it does not manufacture a second sample.

    The run ledger upserts on run_id while this log appended unconditionally, so
    eight writes of ONE run cleared an eight-run promotion floor by themselves,
    and a corrected outcome left its superseded value in the average.
    """
    log = _seed_experiment(tmp_path, "rewrite")
    for _ in range(8):
        _write_run_entry_cli(tmp_path, "rewrite", run_id="same-run")
    _write_run_entry_cli(tmp_path, "rewrite", run_id="same-run", outcome="fail")

    applied = [
        json.loads(line) for line in log.read_text().strip().splitlines()
        if json.loads(line).get("event") == "applied"
    ]
    assert len(applied) == 1
    assert applied[0]["metric_value"] == 0.0  # the correction, not the superseded pass

    run_id = _write_state(tmp_path, 3)
    sweep = _runner().run(tmp_path, run_id=run_id, source="test")["stages"]["sample_sweep"]
    assert sweep["eligible"] == 0


def test_an_artifact_name_cannot_escape_the_experiments_directory(tmp_path: Path) -> None:
    from write_run_entry.iohelpers import append_experiment_rows

    experiments = tmp_path / ".build-loop" / "experiments"
    experiments.mkdir(parents=True)
    append_experiment_rows(
        experiments, "run-x", ["../../../outside/new"], "pass", "2026-09-12T00:00:00Z"
    )
    assert not (tmp_path.parent / "outside").exists()
    assert list(experiments.glob("*.jsonl")) == []


def test_outcome_rows_are_not_graded_against_an_incompatible_metric(tmp_path: Path) -> None:
    """baseline 10 / target 5 seconds cannot be met by a pass/fail score of 1.0."""
    _seed_experiment(tmp_path, "seconds")
    log = tmp_path / ".build-loop" / "experiments" / "seconds.jsonl"
    log.write_text(
        json.dumps({
            "event": "created", "artifact": "seconds", "baseline_metric": "seconds to complete",
            "baseline_value": 10, "target_value": 5, "sample_size_target": 8,
        }) + "\n",
        encoding="utf-8",
    )
    for index in range(8):
        _write_run_entry_cli(tmp_path, "seconds", run_id=f"sec-{index}")

    run_id = _write_state(tmp_path, 3)
    sweep = _runner().run(tmp_path, run_id=run_id, source="test")["stages"]["sample_sweep"]
    assert sweep["eligible"] == 0
    assert sweep["metric_mismatch"] == 1
    assert sweep["metric_mismatch_detail"][0]["artifact"] == "seconds"


def test_a_created_row_after_the_first_line_still_anchors_the_sweep(tmp_path: Path) -> None:
    """The writer can now create the log, so `applied` may precede `created`."""
    _seed_experiment(tmp_path, "late")
    log = tmp_path / ".build-loop" / "experiments" / "late.jsonl"
    rows = [{"event": "applied", "run_id": "pre-0", "metric_value": 1.0,
             "metric_source": "run_outcome", "confounded": False}]
    rows.append({
        "event": "created", "artifact": "late", "baseline_metric": "pass rate",
        "baseline_value": 0.5, "target_value": 0.8, "sample_size_target": 8,
    })
    rows.extend({"event": "ignored", "detail": "x" * 500} for _ in range(1_100))
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    _append_applied_rows(tmp_path, "late", count=8)

    run_id = _write_state(tmp_path, 3)
    _seed_control_runs(tmp_path)
    sweep = _runner().run(tmp_path, run_id=run_id, source="test")["stages"]["sample_sweep"]
    assert sweep["eligible"] == 1, sweep


def test_a_treatment_arm_that_only_matches_the_base_rate_is_not_promoted(tmp_path: Path) -> None:
    """Eight applied runs pass; eight non-applied runs in the same window also pass.

    The artifact changed nothing. The level comparison promoted it anyway,
    because a hand-authored target at or below the ambient outcome mean is met
    by the base rate alone.
    """
    _seed_experiment(tmp_path, "baserate")
    for index in range(8):
        _write_run_entry_cli(tmp_path, "baserate", run_id=f"br-{index}")

    run_id = _write_state(tmp_path, 3)
    _seed_control_runs(tmp_path, count=8, outcome="pass")
    sweep = _runner().run(tmp_path, run_id=run_id, source="test")["stages"]["sample_sweep"]

    assert sweep["eligible"] == 0, sweep
    assert sweep["metric_missing"] == 0, sweep


def test_no_control_population_blocks_promotion_and_says_why(tmp_path: Path) -> None:
    _seed_experiment(tmp_path, "nocontrol")
    for index in range(8):
        _write_run_entry_cli(tmp_path, "nocontrol", run_id=f"nc-{index}")

    run_id = _write_state(tmp_path, 3)
    sweep = _runner().run(tmp_path, run_id=run_id, source="test")["stages"]["sample_sweep"]

    assert sweep["eligible"] == 0
    assert sweep["control_missing"] == 1
    detail = sweep["control_missing_detail"][0]
    assert detail["artifact"] == "nocontrol"
    assert detail["control_runs_required"] == 8


def test_one_legacy_row_cannot_disable_the_metric_scale_guard(tmp_path: Path) -> None:
    """`all` let a single pre-metric row switch the guard off for the artifact."""
    _seed_experiment(tmp_path, "mixed")
    log = tmp_path / ".build-loop" / "experiments" / "mixed.jsonl"
    log.write_text(
        json.dumps({
            "event": "created", "artifact": "mixed", "baseline_metric": "seconds to complete",
            "baseline_value": 10, "target_value": 5, "sample_size_target": 8,
        }) + "\n"
        + json.dumps({
            "event": "applied", "run_id": "legacy-0", "metric_value": 0.9, "confounded": False,
        }) + "\n",
        encoding="utf-8",
    )
    for index in range(8):
        _write_run_entry_cli(tmp_path, "mixed", run_id=f"mx-{index}")

    run_id = _write_state(tmp_path, 3)
    _seed_control_runs(tmp_path)
    sweep = _runner().run(tmp_path, run_id=run_id, source="test")["stages"]["sample_sweep"]

    assert sweep["eligible"] == 0, sweep
    assert sweep["metric_mismatch"] == 1, sweep


def test_an_out_of_window_control_population_does_not_count(tmp_path: Path) -> None:
    """Eight runs from years ago are a different era, not a control arm."""
    _seed_experiment(tmp_path, "stale")
    for index in range(8):
        _write_run_entry_cli(tmp_path, "stale", run_id=f"st-{index}")

    run_id = _write_state(tmp_path, 3)
    path = tmp_path / ".build-loop" / "state.json"
    state = json.loads(path.read_text())
    for row in state["runs"]:
        row["date"] = "2020-01-01T00:00:00Z"
    for index in range(8):
        state["runs"].append({
            "run_id": f"ancient-{index}", "date": "2020-01-01T00:00:00Z",
            "goal": "old", "outcome": "fail", "host": "test", "commit": "pending",
            "phases": {}, "manualInterventions": [], "diagnosticCommands": [],
            "filesTouched": [], "judge_decisions": [], "security_findings": [],
            "active_experimental_artifacts": [],
        })
    path.write_text(json.dumps(state), encoding="utf-8")

    sweep = _runner().run(tmp_path, run_id=run_id, source="test")["stages"]["sample_sweep"]
    assert sweep["eligible"] == 0, sweep
    assert sweep["control_missing"] == 1, sweep


def test_a_caller_supplied_metric_is_not_differenced_against_run_outcomes(
    tmp_path: Path,
) -> None:
    """"seconds to complete" shares no scale with a pass/fail control mean.

    Differencing them both blocked real improvements (5s against a control of
    1.0) and passed flat ones (10 - 1 >= 1). Those experiments keep the level
    comparison and the receipt says which rule ran.
    """
    from write_run_entry.iohelpers import append_experiment_rows

    _seed_experiment(tmp_path, "seconds2")
    log = tmp_path / ".build-loop" / "experiments" / "seconds2.jsonl"
    log.write_text(
        json.dumps({
            "event": "created", "artifact": "seconds2",
            "baseline_metric": "seconds to complete",
            "baseline_value": 10, "target_value": 5, "sample_size_target": 8,
        }) + "\n",
        encoding="utf-8",
    )
    experiments = tmp_path / ".build-loop" / "experiments"
    for index in range(8):
        append_experiment_rows(
            experiments, f"sec2-{index}", ["seconds2"], "pass",
            "2026-09-12T00:00:00Z", metric_value=5.0,
        )

    run_id = _write_state(tmp_path, 3)
    _seed_control_runs(tmp_path, count=8, outcome="pass")
    result = _runner().run(tmp_path, run_id=run_id, source="test")
    sweep = result["stages"]["sample_sweep"]

    assert sweep["eligible"] == 1, sweep
    reviewer = next(o for o in result["work_orders"] if o["role"] == "promotion-reviewer")
    assert "level" in reviewer["target_metric"]["comparison"]
    assert reviewer["target_metric"]["observed"] == 5.0


def test_the_head_scan_skips_an_oversized_row_without_reading_it(tmp_path: Path) -> None:
    """Bounded AND complete: cap the read, but keep scanning past the long row.

    One enormous early row used to allocate past the reader's own byte limits.
    Capping it then abandoned the scan, which silently skipped an experiment
    whose valid `created` row merely sat after that row. Both are wrong.
    """
    _seed_experiment(tmp_path, "huge")
    log = tmp_path / ".build-loop" / "experiments" / "huge.jsonl"
    log.write_text(
        json.dumps({"event": "noise", "blob": "x" * 400_000}) + "\n"
        + json.dumps({
            "event": "created", "artifact": "huge", "baseline_metric": "pass rate",
            "baseline_value": 0.5, "target_value": 0.8, "sample_size_target": 8,
        }) + "\n",
        encoding="utf-8",
    )
    runner = _runner()
    found = runner._head_created_row(log)
    assert found is not None and found["artifact"] == "huge"

    # The oversized row itself is never parsed: a file holding only that row
    # yields nothing rather than decoding 400 KB.
    only_huge = tmp_path / ".build-loop" / "experiments" / "onlyhuge.jsonl"
    only_huge.write_text(
        json.dumps({"event": "created", "artifact": "x", "blob": "y" * 400_000}) + "\n",
        encoding="utf-8",
    )
    assert runner._head_created_row(only_huge) is None


def test_one_row_missing_metric_source_still_runs_the_control_arm(tmp_path: Path) -> None:
    """An unlabelled legacy row is outcome-derived, not a second scale.

    Two defects lived here in sequence. `all()` downgraded the artifact to the
    level comparison and labelled outcome rows "caller-supplied". The first fix
    then classified the same sample as mixed-scale and refused to grade it --
    also wrong, and with a diagnostic that is false about a row the writer
    derived from the run outcome. The control arm must actually RUN.
    """
    _seed_experiment(tmp_path, "mixedsrc", target=0.7)
    log = tmp_path / ".build-loop" / "experiments" / "mixedsrc.jsonl"
    for index in range(8):
        _write_run_entry_cli(tmp_path, "mixedsrc", run_id=f"ms-{index}")
    rows = log.read_text().strip().splitlines()
    parsed = [json.loads(r) for r in rows]
    for row in parsed:
        if row.get("event") == "applied":
            row.pop("metric_source", None)
            break
    log.write_text("".join(json.dumps(r) + "\n" for r in parsed), encoding="utf-8")

    run_id = _write_state(tmp_path, 3)
    _seed_control_runs(tmp_path, count=12, outcome="pass")
    result = _runner().run(tmp_path, run_id=run_id, source="test")
    sweep = result["stages"]["sample_sweep"]

    # Not promoted -- the delta against an all-pass control is 0, below the
    # required 0.2 -- but graded by the CONTROL rule, not refused.
    assert sweep["eligible"] == 0, sweep
    assert sweep["metric_mismatch"] == 0, sweep
    assert sweep["control_missing"] == 0, sweep
    orders = [o for o in result["work_orders"] if o["role"] == "promotion-reviewer"]
    assert orders == [], orders


def test_a_mixed_scale_sample_is_reported_not_averaged(tmp_path: Path) -> None:
    """Averaging caller metrics with outcome scores produces a mean on no scale."""
    from write_run_entry.iohelpers import append_experiment_rows

    _seed_experiment(tmp_path, "mixedscale")
    experiments = tmp_path / ".build-loop" / "experiments"
    for index in range(6):
        _write_run_entry_cli(tmp_path, "mixedscale", run_id=f"mx2-{index}")
    for index in range(2):
        append_experiment_rows(
            experiments, f"caller-{index}", ["mixedscale"], "pass",
            "2026-09-12T00:00:00Z", metric_value=7.0,
        )

    run_id = _write_state(tmp_path, 3)
    _seed_control_runs(tmp_path)
    sweep = _runner().run(tmp_path, run_id=run_id, source="test")["stages"]["sample_sweep"]

    assert sweep["eligible"] == 0, sweep
    assert sweep["metric_mismatch"] == 1, sweep


def test_an_out_of_window_treatment_arm_does_not_promote(tmp_path: Path) -> None:
    """The 90-day bound applied only to controls, so 2020 rows could promote."""
    _seed_experiment(tmp_path, "oldtreat")
    log = tmp_path / ".build-loop" / "experiments" / "oldtreat.jsonl"
    for index in range(8):
        _write_run_entry_cli(tmp_path, "oldtreat", run_id=f"ot-{index}")
    parsed = [json.loads(r) for r in log.read_text().strip().splitlines()]
    for row in parsed:
        if row.get("event") == "applied":
            row["date"] = "2020-01-01T00:00:00Z"
    log.write_text("".join(json.dumps(r) + "\n" for r in parsed), encoding="utf-8")

    run_id = _write_state(tmp_path, 3)
    _seed_control_runs(tmp_path, count=12, outcome="partial")
    sweep = _runner().run(tmp_path, run_id=run_id, source="test")["stages"]["sample_sweep"]

    assert sweep["eligible"] == 0, sweep
    assert sweep["treatment_out_of_window"] == 8, sweep


def test_a_future_dated_control_run_does_not_count_as_recent(tmp_path: Path) -> None:
    _seed_experiment(tmp_path, "future")
    for index in range(8):
        _write_run_entry_cli(tmp_path, "future", run_id=f"fu-{index}")

    run_id = _write_state(tmp_path, 3)
    path = tmp_path / ".build-loop" / "state.json"
    state = json.loads(path.read_text())
    for index in range(12):
        state["runs"].append({
            "run_id": f"future-{index}", "date": "2099-01-01T00:00:00Z",
            "goal": "c", "outcome": "partial", "host": "test", "commit": "pending",
            "phases": {}, "manualInterventions": [], "diagnosticCommands": [],
            "filesTouched": [], "judge_decisions": [], "security_findings": [],
            "active_experimental_artifacts": [],
        })
    path.write_text(json.dumps(state), encoding="utf-8")

    sweep = _runner().run(tmp_path, run_id=run_id, source="test")["stages"]["sample_sweep"]
    assert sweep["control_missing"] == 1, sweep


def test_an_out_of_window_row_is_not_also_counted_as_missing_a_metric(tmp_path: Path) -> None:
    """`metric_missing` said those rows carry no numeric metric. They do.

    Computing `unmeasured` after the window filter counted every measured-but-old
    row a second time, under a reason that is false about it.
    """
    _seed_experiment(tmp_path, "double")
    log = tmp_path / ".build-loop" / "experiments" / "double.jsonl"
    for index in range(8):
        _write_run_entry_cli(tmp_path, "double", run_id=f"db-{index}")
    parsed = [json.loads(r) for r in log.read_text().strip().splitlines()]
    for row in parsed:
        if row.get("event") == "applied":
            row["date"] = "2020-01-01T00:00:00Z"
    log.write_text("".join(json.dumps(r) + "\n" for r in parsed), encoding="utf-8")

    run_id = _write_state(tmp_path, 3)
    _seed_control_runs(tmp_path, count=12, outcome="partial")
    sweep = _runner().run(tmp_path, run_id=run_id, source="test")["stages"]["sample_sweep"]

    assert sweep["treatment_out_of_window"] == 8, sweep
    assert sweep["metric_missing"] == 0, sweep


def test_the_head_scan_drain_consumes_the_line_budget(tmp_path: Path) -> None:
    """An unbounded drain turned a head-bounded scan into a full-file read."""
    _seed_experiment(tmp_path, "drain")
    log = tmp_path / ".build-loop" / "experiments" / "drain.jsonl"
    log.write_text(
        json.dumps({"event": "noise", "blob": "x" * 400_000}) + "\n"
        + json.dumps({
            "event": "created", "artifact": "drain", "baseline_metric": "pass rate",
            "baseline_value": 0.5, "target_value": 0.8, "sample_size_target": 8,
        }) + "\n",
        encoding="utf-8",
    )
    runner = _runner()
    # A budget of 2 is spent by the oversized row's own chunks, so the created
    # row beyond it is not reached -- the scan stays bounded rather than
    # reading on until EOF.
    assert runner._head_created_row(log, max_lines=2) is None
    # With a real budget the same file resolves.
    assert runner._head_created_row(log, max_lines=500)["artifact"] == "drain"


def test_retro_skip_counters_reach_the_learn_envelope(tmp_path: Path) -> None:
    """A drop nobody reports looks identical to an empty queue.

    `enforce_retro_signals.scan` silently drops two classes of candidate —
    already dispositioned, and naming no gate. Both counts are now carried into
    the Learn details block, so Phase 6 can say how many candidates it declined
    to count rather than implying the queue was empty.
    """
    run_id = _write_state(tmp_path, 3)
    runner = _runner()
    d = tmp_path / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)

    def _candidate(text: str, *, disposed: bool = False) -> str:
        box = "- [x] Adopt" if disposed else "- [ ] Adopt"
        return f"# Enforce candidate\n\n## Candidate\n\n{text}\n\n## Disposition\n\n{box}\n"

    # Two run-ids naming a judge (no gate), one already dispositioned.
    (d / "run-aaa-01.md").write_text(
        _candidate("Enforce gate: inline-self-verification (failed this run)"), encoding="utf-8")
    (d / "run-bbb-01.md").write_text(
        _candidate("Enforce gate: inline-self-verification (failed this run)"), encoding="utf-8")
    (d / "run-ccc-01.md").write_text(
        _candidate("Enforce gate: review-g (failed this run)", disposed=True), encoding="utf-8")

    patterns, count, skipped = runner._retro_patterns(tmp_path)

    assert patterns == [] and count == 0
    assert skipped == {
        "retro_dispositioned_skipped": 1,
        "retro_placeholder_skipped": 2,
    }

    result = runner.run(tmp_path, run_id=run_id, source="test")
    details = result["stages"]["collect"]
    assert details["retro_placeholder_skipped"] == 2
    assert details["retro_dispositioned_skipped"] == 1
