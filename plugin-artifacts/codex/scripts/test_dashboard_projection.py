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
    assert [phase["location"] for phase in result["phases"]] == [
        ".build-loop/goal.md", ".build-loop/plan.md", ".build-loop/state.json",
        ".build-loop/evals/", ".build-loop/issues/",
        ".build-loop/learn/run-current.json",
    ]
    assert {task["id"]: task["status"] for task in result["tasks"]} == {"c1": "active", "c2": "queued", "c0": "complete"}
    assert {task["id"]: task["phase"] for task in result["tasks"]} == {"c1": "execute", "c2": "execute", "c0": "execute"}
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
        "judges_used": 0,
        "models_used": 1,
        "work_orders_pending": 0,
        "open_work_total": 2,
        "open_work_queued": 2,
        "open_work_deferred": 0,
    }


def test_open_work_projects_canonical_queues_with_refresh_metadata(tmp_path: Path, monkeypatch) -> None:
    projection._OPEN_WORK_CACHE.clear()
    monkeypatch.setattr(projection, "collect_task_surface", lambda **_kwargs: {
        "open_count": 3,
        "execution_queue_count": 2,
        "deferred_count": 1,
        "counts_by_surface": {"queue": 1, "operations_center": 1, "backlog": 1},
        "items": [
            {"id": "local", "title": "Local queue", "surface": "queue", "lifecycle": "queued", "execution_eligible": True},
            {"id": "shared", "title": "Shared queue", "surface": "operations_center", "lifecycle": "review", "execution_eligible": True},
            {"id": "later", "title": "Deferred work", "surface": "backlog", "lifecycle": "deferred", "execution_eligible": False},
        ],
        "truncated": False,
        "operations_center": {"status": "available", "matched_count": 1},
    })

    result = projection.build_run_projection(tmp_path)

    assert result["schema_version"] == "1.3"
    assert result["open_work"]["open_count"] == 3
    assert result["open_work"]["refresh_interval_seconds"] == 30
    assert result["open_work"]["refreshed_at"]
    assert result["metrics"]["open_work_queued"] == 2
    assert result["metrics"]["open_work_deferred"] == 1


