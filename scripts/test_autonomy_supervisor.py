from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import autonomy_supervisor as supervisor


@pytest.fixture
def repo() -> Path:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0'\n", encoding="utf-8")
        yield root


def test_preflight_asks_only_for_missing_intent_anchor(repo: Path) -> None:
    result = supervisor.assess_preflight(repo, {})
    assert result["ready"] is False
    assert [question["field"] for question in result["questions"]] == ["goal"]
    assert {assumption["field"] for assumption in result["assumptions"]} == {
        "scope_roots", "validation_commands", "external_dependencies"
    }


def test_initializer_applies_flag_precedence_and_persists_outcome_policy(repo: Path) -> None:
    started = datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc)
    result = supervisor.initialize_run(
        repo, "Migrate account data", run_id="run-1", budget="30m", long=True, now=started
    )
    assert result["budget"]["mode"] == "custom"
    assert result["budget"]["deadline_at"] == "2026-08-08T01:30:00+00:00"
    assert result["budget"]["soft_target"] is True
    assert result["outcome_first"] is True
    state = json.loads((repo / supervisor.STATE_PATH).read_text(encoding="utf-8"))
    assert state["execution"]["related_issue_policy"] == "execute_related_reversible_testable"


def test_parse_duration_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="followed by"):
        supervisor.parse_duration("overnight")


def test_preflight_assumes_reversible_details_and_names_validation(repo: Path) -> None:
    result = supervisor.assess_preflight(repo, {"goal": "Refactor the parser"})
    assert result["ready"] is True
    assert result["resolved"]["validation_commands"] == ["uv run pytest -q"]
    assert all(assumption["validation"] for assumption in result["assumptions"])
    assert result["task_profile"]["task_type"] == "refactor"


def test_preflight_surfaces_consequential_choices_with_impact(repo: Path) -> None:
    result = supervisor.assess_preflight(repo, {
        "goal": "Release the migration",
        "production_action": True,
        "irreversible_action": True,
        "major_user_decision": True,
    })
    assert {question["field"] for question in result["questions"]} == {
        "production_policy", "irreversible_policy", "major_user_decision_policy"
    }
    assert all(question["why"] and question["impact"] for question in result["questions"])


def test_task_history_changes_future_profile(repo: Path) -> None:
    for index in range(2):
        supervisor.record_run(repo, {
            "run_id": f"run-{index}", "goal": "Refactor authentication modules",
            "duration_seconds": 5400 + index * 600, "related_discovered": 4,
            "related_completed": 3, "interventions": 1, "outcome": "pass",
            "telemetry": {"tool_calls": 10, "tool_errors": 2, "repeated_calls": 3},
        })
    profile = supervisor.task_profile(repo, "Refactor checkout modules")
    assert profile["basis"] == "history"
    assert profile["sample_count"] == 2
    assert profile["median_duration_seconds"] == 5700
    assert profile["supervision_recommended"] is True
    assert profile["related_completion_rate"] == 0.75
    assert profile["tool_error_rate"] == 0.2
    assert "provider and tool health" in profile["preflight_focus"]
    assert "missing evidence before retry" in profile["preflight_focus"]


def test_snapshot_is_aligned_bounded_and_persistent(repo: Path) -> None:
    supervisor.initialize_run(repo, "repair authentication workflow", run_id="run-snapshot")
    issues = repo / ".build-loop/issues"
    issues.mkdir(parents=True)
    (issues / "auth-one.md").write_text("Fix authentication token refresh", encoding="utf-8")
    (issues / "auth-two.md").write_text("Validate authentication logout", encoding="utf-8")
    (issues / "colors.md").write_text("Change marketing colors", encoding="utf-8")
    backlog = repo / ".build-loop/backlog/items"
    backlog.mkdir(parents=True)
    (backlog / "auth-three.md").write_text("Repair authentication callback", encoding="utf-8")
    result = supervisor.snapshot_queue(repo, "repair authentication workflow", limit=1)
    assert len(result["selected"]) == 1
    assert result["deferred_aligned_count"] == 2
    assert len(result["selected"][0]["content_sha256"]) == 64
    assert result["later_arrivals_policy"] == "next_manifest"
    persisted = json.loads((repo / supervisor.MANIFEST_PATH).read_text(encoding="utf-8"))
    assert persisted == result
    assert result["lease_owner"] == "run-snapshot"
    assert result["resume_key"] == "run-snapshot"
    assert result["lease_expires_at"]
    run_manifest = repo / result["run_manifest_path"]
    assert json.loads(run_manifest.read_text(encoding="utf-8")) == result


