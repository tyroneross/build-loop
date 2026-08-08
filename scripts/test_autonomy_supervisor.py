from __future__ import annotations

import json
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
        })
    profile = supervisor.task_profile(repo, "Refactor checkout modules")
    assert profile["basis"] == "history"
    assert profile["sample_count"] == 2
    assert profile["median_duration_seconds"] == 5700
    assert profile["supervision_recommended"] is True
    assert profile["related_completion_rate"] == 0.75


def test_snapshot_is_aligned_bounded_and_persistent(repo: Path) -> None:
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


def test_third_identical_verdict_quarantines_and_changed_verdict_resets(repo: Path) -> None:
    assert supervisor.record_verdict(repo, "issue:one", "failed")["action"] == "continue"
    assert supervisor.record_verdict(repo, "issue:one", "failed")["same_verdict_count"] == 2
    third = supervisor.record_verdict(repo, "issue:one", "failed")
    assert third["action"] == "quarantine"
    changed = supervisor.record_verdict(repo, "issue:one", "partial")
    assert changed["same_verdict_count"] == 1
    assert changed["action"] == "continue"


def test_snapshot_rejects_unbounded_limit(repo: Path) -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        supervisor.snapshot_queue(repo, "goal", limit=101)


@pytest.mark.parametrize(
    ("signals", "action", "next_concurrency"),
    [
        ({"current_concurrency": 4, "provider_429s": 3}, "reduce_concurrency", 2),
        ({"current_concurrency": 4, "memory_percent": 96}, "pause_new_work", 0),
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