def test_workspace_projects_multiple_active_worktrees_without_duplicate_ledgers(tmp_path: Path, monkeypatch) -> None:
    projection._OPEN_WORK_CACHE.clear()
    peer = tmp_path / ".build-loop/worktrees/run-peer"
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": True,
        "execution": {"build_loop_id": "run-root", "phase": "plan"},
    })
    _write_json(peer / ".build-loop/state.json", {
        "active": True,
        "execution": {"build_loop_id": "run-peer", "phase": "execute"},
    })
    (tmp_path / ".build-loop/agent-ledger.jsonl").write_text(json.dumps({
        "run_id": "run-root", "phase": "plan", "agent": "root-planner", "action": "author", "model": "root-model",
    }) + "\n", encoding="utf-8")
    (peer / ".build-loop/agent-ledger.jsonl").write_text(json.dumps({
        "run_id": "run-peer", "phase": "execute", "agent": "peer-builder", "action": "execute", "model": "peer-model",
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(projection, "_git_worktree_paths", lambda _root, _warnings: [tmp_path, peer])
    monkeypatch.setattr(projection, "collect_task_surface", lambda **_kwargs: {
        "open_count": 0,
        "execution_queue_count": 0,
        "deferred_count": 0,
        "counts_by_surface": {},
        "items": [],
        "truncated": False,
        "operations_center": {"status": "not_requested"},
    })

    result = projection.build_run_projection(tmp_path)

    assert [(loop["run_id"], loop["scope"], loop["current_phase"]) for loop in result["workspace"]["active_loops"]] == [
        ("run-root", "Repository", "plan"),
        ("run-peer", ".build-loop/worktrees/run-peer", "execute"),
    ]
    assert all("open_work" not in loop for loop in result["workspace"]["active_loops"])
    assert [[(agent["name"], agent["model"]) for agent in loop["agents"]] for loop in result["workspace"]["active_loops"]] == [
        [("root-planner", "root-model")],
        [("peer-builder", "peer-model")],
    ]


def test_agent_ledger_keeps_phase_identity_model_and_per_row_judge_status(tmp_path: Path) -> None:
    rows = [
        {"run_id": "run", "phase": "plan", "agent": "shared", "action": "author", "model": "plan-model", "ts": "1"},
        {"run_id": "run", "phase": "plan", "agent": "shared", "action": "author", "model": "", "ts": "2"},
        {"run_id": "run", "phase": "review", "agent": "shared", "action": "gate", "model": "judge-model", "ts": "3"},
        {"run_id": "run", "phase": "execute", "agent": "builder", "action": "execute", "model": "build-model", "ts": "4"},
    ]

    agents = projection._agents_from_ledger(reversed(rows), "run")
    by_identity = {(item["name"], item["phase"]): item for item in agents}

    assert set(by_identity) == {("shared", "plan"), ("shared", "review"), ("builder", "execute")}
    assert by_identity[("shared", "plan")]["model"] == "plan-model"
    assert by_identity[("shared", "plan")]["judge"] is False
    assert by_identity[("shared", "review")]["judge"] is True
    assert by_identity[("builder", "execute")]["judge"] is False


def test_judge_record_enriches_ledger_agent_and_preserves_recorded_model(tmp_path: Path, monkeypatch) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": True,
        "execution": {"build_loop_id": "run-judge", "phase": "review"},
        "runs": [{
            "run_id": "run-judge",
            "goal": "Review the dashboard.",
            "judge_decisions": [{
                "judge_id": "independent-auditor",
                "verdict": "yay",
                "model": "gpt-5.6-sol",
            }],
        }],
    })
    ledger = tmp_path / ".build-loop/agent-ledger.jsonl"
    ledger.write_text(json.dumps({
        "run_id": "run-judge",
        "phase": "review",
        "agent": "independent-auditor",
        "action": "verify",
        "status": "active",
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(projection, "_git_worktree_paths", lambda _root, _warnings: [tmp_path])

    result = projection.build_run_projection(tmp_path)

    assert len(result["agents"]) == 1
    assert result["agents"][0]["judge"] is True
    assert result["agents"][0]["model"] == "gpt-5.6-sol"
    assert "run judge record" in result["agents"][0]["source"]
    assert result["metrics"]["judges_used"] == 1
    assert result["metrics"]["models_used"] == 1


def test_workspace_projects_handoffs_and_prior_run_evidence(tmp_path: Path, monkeypatch) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "runs": [{
            "run_id": "done-1",
            "goal": "Ship the dashboard.",
            "outcome": "pass",
            "date": "2026-08-30T12:00:00Z",
            "commit": "abc123",
            "judge_decisions": [{
                "judge_id": "review-judge",
                "verdict": "nay",
                "judge_model": "claude-opus",
                "target": "dashboard",
                "verdict_ts": "2026-08-30T12:05:00Z",
            }],
        }],
    })
    handoff = tmp_path / "docs/handoff/2026-08-30-dashboard.md"
    handoff.parent.mkdir(parents=True)
    handoff.write_text(
        "# Dashboard handoff\n\n**Date:** 2026-08-30\n**From:** Codex\n**To:** Claude Code\n**Status:** complete\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(projection, "_git_worktree_paths", lambda _root, _warnings: [tmp_path])

    result = projection.build_run_projection(tmp_path)

    assert result["workspace"]["handoffs"] == [{
        "id": "docs:handoff:2026-08-30-dashboard.md",
        "title": "Dashboard handoff",
        "date": "2026-08-30",
        "participants": ["Codex", "Claude Code"],
        "status": "complete",
        "status_detail": "complete",
        "path": "docs/handoff/2026-08-30-dashboard.md",
    }]
    assert result["workspace"]["history"] == [{
        "run_id": "done-1",
        "goal": "Ship the dashboard.",
        "status": "complete",
        "date": "2026-08-30T12:00:00Z",
        "commit": "abc123",
        "host": "",
        "judges": ["review-judge"],
        "judge_records": [{
            "name": "review-judge",
            "verdict": "nay",
            "status": "blocked",
            "target": "dashboard",
            "timestamp": "2026-08-30T12:05:00Z",
            "model": "claude-opus",
        }],
        "judge_used": True,
        "models": ["claude-opus"],
    }]


def test_handoffs_cannot_follow_symlinks_outside_repository(tmp_path: Path, monkeypatch) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {"active": False, "execution": {}, "runs": []})
    external = tmp_path.parent / f"{tmp_path.name}-external-handoff.md"
    external.write_text("# Private handoff\n\n**From:** Outside\n", encoding="utf-8")
    docs_link = tmp_path / "docs/handoff/leak.md"
    coordination_link = tmp_path / ".build-loop/coordination/team-handoff.md"
    docs_link.parent.mkdir(parents=True)
    coordination_link.parent.mkdir(parents=True)
    docs_link.symlink_to(external)
    coordination_link.symlink_to(external)
    monkeypatch.setattr(projection, "_git_worktree_paths", lambda _root, _warnings: [tmp_path])

    result = projection.build_run_projection(tmp_path)

    assert result["workspace"]["handoffs"] == []
    assert any("outside the repository" in warning for warning in result["warnings"])


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

    assert result["tasks"] == [{"id": "same", "title": "Fix dashboard", "status": "complete", "owner": "", "phase": "execute"}]


def test_iteration_tasks_are_assigned_to_the_iterate_phase(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": True,
        "execution": {
            "build_loop_id": "run-1",
            "phase": "iterate",
            "item_iterations": {
                "finding-1": [{"status": "active", "summary": "Fix review finding"}],
            },
        },
    })

    result = projection.build_run_projection(tmp_path)

    assert result["tasks"] == [{
        "id": "finding-1",
        "title": "Fix review finding",
        "status": "active",
        "owner": "",
        "phase": "iterate",
    }]


def test_later_execute_record_does_not_move_iteration_task_between_phases(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": True,
        "execution": {
            "build_loop_id": "run-1",
            "phase": "iterate",
            "item_iterations": {
                "finding-1": [{"status": "active", "summary": "Fix review finding"}],
            },
            "tasks": [{"id": "finding-1", "status": "complete"}],
        },
    })

    result = projection.build_run_projection(tmp_path)

    assert result["tasks"][0]["phase"] == "iterate"


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
    assert all(task["phase"] == "execute" for task in result["tasks"])
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