def test_adaptive_manifest_uses_task_shape_and_available_work(repo: Path) -> None:
    pressured = {"thermal_state": "serious", "load_ratio": 0.95, "memory_percent": 90}
    stable = {"thermal_state": "nominal", "load_ratio": 0.7, "memory_percent": 70}
    cool = {"thermal_state": "nominal", "load_ratio": 0.2, "memory_percent": 40}
    assert supervisor.resolve_queue_limit(repo, "Document the API", available_count=20, signals=stable) == 4
    assert supervisor.resolve_queue_limit(repo, "Clean up queued issues", available_count=20, signals=pressured) == 12
    assert supervisor.resolve_queue_limit(repo, "Clean up queued issues", available_count=40, signals=cool) == 30


def test_related_issue_routes_are_mece() -> None:
    executable = supervisor.classify_related_issue({
        "intent_aligned": True, "inside_repo": True, "reversible": True,
        "validation_available": True,
    })
    followup = supervisor.classify_related_issue({
        "intent_aligned": False, "inside_repo": True, "reversible": True,
        "validation_available": True,
    })
    decision = supervisor.classify_related_issue({
        "intent_aligned": True, "inside_repo": True, "reversible": False,
        "validation_available": True, "irreversible": True,
    })
    assert [executable["route"], followup["route"], decision["route"]] == ["execute", "followup", "decision"]
    assert [executable["next_action"], followup["next_action"], decision["next_action"]] == [
        "execute_and_validate", "persist_followup", "request_decision"
    ]


def _verdict(repo: Path, verdict: str = "failed", **kwargs: object) -> dict:
    return supervisor.record_verdict(
        repo, "issue:one", verdict,
        actor_id="worker-1", actor_session="worker-session-1", **kwargs,
    )


def test_third_identical_verdict_requires_audit_and_fifth_quarantines(repo: Path) -> None:
    assert _verdict(repo)["action"] == "continue"
    assert _verdict(repo)["same_verdict_count"] == 2
    third = _verdict(repo)
    assert third["action"] == "independent_audit"
    blocked_retry = _verdict(repo)
    assert blocked_retry["same_verdict_count"] == 3
    supervisor.initialize_run(repo, "repair issue one", run_id="run-audit")
    ledger = repo / ".build-loop/agent-ledger.jsonl"
    ledger.write_text(json.dumps({
        "run_id": "run-audit", "agent": "auditor-2", "action": "verify",
        "refs": {"item_id": "issue:one", "session_id": "auditor-session-2"},
    }) + "\n", encoding="utf-8")
    audit = supervisor.record_independent_audit(
        repo, "issue:one", "auditor: root cause remains",
        auditor_id="auditor-2", auditor_session="auditor-session-2",
    )
    assert audit["audit_receipt"]["auditor_id"] == "auditor-2"
    assert audit["audit_receipt"]["ledger_receipt_sha256"]
    assert audit["audit_required"] is False
    assert _verdict(repo)["same_verdict_count"] == 4
    fifth = _verdict(repo)
    assert fifth["action"] == "quarantine"


def test_resolved_verdict_clears_repeat_counter(repo: Path) -> None:
    _verdict(repo)
    resolved = _verdict(repo, "pass", resolved=True)
    assert resolved["action"] == "resolved"
    assert resolved["same_verdict_count"] == 0


