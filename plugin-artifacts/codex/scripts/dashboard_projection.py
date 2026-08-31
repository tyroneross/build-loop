#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Read-only projection of Build Loop phase, task, and agent run state."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_PLAN_BYTES = 512 * 1024
MAX_LEDGER_LINES = 5_000
MAX_WORKING_STATE_LINES = 200
MAX_RUN_NOTES = 20

PHASES: tuple[dict[str, Any], ...] = (
    {"id": "assess", "number": 1, "name": "Assess", "summary": "Understand the repository and define success.", "output": "State summary and goal"},
    {"id": "plan", "number": 2, "name": "Plan", "summary": "Map the work, ownership, dependencies, and checks.", "output": "Ordered task plan"},
    {"id": "execute", "number": 3, "name": "Execute", "summary": "Build the planned change.", "output": "Working implementation"},
    {"id": "review", "number": 4, "name": "Review", "summary": "Critique, validate, fact-check, simplify, and report.", "output": "Scorecard and evidence"},
    {"id": "iterate", "number": 5, "name": "Iterate", "summary": "Fix review failures and re-check the result.", "output": "Resolved review findings"},
    {"id": "learn", "number": 6, "name": "Learn", "summary": "Capture recurring lessons and improvement candidates.", "output": "Learning outcome"},
)

PHASE_ALIASES = {
    "1": "assess",
    "assessment": "assess",
    "phase1": "assess",
    "phase_1": "assess",
    "2": "plan",
    "planning": "plan",
    "phase2": "plan",
    "phase_2": "plan",
    "3": "execute",
    "implementation": "execute",
    "build": "execute",
    "phase3": "execute",
    "phase_3": "execute",
    "4": "review",
    "critic": "review",
    "validate": "review",
    "fact_check": "review",
    "fact-check": "review",
    "simplify": "review",
    "report": "review",
    "phase4": "review",
    "phase_4": "review",
    "review_a": "review",
    "review_b": "review",
    "review_c": "review",
    "review_d": "review",
    "review_e": "review",
    "review_f": "review",
    "review_g": "review",
    "5": "iterate",
    "iteration": "iterate",
    "phase5": "iterate",
    "phase_5": "iterate",
    "6": "learn",
    "learning": "learn",
    "phase6": "learn",
    "phase_6": "learn",
}

