# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

import dashboard_projection as projection
import working_state_writer as working_state


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_active_run_projects_one_current_phase_tasks_and_invoked_agents(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": True,
        "phase": "execute",
        "intent": {"restated_intent": "Make the loop visible."},
        "execution": {
            "build_loop_id": "run-current",
            "phase": "execute",
            "queued_chunks": ["c1", {"chunk_id": "c2", "title": "Render dashboard"}],
            "in_flight_chunks": [{"chunk_id": "c1", "title": "Build projection", "owner": "implementer"}],
            "completed_chunks": [{"chunk_id": "c0", "title": "Assess sources", "status": "fixed"}],
        },
    })
    ledger = tmp_path / ".build-loop/agent-ledger.jsonl"
    ledger.write_text("\n".join((
        json.dumps({"ts": "2026-08-28T20:00:00Z", "run_id": "older", "phase": "execute", "agent": "old-agent", "action": "execute"}),
        json.dumps({"ts": "2026-08-28T21:00:00Z", "run_id": "run-current", "phase": "execute", "agent": "frontend-implementer", "action": "execute", "tier": "code", "model": "gpt", "status": "pass"}),
        json.dumps({"ts": "2026-08-28T21:01:00Z", "run_id": "run-current", "phase": "review", "agent": "independent-auditor", "action": "verify", "status": "partial"}),
    )) + "\n", encoding="utf-8")

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "active"
    assert result["current_phase"] == "execute"
    assert [phase["status"] for phase in result["phases"]] == ["complete", "complete", "active", "pending", "pending", "pending"]
    assert [phase["output"] for phase in result["phases"]] == [
        "State summary and goal", "Ordered task plan", "Working implementation",
        "Scorecard and evidence", "Resolved review findings", "Learning outcome",
    ]
    assert {task["id"]: task["status"] for task in result["tasks"]} == {"c1": "active", "c2": "queued", "c0": "complete"}
    assert [agent["name"] for agent in result["agents"]] == ["independent-auditor", "frontend-implementer"]
    assert result["agents"][0]["source"] == "agent-ledger"
    assert result["metrics"] == {
        "phases_complete": 2,
        "phases_total": 6,
        "tasks_complete": 1,
        "tasks_active": 1,
        "tasks_blocked": 0,
        "tasks_total": 3,
        "agents_invoked": 2,
        "work_orders_pending": 0,
    }


def test_current_run_notes_use_bounded_working_state_and_exclude_other_runs(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": True,
        "execution": {"build_loop_id": "run-current", "phase": "execute"},
    })
    working = tmp_path / ".build-loop/working-state"
    working.mkdir(parents=True)
    working.joinpath("log.jsonl").write_text("\n".join((
        json.dumps({"t": "2026-08-28T20:00:00Z", "run": "older", "agent": "old", "note": "Do not show"}),
        json.dumps({"t": "2026-08-28T21:00:00Z", "run": "run-current", "agent": "implementer", "phase": "execute", "note": "Projection connected."}),
    )) + "\n", encoding="utf-8")
    _write_json(working / "current.json", {
        "updated_at": "2026-08-28T21:01:00Z",
        "run_id": "run-current",
        "agent": "reviewer",
        "phase": "review",
        "comment": "Checking the live result.",
    })

    result = projection.build_run_projection(tmp_path)

    assert [(item["text"], item["phase"]) for item in result["notes"]] == [
        ("Checking the live result.", "review"),
        ("Projection connected.", "execute"),
    ]
    assert all(item["text"] != "Do not show" for item in result["notes"])


def test_working_state_writer_keeps_note_and_run_identity_in_log_row() -> None:
    class Args:
        agent = "implementer"
        run_id = "run-1"
        phase = "execute"
        chunk_id = "c1"
        current_task_id = None
        current_task_summary = None
        current_file = None
        current_file_line_range = None
        next_task_id = None
        next_task_summary = None
        status = "editing"
        elapsed_in_chunk_s = None
        blocked_reason = None
        note = "A free-form progress comment."

    state = working_state.build_state(Args())
    row = working_state.build_log_row(state)

    assert state["note"] == "A free-form progress comment."
    assert row["run"] == "run-1"
    assert row["phase"] == "execute"
    assert row["note"] == "A free-form progress comment."