def test_audit_rejects_worker_identity_and_policy_override(repo: Path) -> None:
    _verdict(repo)
    _verdict(repo)
    _verdict(repo)
    with pytest.raises(ValueError, match="must differ"):
        supervisor.record_independent_audit(
            repo, "issue:one", "same worker self-reviewed",
            auditor_id="worker-1", auditor_session="worker-session-1",
        )
    with pytest.raises(ValueError, match="fixed"):
        _verdict(repo, limit=6)


def test_snapshot_rejects_unbounded_limit(repo: Path) -> None:
    with pytest.raises(ValueError, match="between 1 and 150"):
        supervisor.snapshot_queue(repo, "goal", limit=151)


@pytest.mark.parametrize(
    ("signals", "action", "next_concurrency"),
    [
        ({"current_concurrency": 4, "provider_429s": 3}, "reduce_concurrency", 2),
        ({"current_concurrency": 4, "memory_percent": 96}, "pause_new_work", 0),
        ({"current_concurrency": 4, "load_ratio": 0.95}, "reduce_concurrency", 2),
        ({"current_concurrency": 4, "latency_p95_ms": 2200, "latency_baseline_ms": 1000}, "reduce_concurrency", 2),
        ({"current_concurrency": 2, "max_concurrency": 4, "stable_windows": 2}, "recover_one", 3),
        ({"current_concurrency": 2}, "steady", 2),
    ],
)
def test_backpressure_routes_live_signals(signals: dict, action: str, next_concurrency: int) -> None:
    result = supervisor.backpressure_action(signals)
    assert result["action"] == action
    assert result["next_concurrency"] == next_concurrency


def test_backpressure_stops_at_cost_ceiling() -> None:
    result = supervisor.backpressure_action({
        "current_concurrency": 3, "cost_used": 10, "cost_ceiling": 10,
    })
    assert result["action"] == "pause_new_work"
    assert result["cost_ratio"] == 1.0


def test_backpressure_clamps_existing_workers_to_binding_maximum() -> None:
    result = supervisor.backpressure_action({"current_concurrency": 10, "max_concurrency": 3})
    assert result["action"] == "reduce_concurrency"
    assert result["next_concurrency"] == 3


def test_backpressure_does_not_admit_first_worker_under_pressure() -> None:
    result = supervisor.backpressure_action({
        "current_concurrency": 0, "max_concurrency": 10, "provider_429s": 2,
    })
    assert result["action"] == "pause_new_work"
    assert result["next_concurrency"] == 0


def test_supervisor_selects_initial_fanout_from_independent_work_and_capacity(repo: Path) -> None:
    result = supervisor.select_fanout(repo, {
        "independent_items": 20,
        "execution_location": "cloud",
        "model_size": "small",
        "token_budget": 40_000,
        "signals": {
            "current_concurrency": 0, "load_ratio": 0, "memory_percent": 20,
            "disk_free_gb": 100, "thermal_state": "nominal",
        },
    })
    assert result["decision_owner"] == "autonomy_supervisor"
    assert result["capacity"]["effective_max"] == 5
    assert result["admission"]["next_concurrency"] == 4


def test_supervisor_does_not_initially_admit_under_critical_thermal_pressure(repo: Path) -> None:
    result = supervisor.select_fanout(repo, {
        "independent_items": 20,
        "signals": {"current_concurrency": 0, "thermal_state": "critical"},
    })
    assert result["admission"]["action"] == "pause_new_work"
    assert result["admission"]["next_concurrency"] == 0


def test_supervisor_pauses_when_shared_ceiling_is_full(repo: Path) -> None:
    result = supervisor.select_fanout(repo, {
        "independent_items": 20,
        "shared_capacity": 150,
        "active_elsewhere": 150,
        "signals": {"current_concurrency": 0},
    })
    assert result["capacity"]["effective_max"] == 0
    assert result["admission"]["action"] == "pause_new_work"
    assert result["admission"]["reasons"] == ["shared_capacity_exhausted"]