def test_hook_only_receipt_does_not_replace_latest_orchestrator_run(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "runs": [
            {"run_id": "done-1", "goal": "Finish dashboard.", "outcome": "pass"},
            {
                "run_id": "hook-1",
                "goal": "(hook-only commit; no orchestrator run)",
                "outcome": "partial",
                "judge_decisions": [{"judge_id": "independent-auditor-hook", "verdict": "pending"}],
            },
        ],
    })

    result = projection.build_run_projection(tmp_path)

    assert result["run_id"] == "done-1"
    assert result["status"] == "complete"
    assert result["agents"] == []


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


def test_awaiting_learn_agents_overrides_stale_pending_phase_status(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "runs": [{
            "run_id": "run-learn",
            "goal": "Finish Learn consistently.",
            "outcome": "pass",
            "phases": {"learn": {"status": "pending"}},
            "learn": {
                "status": "awaiting_agents",
                "receipt": ".build-loop/learn/run-learn.json",
            },
        }],
    })

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "active"
    assert result["current_phase"] == "learn"
    assert result["phases"][-1]["status"] == "active"


def test_awaiting_learn_agents_does_not_reopen_completed_learn_phase(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "runs": [{
            "run_id": "run-learn",
            "goal": "Finish Learn consistently.",
            "outcome": "pass",
            "phases": {"learn": {"status": "complete"}},
            "learn": {"status": "awaiting_agents"},
        }],
    })

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "complete"
    assert result["current_phase"] is None
    assert result["phases"][-1]["status"] == "complete"


def test_awaiting_learn_agents_preserves_blocked_learn_phase(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "runs": [{
            "run_id": "run-learn",
            "goal": "Finish Learn consistently.",
            "outcome": "pass",
            "phases": {"learn": {"status": "blocked"}},
            "learn": {"status": "awaiting_agents"},
        }],
    })

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "blocked"
    assert result["current_phase"] == "learn"
    assert result["phases"][-1]["status"] == "blocked"


def test_top_level_completed_learn_phase_is_not_reopened(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "phases": {"learn": {"status": "complete"}},
        "runs": [{
            "run_id": "run-learn",
            "goal": "Finish Learn consistently.",
            "outcome": "pass",
            "learn": {"status": "awaiting_agents"},
        }],
    })

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "complete"
    assert result["current_phase"] is None
    assert result["phases"][-1]["status"] == "complete"


def test_top_level_blocked_learn_phase_remains_the_current_blocker(tmp_path: Path) -> None:
    _write_json(tmp_path / ".build-loop/state.json", {
        "active": False,
        "execution": {},
        "phases": {"learn": {"status": "blocked"}},
        "runs": [{
            "run_id": "run-learn",
            "goal": "Finish Learn consistently.",
            "outcome": "pass",
            "learn": {"status": "awaiting_agents"},
        }],
    })

    result = projection.build_run_projection(tmp_path)

    assert result["status"] == "blocked"
    assert result["current_phase"] == "learn"
    assert result["phases"][-1]["status"] == "blocked"


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