def test_task_status_precedence_prevents_conflicting_duplicates(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": True,
        "execution": {
            "build_loop_id": "run-1",
            "phase": "iterate",
            "queued_chunks": ["same"],
            "in_flight_chunks": [{"chunk_id": "same", "title": "Fix dashboard"}],
            "completed_chunks": [{"chunk_id": "same", "title": "Fix dashboard", "status": "fixed"}],
        },
    })

    result = projection.build_run_projection(tmp_path)

    assert result["tasks"] == [{"id": "same", "title": "Fix dashboard", "status": "complete", "owner": ""}]


def test_plan_headings_are_a_labeled_fallback_when_execution_has_no_tasks(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": True,
        "phase": "plan",
        "execution": {"build_loop_id": "run-1", "phase": "plan"},
    })
    plan = tmp_path / ".build-loop/plan.md"
    plan.write_text("# Plan\n\n### C1 - Build projection\n\n### C2 — Render dashboard\n", encoding="utf-8")

    result = projection.build_run_projection(tmp_path)

    assert [task["id"] for task in result["tasks"]] == ["C1", "C2"]
    assert all(task["status"] == "pending" for task in result["tasks"])
    assert any("plan headings" in warning for warning in result["warnings"])


def test_plan_headings_enrich_bare_canonical_queue_ids(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": True,
        "execution": {
            "build_loop_id": "run-1",
            "phase": "execute",
            "queued_chunks": ["c2"],
            "in_flight_chunks": ["c1"],
        },
    })
    plan = tmp_path / ".build-loop/plan.md"
    plan.write_text("# Plan\n\n### C1 - Build projection\n\n### C2 - Render dashboard\n", encoding="utf-8")

    result = projection.build_run_projection(tmp_path)

    assert [(task["id"], task["title"]) for task in result["tasks"]] == [
        ("c2", "Render dashboard"),
        ("c1", "Build projection"),
    ]
    assert not any("fallback" in warning for warning in result["warnings"])


def test_completed_run_uses_run_judges_without_claiming_an_active_phase(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "runs": [{
            "run_id": "done-1",
            "goal": "Finish the dashboard.",
            "outcome": "pass",
            "judge_decisions": [{"judge_id": "independent-auditor", "verdict": "yay", "verdict_ts": "2026-08-28T21:00:00Z"}],
        }],
    })

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "complete"
    assert result["current_phase"] is None
    assert all(phase["status"] == "complete" for phase in result["phases"])
    assert result["agents"][0]["name"] == "independent-auditor"
    assert result["agents"][0]["source"] == "run judge record"


def test_completed_run_does_not_show_plan_tasks_as_pending(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "runs": [{"run_id": "done-1", "goal": "Finish dashboard.", "outcome": "pass"}],
    })
    plan = tmp_path / ".build-loop/plan.md"
    plan.write_text("# Plan\n\n### C1 - Build projection\n", encoding="utf-8")

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "complete"
    assert result["tasks"] == []


def test_completed_run_preserves_recorded_major_tasks(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "runs": [{
            "run_id": "done-1",
            "goal": "Finish dashboard.",
            "outcome": "pass",
            "tasks": [
                {"id": "C1", "title": "Build projection"},
                {"id": "C2", "title": "Review dashboard", "status": "complete"},
            ],
        }],
    })

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "complete"
    assert [(task["id"], task["status"]) for task in result["tasks"]] == [
        ("C1", "complete"),
        ("C2", "complete"),
    ]