def test_supervisor_pauses_when_no_independent_work_exists(repo: Path) -> None:
    result = supervisor.select_fanout(repo, {
        "independent_items": 0,
        "signals": {"current_concurrency": 0},
    })
    assert result["capacity"]["effective_max"] == 0
    assert result["admission"]["action"] == "pause_new_work"
    assert result["admission"]["reasons"] == ["no_independent_work"]


def test_trace_pressure_feeds_admission_and_stability_persists(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter([
        "2026-08-10T00:00:00+00:00",
        "2026-08-10T00:00:31+00:00",
        "2026-08-10T00:01:02+00:00",
    ])
    monkeypatch.setattr(supervisor, "_now", lambda: next(times))
    monkeypatch.setattr(supervisor, "host_signals", lambda _repo: {
        "load_ratio": 0.2, "memory_percent": 30, "disk_free_gb": 100,
        "thermal_state": "nominal",
    })
    monkeypatch.setattr(supervisor, "summarize_tool_traces", lambda *_args, **_kwargs: {
        "provider_429s": 2, "tool_errors": 3, "p95_duration_ms": 2400,
        "repeated_calls": 2,
    })
    pressured = supervisor.select_fanout(repo, {
        "independent_items": 8, "signals": {"current_concurrency": 4},
    })
    assert pressured["admission"]["action"] == "reduce_concurrency"
    assert "repeated_provider_429" in pressured["admission"]["reasons"]

    monkeypatch.setattr(supervisor, "summarize_tool_traces", lambda *_args, **_kwargs: {
        "provider_429s": 0, "tool_errors": 0, "p95_duration_ms": 900,
        "repeated_calls": 0,
    })
    first = supervisor.select_fanout(repo, {
        "independent_items": 8, "signals": {"current_concurrency": 2},
    })
    second = supervisor.select_fanout(repo, {
        "independent_items": 8, "signals": {"current_concurrency": 2},
    })
    assert first["observed_signals"]["stable_windows"] == 1
    assert second["observed_signals"]["stable_windows"] == 2
    assert second["admission"]["action"] == "recover_one"


def test_stable_windows_require_elapsed_observation_time(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter([
        "2026-08-10T00:00:00+00:00",
        "2026-08-10T00:00:00+00:00",
        "2026-08-10T00:00:29+00:00",
        "2026-08-10T00:00:30+00:00",
    ])
    monkeypatch.setattr(supervisor, "_now", lambda: next(times))
    signals = {
        "provider_429s": 0, "error_streak": 0, "memory_percent": 20,
        "disk_free_gb": 100, "load_ratio": 0.2, "thermal_state": "nominal",
    }
    assert supervisor._update_pressure_state(repo, dict(signals))["stable_windows"] == 1
    assert supervisor._update_pressure_state(repo, dict(signals))["stable_windows"] == 1
    assert supervisor._update_pressure_state(repo, dict(signals))["stable_windows"] == 1
    assert supervisor._update_pressure_state(repo, dict(signals))["stable_windows"] == 2


def test_host_signals_measure_load_disk_memory_and_thermal(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor.os, "cpu_count", lambda: 10)
    monkeypatch.setattr(supervisor.os, "getloadavg", lambda: (5.0, 4.0, 3.0))
    monkeypatch.setattr(
        supervisor.shutil,
        "disk_usage",
        lambda _path: supervisor.shutil._ntuple_diskusage(100, 50, 20 * 1024 ** 3),
    )

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        output = (
            "No thermal warning level has been recorded\n"
            if command[0] == "pmset"
            else "System-wide memory free percentage: 72%\n"
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)
    signals = supervisor.host_signals(repo)
    assert signals == {
        "load_ratio": 0.5,
        "disk_free_gb": 20.0,
        "thermal_state": "nominal",
        "signal_source": "host_probe",
        "memory_percent": 28.0,
    }