TASK_STATUS_PRIORITY = {"pending": 0, "queued": 1, "active": 2, "blocked": 3, "complete": 4}
PLAN_TASK_RE = re.compile(
    r"^#{2,4}\s+(?:(?:Task|Chunk|Commit)\s+)?(?P<id>[A-Za-z]+\d+|\d+)\s*(?:[:\-\u2013\u2014]\s*)?(?P<title>.+)$"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _contained_file(workdir: Path, relative: str, warnings: list[str]) -> Path | None:
    candidate = workdir / relative
    try:
        resolved = candidate.resolve()
        resolved.relative_to(workdir)
    except (OSError, ValueError):
        warnings.append(f"Skipped {relative}: source resolves outside the repository.")
        return None
    return resolved if resolved.is_file() else None


def _read_text(
    workdir: Path,
    relative: str,
    warnings: list[str],
    sources: list[Path],
    *,
    max_bytes: int,
) -> str | None:
    path = _contained_file(workdir, relative, warnings)
    if path is None:
        return None
    try:
        if path.stat().st_size > max_bytes:
            warnings.append(f"Skipped {relative}: source exceeds the dashboard read limit.")
            return None
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        warnings.append(f"Could not read {relative}: {exc}.")
        return None
    sources.append(path)
    return text


def _read_json(
    workdir: Path,
    relative: str,
    warnings: list[str],
    sources: list[Path],
) -> Any:
    text = _read_text(workdir, relative, warnings, sources, max_bytes=MAX_JSON_BYTES)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        warnings.append(f"Ignored malformed JSON in {relative}.")
        return None


def _normalize_phase(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    phase = value.strip().lower().replace(" ", "_").replace("-", "_")
    phase = PHASE_ALIASES.get(phase, phase)
    return phase if phase in {item["id"] for item in PHASES} else None


def _normalize_explicit_status(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    status = value.strip().lower().replace("-", "_").replace(" ", "_")
    if status in {"pass", "passed", "done", "fixed", "complete", "completed", "success", "succeeded"}:
        return "complete"
    if status in {"active", "running", "started", "in_progress", "working"}:
        return "active"
    if status in {"fail", "failed", "blocked", "error", "rejected", "needs_input", "partial", "incomplete"}:
        return "blocked"
    if status in {"pending", "queued", "planned", "todo", "not_started"}:
        return "pending"
    return None


def _latest_run(state: dict[str, Any]) -> dict[str, Any] | None:
    runs = state.get("runs")
    if not isinstance(runs, list):
        return None
    return next((
        item for item in reversed(runs)
        if isinstance(item, dict)
        and item.get("goal") != "(hook-only commit; no orchestrator run)"
    ), None)


def _run_context(state: dict[str, Any]) -> dict[str, Any]:
    execution = state.get("execution") if isinstance(state.get("execution"), dict) else {}
    latest = _latest_run(state)
    active_run_id = execution.get("build_loop_id") or execution.get("run_id")
    active = bool(active_run_id and state.get("active", True) is not False)
    selected_run = execution if active else (latest or {})
    run_id = active_run_id or selected_run.get("run_id")
    outcome = str(selected_run.get("outcome") or "").lower()
    complete = not active and bool(latest) and outcome in {"pass", "passed", "complete", "completed", "success", "succeeded"}
    incomplete = not active and bool(latest) and outcome in {"fail", "failed", "blocked", "partial", "error", "rejected"}
    status = "active" if active else "complete" if complete else "blocked" if incomplete else "idle"
    if active and _normalize_explicit_status(execution.get("status")) == "blocked":
        status = "blocked"
    current_phase = _normalize_phase(execution.get("phase") or state.get("phase")) if active else None
    if status == "blocked" and not current_phase:
        phases = selected_run.get("phases")
        if isinstance(phases, dict):
            for phase_id, phase_record in phases.items():
                raw_status = phase_record.get("status") if isinstance(phase_record, dict) else phase_record
                if _normalize_explicit_status(raw_status) == "blocked":
                    current_phase = _normalize_phase(phase_id)
    learn = selected_run.get("learn") if isinstance(selected_run.get("learn"), dict) else {}
    learn_status = str(learn.get("status") or "").strip().lower()
    if run_id and learn_status in {"pending", "awaiting_agents"}:
        status = "active"
        current_phase = "learn"
    elif run_id and learn_status == "error":
        status = "blocked"
        current_phase = "learn"
    return {
        "execution": execution,
        "latest_run": latest,
        "selected_run": selected_run,
        "run_id": str(run_id) if run_id else None,
        "current_phase": current_phase,
        "status": status,
        "active": active,
    }


def _phase_projection(context: dict[str, Any], state: dict[str, Any], warnings: list[str]) -> list[dict[str, Any]]:
    current = context["current_phase"]
    status = context["status"]
    current_index = next((index for index, phase in enumerate(PHASES) if phase["id"] == current), None)
    explicit_sources = []
    for value in (
        state.get("phases"),
        context["execution"].get("phases"),
        context["selected_run"].get("phases"),
    ):
        if isinstance(value, dict):
            explicit_sources.append(value)

    result: list[dict[str, Any]] = []
    for index, phase in enumerate(PHASES):
        phase_status = "pending"
        if status == "complete":
            phase_status = "complete"
        elif current_index is not None:
            phase_status = "complete" if index < current_index else ("blocked" if status == "blocked" else "active") if index == current_index else "pending"
        for explicit in explicit_sources:
            raw = explicit.get(phase["id"])
            if isinstance(raw, dict):
                raw = raw.get("status") or raw.get("outcome")
            normalized = _normalize_explicit_status(raw)
            if normalized:
                phase_status = normalized
        result.append({**phase, "status": phase_status})

    if context["active"] and current_index is None:
        warnings.append("The run is active, but no recognized current phase was recorded.")
    active_count = sum(item["status"] == "active" for item in result)
    if active_count > 1:
        warnings.append("Multiple phases were marked active; the dashboard preserved the recorded states.")
    return result


def _learn_receipt(
    workdir: Path,
    context: dict[str, Any],
    warnings: list[str],
    sources: list[Path],
) -> dict[str, Any]:
    run_id = context.get("run_id")
    if not run_id:
        return {}
    selected = context.get("selected_run") if isinstance(context.get("selected_run"), dict) else {}
    summary = selected.get("learn") if isinstance(selected.get("learn"), dict) else {}
    relative = summary.get("receipt") or f".build-loop/learn/{run_id}.json"
    if not isinstance(relative, str) or not relative:
        return {}
    receipt = _read_json(workdir, relative, warnings, sources)
    if receipt is None:
        return {}
    if not isinstance(receipt, dict) or receipt.get("schema") != "build-loop.learn-receipt.v1":
        warnings.append(f"Ignored invalid Learn receipt in {relative}.")
        return {}
    if str(receipt.get("run_id") or "") != str(run_id):
        warnings.append(f"Ignored Learn receipt for another run in {relative}.")
        return {}
    return receipt


def _task_id(item: Any, fallback: str) -> str:
    if isinstance(item, dict):
        for key in ("chunk_id", "task_id", "commit_id", "id"):
            if item.get(key):
                return str(item[key])
    if isinstance(item, str) and item.strip():
        return item.strip()
    return fallback


def _task_title(item: Any, task_id: str) -> str:
    if isinstance(item, dict):
        for key in ("title", "subject", "name", "summary"):
            if item.get(key):
                return str(item[key]).strip()
    return task_id.replace("_", " ").replace("-", " ").strip().capitalize()


def _merge_task(tasks: dict[str, dict[str, Any]], item: Any, status: str, order: int) -> None:
    task_id = _task_id(item, f"task-{order + 1}")
    explicit = _normalize_explicit_status(item.get("status")) if isinstance(item, dict) else None
    normalized = explicit or status
    candidate = {
        "id": task_id,
        "title": _task_title(item, task_id),
        "status": normalized,
        "owner": str(item.get("owner") or item.get("agent") or "") if isinstance(item, dict) else "",
        "order": order,
    }
    current = tasks.get(task_id)
    if current is None or TASK_STATUS_PRIORITY.get(normalized, 0) >= TASK_STATUS_PRIORITY.get(current["status"], 0):
        if current:
            candidate["order"] = current["order"]
            candidate["title"] = current["title"] if candidate["title"] == task_id else candidate["title"]
            candidate["owner"] = candidate["owner"] or current["owner"]
        tasks[task_id] = candidate


def _structured_tasks(state: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    execution = context["execution"]
    tasks: dict[str, dict[str, Any]] = {}
    order = 0
    for key, status in (
        ("queued_chunks", "queued"),
        ("in_flight_chunks", "active"),
        ("completed_chunks", "complete"),
    ):
        values = execution.get(key)
        if isinstance(values, list):
            for item in values:
                _merge_task(tasks, item, status, order)
                order += 1

    iterations = execution.get("item_iterations")
    if isinstance(iterations, dict):
        for item_id, attempts in iterations.items():
            latest = attempts[-1] if isinstance(attempts, list) and attempts else attempts
            _merge_task(tasks, {"id": item_id, **(latest if isinstance(latest, dict) else {})}, "active", order)
            order += 1

    per_commit = state.get("perCommit")
    if isinstance(per_commit, dict):
        for item in per_commit.get("queued") or []:
            _merge_task(tasks, item, "queued", order)
            order += 1
        if per_commit.get("in_flight"):
            _merge_task(tasks, per_commit["in_flight"], "active", order)
            order += 1
        for item in per_commit.get("completed") or []:
            _merge_task(tasks, item, "complete", order)
            order += 1

    recorded_tasks = context["selected_run"].get("tasks")
    if isinstance(recorded_tasks, list):
        default_status = "complete" if context["status"] == "complete" else "pending"
        for item in recorded_tasks:
            explicit = item.get("status") if isinstance(item, dict) else None
            _merge_task(tasks, item, _normalize_explicit_status(explicit) or default_status, order)
            order += 1
    return sorted(({key: value for key, value in task.items() if key != "order"} for task in tasks.values()), key=lambda task: tasks[task["id"]]["order"])


def _plan_tasks(
    workdir: Path,
    warnings: list[str],
    sources: list[Path],
    *,
    fallback: bool,
) -> list[dict[str, Any]]:
    text = _read_text(workdir, ".build-loop/plan.md", warnings, sources, max_bytes=MAX_PLAN_BYTES)
    if not text:
        return []
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = PLAN_TASK_RE.match(line.strip())
        if not match:
            continue
        task_id = match.group("id")
        title = match.group("title").strip()
        if task_id.lower().startswith("phase") or title.lower() in {"plan", "goal", "headline"} or task_id in seen:
            continue
        seen.add(task_id)
        tasks.append({"id": task_id, "title": title, "status": "pending", "owner": ""})
    if tasks and fallback:
        warnings.append("Major tasks came from plan headings because structured execution tasks were unavailable.")
    return tasks[:50]


def _enrich_task_titles(tasks: list[dict[str, Any]], planned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    titles = {str(item["id"]).casefold(): item["title"] for item in planned}
    for task in tasks:
        generic = _task_title(task["id"], task["id"])
        planned_title = titles.get(str(task["id"]).casefold())
        if planned_title and task["title"] == generic:
            task["title"] = planned_title
    return tasks


def _read_ledger(workdir: Path, warnings: list[str], sources: list[Path]) -> list[dict[str, Any]]:
    text = _read_text(workdir, ".build-loop/agent-ledger.jsonl", warnings, sources, max_bytes=MAX_JSON_BYTES)
    if not text:
        return []
    rows: list[dict[str, Any]] = []
    malformed = 0
    for line in text.splitlines()[-MAX_LEDGER_LINES:]:
        try:
            item = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if isinstance(item, dict) and item.get("agent"):
            rows.append(item)
    if malformed:
        warnings.append(f"Ignored {malformed} malformed agent ledger row(s).")
    return rows


def _agent_role(action: Any) -> str:
    return {
        "author": "planner",
        "re-plan": "planner",
        "execute": "implementer",
        "take-over": "implementer",
        "verify": "reviewer",
        "gate": "gate",
    }.get(str(action or ""), "agent")


def _agents_from_ledger(rows: Iterable[dict[str, Any]], run_id: str | None) -> list[dict[str, Any]]:
    if not run_id:
        return []
    selected = [row for row in rows if str(row.get("run_id") or "") == run_id]
    latest: dict[str, dict[str, Any]] = {}
    actions: dict[str, list[str]] = {}
    for row in selected:
        name = str(row["agent"])
        action = str(row.get("action") or "")
        if action and action not in actions.setdefault(name, []):
            actions[name].append(action)
        latest[name] = row
    agents: list[dict[str, Any]] = []
    for name, row in latest.items():
        explicit = _normalize_explicit_status(row.get("status"))
        agents.append({
            "name": name,
            "role": _agent_role(row.get("action")),
            "status": explicit or "invoked",
            "phase": _normalize_phase(row.get("phase")) or str(row.get("phase") or ""),
            "tier": str(row.get("tier") or ""),
            "model": str(row.get("model") or ""),
            "last_seen": str(row.get("ts") or ""),
            "actions": actions.get(name, []),
            "source": "agent-ledger",
        })
    return sorted(agents, key=lambda item: (item["last_seen"], item["name"]), reverse=True)


def _judge_agents(context: dict[str, Any]) -> list[dict[str, Any]]:
    latest = context.get("latest_run") or {}
    selected_run_id = context.get("run_id")
    if selected_run_id and str(latest.get("run_id") or "") != selected_run_id:
        return []
    decisions = latest.get("judge_decisions")
    if not isinstance(decisions, list):
        return []
    agents: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        if not isinstance(decision, dict) or not decision.get("judge_id"):
            continue
        name = str(decision["judge_id"])
        verdict = str(decision.get("verdict") or "")
        agents[name] = {
            "name": name,
            "role": "reviewer",
            "status": _normalize_explicit_status(verdict) or "invoked",
            "phase": "review",
            "tier": "",
            "model": "",
            "last_seen": str(decision.get("verdict_ts") or decision.get("ts") or ""),
            "actions": ["verify"],
            "source": "run judge record",
        }
    return sorted(agents.values(), key=lambda item: (item["last_seen"], item["name"]), reverse=True)


def _learn_agents(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    for order in receipt.get("work_orders", []) if isinstance(receipt.get("work_orders"), list) else []:
        if (
            not isinstance(order, dict)
            or not order.get("role")
            or order.get("status") not in {"complete", "failed"}
        ):
            continue
        role = str(order["role"])
        agents.append({
            "name": role,
            "role": "reviewer" if role == "promotion-reviewer" else "implementer",
            "status": _normalize_explicit_status(order.get("status")) or "queued",
            "phase": "learn",
            "tier": "",
            "model": "",
            "last_seen": str(order.get("attested_at") or receipt.get("updated_at") or receipt.get("created_at") or ""),
            "actions": ["verify" if role == "promotion-reviewer" else "execute"],
            "source": f"Learn attestation · {order.get('source') or 'pattern'}",
        })
    return agents


def _learn_work_orders(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    raw_orders = receipt.get("work_orders", []) if isinstance(receipt.get("work_orders"), list) else []
    for index, item in enumerate(raw_orders):
        if not isinstance(item, dict) or not item.get("role"):
            continue
        order_id = item.get("id") or f"learn-order-{index + 1}"
        orders.append({
            "id": str(order_id),
            "role": str(item["role"]),
            "status": _normalize_explicit_status(item.get("status")) or "queued",
            "source": str(item.get("source") or "pattern"),
            "pattern": str(item.get("pattern_key") or ""),
        })
    return orders[:20]


def _note_from_record(item: dict[str, Any]) -> str:
    for key in ("note", "comment", "message", "current_task_summary", "blocked_reason"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:800]
    return ""


def _working_notes(
    workdir: Path,
    context: dict[str, Any],
    warnings: list[str],
    sources: list[Path],
) -> list[dict[str, Any]]:
    """Return current-run free-form notes from the existing working-state channel."""
    run_id = context.get("run_id")
    if not run_id:
        return []

    records: list[dict[str, Any]] = []
    log_text = _read_text(
        workdir,
        ".build-loop/working-state/log.jsonl",
        warnings,
        sources,
        max_bytes=MAX_JSON_BYTES,
    )
    malformed = 0
    if log_text:
        for line in log_text.splitlines()[-MAX_WORKING_STATE_LINES:]:
            try:
                item = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if isinstance(item, dict):
                records.append(item)
    if malformed:
        warnings.append(f"Ignored {malformed} malformed working-state row(s).")

    current = _read_json(workdir, ".build-loop/working-state/current.json", warnings, sources)
    if isinstance(current, dict):
        records.append(current)

    notes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in records:
        item_run_id = item.get("run_id") or item.get("run")
        if str(item_run_id or "") != str(run_id):
            continue
        text = _note_from_record(item)
        if not text:
            continue
        timestamp = str(item.get("updated_at") or item.get("t") or item.get("ts") or "")
        author = str(item.get("agent") or item.get("author") or "Build Loop")
        identity = (timestamp, author, text)
        if identity in seen:
            continue
        seen.add(identity)
        raw_phase = item.get("phase")
        notes.append({
            "text": text,
            "author": author,
            "phase": _normalize_phase(raw_phase) or str(raw_phase or ""),
            "timestamp": timestamp,
            "source": "working-state",
        })
    return sorted(notes, key=lambda item: item["timestamp"], reverse=True)[:MAX_RUN_NOTES]


def _learn_notes(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for item in receipt.get("comments", []) if isinstance(receipt.get("comments"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            continue
        notes.append({
            "text": item["text"].strip()[:800],
            "author": str(item.get("source") or "Learn"),
            "phase": "learn",
            "timestamp": str(item.get("at") or ""),
            "source": "Learn receipt",
        })
    return notes


def _goal_text(workdir: Path, state: dict[str, Any], context: dict[str, Any], warnings: list[str], sources: list[Path]) -> str:
    intent = state.get("intent") if isinstance(state.get("intent"), dict) else {}
    for value in (
        intent.get("restated_intent"),
        intent.get("updateIntent"),
        context["selected_run"].get("goal"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    text = _read_text(workdir, ".build-loop/goal.md", warnings, sources, max_bytes=MAX_PLAN_BYTES)
    if text:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped
    return "No run goal is recorded yet."


def _updated_at(sources: list[Path]) -> str | None:
    mtimes = []
    for path in sources:
        try:
            mtimes.append(path.stat().st_mtime)
        except OSError:
            continue
    if not mtimes:
        return None
    return datetime.fromtimestamp(max(mtimes), tz=timezone.utc).isoformat()


def build_run_projection(workdir: Path | str) -> dict[str, Any]:
    """Return a bounded, read-only dashboard snapshot for ``workdir``."""
    root = Path(workdir).expanduser().resolve()
    warnings: list[str] = []
    sources: list[Path] = []
    raw_state = _read_json(root, ".build-loop/state.json", warnings, sources)
    state = raw_state if isinstance(raw_state, dict) else {}
    context = _run_context(state)
    phases = _phase_projection(context, state, warnings)
    learn_receipt = _learn_receipt(root, context, warnings, sources)
    work_orders = _learn_work_orders(learn_receipt)
    tasks = _structured_tasks(state, context)
    if context["active"]:
        planned = _plan_tasks(root, warnings, sources, fallback=not tasks)
        tasks = _enrich_task_titles(tasks, planned) if tasks else planned
    ledger_rows = _read_ledger(root, warnings, sources)
    agents = _agents_from_ledger(ledger_rows, context["run_id"])
    known_agents = {(item["name"], item["phase"]) for item in agents}
    for agent in [*_judge_agents(context), *_learn_agents(learn_receipt)]:
        identity = (agent["name"], agent["phase"])
        if identity not in known_agents:
            agents.append(agent)
            known_agents.add(identity)
    agents.sort(key=lambda item: (item["last_seen"], item["name"]), reverse=True)
    notes = [*_working_notes(root, context, warnings, sources), *_learn_notes(learn_receipt)]
    notes = sorted(notes, key=lambda item: item["timestamp"], reverse=True)[:MAX_RUN_NOTES]
    if context["active"] and not agents:
        warnings.append("No agent invocation records exist for this run yet.")

    completed_tasks = sum(item["status"] == "complete" for item in tasks)
    active_tasks = sum(item["status"] == "active" for item in tasks)
    blocked_tasks = sum(item["status"] == "blocked" for item in tasks)
    current_phase = next((item for item in phases if item["status"] in {"active", "blocked"}), None)
    return {
        "schema_version": "1.0",
        "generated_at": _now(),
        "updated_at": _updated_at(sources),
        "status": context["status"],
        "run_id": context["run_id"],
        "goal": _goal_text(root, state, context, warnings, sources),
        "current_phase": current_phase["id"] if current_phase else None,
        "current_phase_name": current_phase["name"] if current_phase else None,
        "phases": phases,
        "tasks": tasks,
        "agents": agents,
        "work_orders": work_orders,
        "notes": notes,
        "metrics": {
            "phases_complete": sum(item["status"] == "complete" for item in phases),
            "phases_total": len(phases),
            "tasks_complete": completed_tasks,
            "tasks_active": active_tasks,
            "tasks_blocked": blocked_tasks,
            "tasks_total": len(tasks),
            "agents_invoked": len(agents),
            "work_orders_pending": sum(item["status"] in {"pending", "queued"} for item in work_orders),
        },
        "sources": [str(path.relative_to(root)) for path in dict.fromkeys(sources)],
        "warnings": list(dict.fromkeys(warnings)),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default=".")
    args = parser.parse_args()
    print(json.dumps(build_run_projection(args.workdir), indent=2, sort_keys=True))