def test_pending_learn_receipt_projects_phase_agents_and_comments(tmp_path: Path) -> None:
    receipt_path = tmp_path / ".build-loop/learn/run-learn.json"
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "runs": [{
            "run_id": "run-learn",
            "goal": "Finish Learn consistently.",
            "outcome": "pass",
            "learn": {
                "status": "awaiting_agents",
                "receipt": ".build-loop/learn/run-learn.json",
            },
        }],
    })
    _write_json(receipt_path, {
        "schema": "build-loop.learn-receipt.v1",
        "run_id": "run-learn",
        "status": "awaiting_agents",
        "created_at": "2026-08-30T01:00:00Z",
        "work_orders": [
            {"role": "self-improvement-architect", "status": "pending", "source": "procedural"},
            {"role": "promotion-reviewer", "status": "complete", "source": "sample-sweep", "attested_at": "2026-08-30T01:02:00Z"},
        ],
        "comments": [{"at": "2026-08-30T01:01:00Z", "source": "manual", "text": "Review the recurring retry."}],
    })

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "active"
    assert result["current_phase"] == "learn"
    assert [phase["status"] for phase in result["phases"]] == [
        "complete", "complete", "complete", "complete", "complete", "active",
    ]
    assert [(agent["name"], agent["status"], agent["phase"]) for agent in result["agents"]] == [
        ("promotion-reviewer", "complete", "learn"),
    ]
    assert result["metrics"]["agents_invoked"] == 1
    assert [(order["role"], order["status"]) for order in result["work_orders"]] == [
        ("self-improvement-architect", "pending"),
        ("promotion-reviewer", "complete"),
    ]
    assert result["metrics"]["work_orders_pending"] == 1
    assert result["notes"][0]["text"] == "Review the recurring retry."
    assert result["notes"][0]["source"] == "Learn receipt"
    assert ".build-loop/learn/run-learn.json" in result["sources"]


def test_invalid_learn_receipt_does_not_claim_agent_activity(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "runs": [{
            "run_id": "run-learn",
            "goal": "Finish Learn consistently.",
            "outcome": "pass",
            "learn": {"status": "complete", "receipt": ".build-loop/learn/run-learn.json"},
        }],
    })
    _write_json(tmp_path / ".build-loop/learn/run-learn.json", {
        "schema": "wrong",
        "run_id": "run-learn",
        "work_orders": [{"role": "invented-agent", "status": "complete"}],
    })

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "complete"
    assert result["agents"] == []
    assert any("invalid Learn receipt" in warning for warning in result["warnings"])


def test_failed_run_identifies_the_blocked_phase(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "runs": [{
            "run_id": "failed-1",
            "goal": "Finish dashboard.",
            "outcome": "fail",
            "phases": {"4": {"status": "fail"}},
        }],
    })

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "blocked"
    assert result["current_phase"] == "review"
    assert result["phases"][3]["status"] == "blocked"


def test_partial_run_identifies_the_incomplete_phase(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "runs": [{
            "run_id": "partial-1",
            "goal": "Finish dashboard.",
            "outcome": "partial",
            "phases": {"execute": {"status": "partial"}},
        }],
    })

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "blocked"
    assert result["current_phase"] == "execute"
    assert result["phases"][2]["status"] == "blocked"


def test_idle_view_does_not_claim_historical_ledger_agents(tmp_path: Path) -> None:
    ledger = tmp_path / ".build-loop/agent-ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({
        "run_id": "old-run",
        "agent": "old-agent",
        "action": "execute",
    }) + "\n", encoding="utf-8")

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "idle"
    assert result["agents"] == []


def test_missing_and_malformed_sources_degrade_to_an_honest_idle_view(tmp_path: Path) -> None:
    state = tmp_path / ".build-loop/state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{broken", encoding="utf-8")
    ledger = tmp_path / ".build-loop/agent-ledger.jsonl"
    ledger.write_text("not-json\n", encoding="utf-8")

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "idle"
    assert result["run_id"] is None
    assert result["current_phase"] is None
    assert len(result["phases"]) == 6
    assert result["tasks"] == []
    assert result["agents"] == []
    assert any("malformed JSON" in warning for warning in result["warnings"])
    assert any("malformed agent ledger" in warning for warning in result["warnings"])
