#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Deterministic control plane for outcome-based Build Loop autonomy.

The supervisor answers four questions that prose alone cannot enforce:

* Is the run ready, or does a consequential fact require the operator?
* Which related queue items belong to this run's bounded starting manifest?
* Should a newly discovered issue execute, defer, or request a decision?
* Has an item repeated the same verdict often enough to quarantine it?

Completed-run records also build a small local task-shape profile. Duration is
evidence about future supervision needs; it is never the goal or a stop rule.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_io import LockedFile, atomic_write_bytes  # noqa: E402
from parallelism import HARD_CEILING, resolve_fanout  # noqa: E402
from tool_trace import summarize as summarize_tool_traces  # noqa: E402

AUTONOMY_DIR = Path(".build-loop/autonomy")
HISTORY_PATH = AUTONOMY_DIR / "task-history.jsonl"
MANIFEST_PATH = AUTONOMY_DIR / "queue-manifest.json"
CONVERGENCE_PATH = AUTONOMY_DIR / "convergence.json"
BACKPRESSURE_PATH = AUTONOMY_DIR / "backpressure.json"
STATE_PATH = Path(".build-loop/state.json")
DEFAULT_QUEUE_LIMIT = 12
DEFAULT_AUDIT_VERDICT_COUNT = 3
DEFAULT_SAME_VERDICT_LIMIT = 5
STABLE_WINDOW_SECONDS = 30
QUEUE_DIRS = ("issues", "backlog", "ux-queue", "followup", "proposals")
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")
DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(s|m|h)?$")

TASK_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("migration", ("migrate", "migration", "backfill", "schema")),
    ("refactor", ("refactor", "restructure", "modularize", "rename")),
    ("test-expansion", ("test", "coverage", "regression", "fixture")),
    ("backlog-batch", ("backlog", "queue", "issues", "cleanup batch")),
    ("mechanical-cleanup", ("cleanup", "format", "lint", "dead code", "codemod")),
    ("feature", ("build", "implement", "feature", "dashboard", "workflow")),
    ("documentation", ("document", "docs", "readme", "guide")),
)

TASK_QUEUE_BASELINES = {
    "documentation": 4,
    "feature": 8,
    "refactor": 12,
    "migration": 12,
    "test-expansion": 16,
    "mechanical-cleanup": 24,
    "backlog-batch": 24,
    "other": DEFAULT_QUEUE_LIMIT,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Any) -> None:
    atomic_write_bytes(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode())


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with LockedFile(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
            handle.flush()


def parse_duration(value: str) -> int:
    match = DURATION_RE.fullmatch(value.strip().lower())
    if match is None:
        raise ValueError("budget must be a number followed by s, m, or h")
    amount = float(match.group(1))
    multiplier = {None: 1, "s": 1, "m": 60, "h": 3600}[match.group(2)]
    seconds = int(amount * multiplier)
    if seconds < 1:
        raise ValueError("budget must be at least one second")
    return seconds


def initialize_run(
    workdir: Path,
    goal: str,
    *,
    run_id: str,
    budget: str | None = None,
    long: bool = False,
    autonomous: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Initialize one host-neutral run envelope without making time the goal."""
    if not goal.strip() or not run_id.strip():
        raise ValueError("goal and run_id are required")
    started = now or datetime.now(timezone.utc)
    if budget:
        budget_seconds, mode = parse_duration(budget), "custom"
    elif long:
        budget_seconds, mode = 8 * 3600, "long"
    else:
        budget_seconds, mode = 2 * 3600, "default"
    config = _read_json(workdir / ".build-loop/config.json", {})
    autonomy_config = config.get("autonomy", {}) if isinstance(config, dict) else {}
    raw_limit = autonomy_config.get("queueLimit", "adaptive") if isinstance(autonomy_config, dict) else "adaptive"
    queue_limit = resolve_queue_limit(workdir, goal, configured=raw_limit)
    execution = {
        "run_id": run_id,
        "goal": goal.strip(),
        "autonomous": bool(autonomous),
        "outcome_first": True,
        "related_issue_policy": "execute_related_reversible_testable",
        "queue_limit": queue_limit,
        "queue_policy": "adaptive" if str(raw_limit).lower() == "adaptive" else "configured",
        "task_profile": task_profile(workdir, goal),
        "budget": {
            "mode": mode,
            "started_at": started.isoformat(),
            "deadline_at": (started + timedelta(seconds=budget_seconds)).isoformat(),
            "last_checkin_at": None,
            "commits_since_push": 0,
            "checkin_interval_pct": 50,
            "soft_target": True,
        },
    }
    path = workdir / STATE_PATH
    with LockedFile(path):
        state = _read_json(path, {})
        state["execution"] = {**state.get("execution", {}), **execution}
        _atomic_json(path, state)
    return execution


def infer_task_type(goal: str) -> str:
    normalized = goal.lower()
    for task_type, terms in TASK_PATTERNS:
        if any(term in normalized for term in terms):
            return task_type
    return "other"


def _history_rows(workdir: Path) -> list[dict[str, Any]]:
    path = workdir / HISTORY_PATH
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def task_profile(workdir: Path, goal: str) -> dict[str, Any]:
    task_type = infer_task_type(goal)
    matching = [row for row in _history_rows(workdir) if row.get("task_type") == task_type]
    durations = [float(row["duration_seconds"]) for row in matching if row.get("duration_seconds") is not None]
    discovered = [int(row.get("related_discovered", 0)) for row in matching]
    completed = [int(row.get("related_completed", 0)) for row in matching]
    tool_calls = [int(row.get("tool_calls", 0)) for row in matching]
    tool_errors = [int(row.get("tool_errors", 0)) for row in matching]
    repeated_calls = [int(row.get("repeated_tool_calls", 0)) for row in matching]
    sample_count = len(matching)
    median_duration = int(statistics.median(durations)) if durations else None
    mean_discovered = round(statistics.mean(discovered), 1) if discovered else 0.0
    completion_rate = round(sum(completed) / sum(discovered), 2) if sum(discovered) else None
    observed_error_rate = round(sum(tool_errors) / sum(tool_calls), 3) if sum(tool_calls) else 0.0
    mean_repeated_calls = round(statistics.mean(repeated_calls), 1) if repeated_calls else 0.0
    supervision_recommended = bool(
        sample_count >= 2 and (
            (median_duration or 0) >= 3600
            or mean_discovered >= 2
            or observed_error_rate >= 0.1
            or mean_repeated_calls >= 2
        )
    )
    preflight_focus: list[str] = []
    if (median_duration or 0) >= 3600:
        preflight_focus.append("restart-safe checkpoints")
    if mean_discovered >= 2:
        preflight_focus.append("related-work scope and validation")
    if observed_error_rate >= 0.1:
        preflight_focus.append("provider and tool health")
    if mean_repeated_calls >= 2:
        preflight_focus.append("missing evidence before retry")
    return {
        "task_type": task_type,
        "sample_count": sample_count,
        "median_duration_seconds": median_duration,
        "mean_related_discovered": mean_discovered,
        "related_completion_rate": completion_rate,
        "tool_error_rate": observed_error_rate,
        "mean_repeated_tool_calls": mean_repeated_calls,
        "preflight_focus": preflight_focus,
        "supervision_recommended": supervision_recommended,
        "basis": "history" if sample_count >= 2 else "insufficient_history",
    }


def resolve_queue_limit(
    workdir: Path,
    goal: str,
    *,
    available_count: int | None = None,
    configured: Any = "adaptive",
    signals: dict[str, Any] | None = None,
) -> int:
    """Choose a finite manifest size from task shape and prior run evidence."""
    if isinstance(configured, int) and configured > 0:
        result = configured
    elif str(configured).isdigit() and int(configured) > 0:
        result = int(configured)
    else:
        profile = task_profile(workdir, goal)
        result = TASK_QUEUE_BASELINES.get(profile["task_type"], DEFAULT_QUEUE_LIMIT)
        learned_discovery = float(profile.get("mean_related_discovered") or 0)
        if profile.get("basis") == "history" and learned_discovery > 0:
            result = max(result, math.ceil(learned_discovery * 1.5))
        observed = signals if signals is not None else host_signals(workdir)
        thermal = str(observed.get("thermal_state", "unknown")).lower()
        load = float(observed.get("load_ratio", 0))
        memory = float(observed.get("memory_percent", 0))
        if thermal in {"serious", "critical"} or load >= 0.9 or memory >= 85:
            result = max(1, math.ceil(result / 2))
        elif thermal == "nominal" and load < 0.5 and memory < 70:
            result = math.ceil(result * 1.25)
    result = max(1, min(HARD_CEILING, result))
    if isinstance(available_count, int) and available_count >= 0:
        return min(result, max(1, available_count))
    return result


def record_run(workdir: Path, record: dict[str, Any]) -> dict[str, Any]:
    goal = str(record.get("goal") or "").strip()
    if not goal:
        raise ValueError("goal is required")
    run_id = str(record.get("run_id") or f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    telemetry = record.get("telemetry") if isinstance(record.get("telemetry"), dict) else summarize_tool_traces(workdir, run_id)
    row = {
        "run_id": run_id,
        "recorded_at": _now(),
        "goal": goal,
        "task_type": str(record.get("task_type") or infer_task_type(goal)),
        "duration_seconds": max(0, int(record.get("duration_seconds", 0))),
        "related_discovered": max(0, int(record.get("related_discovered", 0))),
        "related_completed": max(0, int(record.get("related_completed", 0))),
        "interventions": max(0, int(record.get("interventions", 0))),
        "outcome": str(record.get("outcome") or "unknown"),
        "tool_calls": max(0, int(telemetry.get("tool_calls", 0))),
        "tool_errors": max(0, int(telemetry.get("tool_errors", 0))),
        "repeated_tool_calls": max(0, int(telemetry.get("repeated_calls", 0))),
        "provider_429s": max(0, int(telemetry.get("provider_429s", 0))),
        "p95_tool_duration_ms": telemetry.get("p95_duration_ms"),
    }
    _append_jsonl(workdir / HISTORY_PATH, row)
    return row


def _infer_validation(workdir: Path) -> list[str]:
    commands: list[str] = []
    if (workdir / "pyproject.toml").exists():
        commands.append("uv run pytest -q")
    elif (workdir / "pytest.ini").exists():
        commands.append("python3 -m pytest -q")
    if (workdir / "package.json").exists():
        commands.append("npm test")
    if (workdir / "Cargo.toml").exists():
        commands.append("cargo test")
    return commands


def assess_preflight(workdir: Path, request: dict[str, Any]) -> dict[str, Any]:
    goal = str(request.get("goal") or "").strip()
    criteria = [str(v).strip() for v in request.get("success_criteria", []) if str(v).strip()]
    validations = [str(v).strip() for v in request.get("validation_commands", []) if str(v).strip()]
    scope = [str(v).strip() for v in request.get("scope_roots", []) if str(v).strip()]
    questions: list[dict[str, str]] = []
    assumptions: list[dict[str, str]] = []

    if not goal:
        questions.append({
            "field": "goal",
            "question": "What outcome must this run produce?",
            "why": "The supervisor cannot judge related work or completion without an intent anchor.",
            "impact": "Work may expand into unrelated changes or stop before the desired outcome.",
        })
    if request.get("production_action") and not request.get("production_policy"):
        questions.append({
            "field": "production_policy",
            "question": "What measured condition authorizes the production action?",
            "why": "Production remains a human gate unless a measured preauthorization exists.",
            "impact": "The run will finish safe work and hold the production action.",
        })
    if request.get("irreversible_action") and not request.get("irreversible_policy"):
        questions.append({
            "field": "irreversible_policy",
            "question": "Should the run skip, wait, or never attempt the irreversible action?",
            "why": "An irreversible choice cannot be validated by undoing it.",
            "impact": "The run will preserve data and defer the action until answered.",
        })
    if request.get("major_user_decision") and not request.get("major_user_decision_policy"):
        questions.append({
            "field": "major_user_decision_policy",
            "question": "Which user-impacting direction should the run implement?",
            "why": "Different answers change the product outcome rather than an implementation detail.",
            "impact": "Implementation pauses only at that decision boundary.",
        })

    if not criteria and goal:
        criteria = ["The requested outcome works on the primary path", "Relevant deterministic validation passes"]
        assumptions.append({
            "field": "success_criteria",
            "value": "primary path works and deterministic checks pass",
            "validation": "Derive concrete assertions from the plan before implementation and execute them before closeout.",
        })
    if not scope:
        scope = [str(workdir.resolve())]
        assumptions.append({
            "field": "scope_roots",
            "value": str(workdir.resolve()),
            "validation": "Reject discovered work whose resolved path leaves the repository root.",
        })
    if not validations:
        validations = _infer_validation(workdir)
        assumptions.append({
            "field": "validation_commands",
            "value": ", ".join(validations) if validations else "goal-specific probe required",
            "validation": "Run the inferred command before execution; replace it if it does not exercise the changed surface.",
        })
    if request.get("external_dependencies") is None:
        assumptions.append({
            "field": "external_dependencies",
            "value": "none required for the first safe chunk",
            "validation": "Probe imports, CLIs, credentials, and reachable endpoints before the first dependent chunk.",
        })

    profile = task_profile(workdir, goal)
    return {
        "ready": not questions,
        "questions": questions,
        "assumptions": assumptions,
        "resolved": {"goal": goal, "success_criteria": criteria, "scope_roots": scope, "validation_commands": validations},
        "task_profile": profile,
        "operating_mode": "supervised_queue_drain" if profile["supervision_recommended"] else "bounded_queue_drain",
    }


def _tokens(text: str) -> set[str]:
    ignored = {"the", "and", "for", "with", "from", "that", "this", "into", "build", "loop"}
    return {token for token in TOKEN_RE.findall(text.lower()) if token not in ignored}


def _queue_candidates(workdir: Path) -> Iterable[tuple[str, Path]]:
    base = workdir / ".build-loop"
    for queue in QUEUE_DIRS:
        directory = base / queue
        if directory.is_dir():
            for path in sorted(directory.rglob("*.md")):
                if path.name.upper() == "INDEX.MD":
                    continue
                yield queue, path


def snapshot_queue(workdir: Path, goal: str, limit: int | None = None) -> dict[str, Any]:
    goal_tokens = _tokens(goal)
    ranked: list[dict[str, Any]] = []
    for queue, path in _queue_candidates(workdir):
        text = path.read_text(encoding="utf-8", errors="replace")[:12000]
        overlap = sorted(goal_tokens & _tokens(text))
        if not overlap:
            continue
        ranked.append({
            "id": f"{queue}:{path.stem}",
            "queue": queue,
            "path": str(path.relative_to(workdir)),
            "alignment_terms": overlap,
            "alignment_score": round(len(overlap) / max(1, len(goal_tokens)), 3),
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        })
    ranked.sort(key=lambda row: (-row["alignment_score"], row["queue"], row["path"]))
    live_signals = host_signals(workdir)
    if limit is None:
        config = _read_json(workdir / ".build-loop/config.json", {})
        autonomy = config.get("autonomy", {}) if isinstance(config, dict) else {}
        configured = autonomy.get("queueLimit", "adaptive") if isinstance(autonomy, dict) else "adaptive"
        limit = resolve_queue_limit(
            workdir,
            goal,
            available_count=len(ranked),
            configured=configured,
            signals=live_signals,
        )
    if limit < 1 or limit > HARD_CEILING:
        raise ValueError(f"limit must be between 1 and {HARD_CEILING}")
    selected = ranked[:limit]
    run_id = _current_run_id(workdir)
    state = _read_json(workdir / STATE_PATH, {})
    execution = state.get("execution") if isinstance(state, dict) else {}
    lease_expires_at = (execution or {}).get("deadline_at") or (
        datetime.now(timezone.utc) + timedelta(hours=2)
    ).isoformat()
    manifest = {
        "created_at": _now(),
        "run_id": run_id,
        "lease_owner": run_id,
        "resume_key": run_id,
        "lease_expires_at": lease_expires_at,
        "goal": goal,
        "limit": limit,
        "capacity_signals": live_signals,
        "selected": selected,
        "deferred_aligned_count": max(0, len(ranked) - len(selected)),
        "later_arrivals_policy": "next_manifest",
    }
    _atomic_json(workdir / MANIFEST_PATH, manifest)
    if run_id:
        safe_run_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", run_id).strip("-") or "run"
        run_path = workdir / AUTONOMY_DIR / "manifests" / f"{safe_run_id}.json"
        manifest["run_manifest_path"] = str(run_path.relative_to(workdir))
        _atomic_json(run_path, manifest)
        _atomic_json(workdir / MANIFEST_PATH, manifest)
    return manifest


def classify_related_issue(issue: dict[str, Any]) -> dict[str, Any]:
    impacts = [str(value) for value in issue.get("impacts", [])]
    decision_reason: str | None = None
    if issue.get("production"):
        decision_reason = "The issue requires a production action."
    elif issue.get("irreversible"):
        decision_reason = "The issue requires an irreversible action."
    elif issue.get("major_user_impact"):
        decision_reason = "The issue changes a major user-facing outcome."
    elif not issue.get("inside_repo", True):
        decision_reason = "The issue resolves outside the authorized repository scope."
    if decision_reason:
        return {"route": "decision", "next_action": "request_decision", "why": decision_reason, "choices": issue.get("choices", []), "impacts": impacts}
    if not issue.get("intent_aligned", False):
        return {"route": "followup", "next_action": "persist_followup", "why": "The issue lacks a resolved link to the current intent.", "choices": [], "impacts": impacts}
    if not issue.get("validation_available", False):
        return {"route": "followup", "next_action": "persist_followup", "why": "No deterministic check can yet prove the fix.", "choices": [], "impacts": impacts}
    if not issue.get("reversible", True):
        return {"route": "decision", "next_action": "request_decision", "why": "The proposed fix cannot be safely reversed.", "choices": issue.get("choices", []), "impacts": impacts}
    return {"route": "execute", "next_action": "execute_and_validate", "why": "The issue is related, in scope, reversible, and deterministically testable.", "choices": [], "impacts": impacts}


def record_verdict(
    workdir: Path,
    item_id: str,
    verdict: str,
    limit: int = DEFAULT_SAME_VERDICT_LIMIT,
    *,
    audit_at: int = DEFAULT_AUDIT_VERDICT_COUNT,
    resolved: bool = False,
    actor_id: str,
    actor_session: str,
) -> dict[str, Any]:
    if not item_id.strip() or not verdict.strip() or not actor_id.strip() or not actor_session.strip():
        raise ValueError("item_id, verdict, actor_id, and actor_session are required")
    if audit_at != DEFAULT_AUDIT_VERDICT_COUNT or limit != DEFAULT_SAME_VERDICT_LIMIT:
        raise ValueError("convergence policy is fixed at audit_at=3 and limit=5")
    path = workdir / CONVERGENCE_PATH
    with LockedFile(path):
        state = _read_json(path, {"items": {}})
        items = state.setdefault("items", {})
        prior = items.get(item_id, {"last_verdict": None, "same_verdict_count": 0})
        if resolved:
            current = {
                "last_verdict": verdict,
                "same_verdict_count": 0,
                "action": "resolved",
                "audit_required": False,
                "updated_at": _now(),
            }
            items[item_id] = current
            _atomic_json(path, state)
            return {"item_id": item_id, **current, "audit_at": audit_at, "limit": limit}
        if prior.get("audit_required"):
            return {
                "item_id": item_id,
                **prior,
                "action": "independent_audit",
                "audit_at": audit_at,
                "limit": limit,
            }
        count = int(prior.get("same_verdict_count", 0)) + 1 if prior.get("last_verdict") == verdict else 1
        prior_actors = prior.get("actor_receipts", []) if prior.get("last_verdict") == verdict else []
        actor_receipts = [*prior_actors, {
            "actor_id": actor_id.strip(),
            "actor_session": actor_session.strip(),
            "recorded_at": _now(),
        }][-limit:]
        audit_required = count == audit_at
        action = "quarantine" if count >= limit else "independent_audit" if audit_required else "continue"
        current = {
            "last_verdict": verdict,
            "same_verdict_count": count,
            "action": action,
            "audit_required": audit_required,
            "audit_evidence": None if prior.get("last_verdict") != verdict else prior.get("audit_evidence"),
            "actor_receipts": actor_receipts,
            "updated_at": _now(),
        }
        items[item_id] = current
        _atomic_json(path, state)
    return {"item_id": item_id, **current, "audit_at": audit_at, "limit": limit}


def record_independent_audit(
    workdir: Path,
    item_id: str,
    evidence: str,
    *,
    auditor_id: str,
    auditor_session: str,
) -> dict[str, Any]:
    if not item_id.strip() or not evidence.strip() or not auditor_id.strip() or not auditor_session.strip():
        raise ValueError("item_id, auditor evidence, auditor_id, and auditor_session are required")
    path = workdir / CONVERGENCE_PATH
    with LockedFile(path):
        state = _read_json(path, {"items": {}})
        current = state.setdefault("items", {}).get(item_id)
        if not current or not current.get("audit_required"):
            raise ValueError("item does not have a pending independent audit")
        actor_receipts = current.get("actor_receipts", [])
        if any(
            row.get("actor_id") == auditor_id.strip()
            or row.get("actor_session") == auditor_session.strip()
            for row in actor_receipts
            if isinstance(row, dict)
        ):
            raise ValueError("independent auditor must differ from the workers that produced the verdict")
        run_id = _current_run_id(workdir)
        ledger_path = workdir / ".build-loop/agent-ledger.jsonl"
        ledger_receipt: dict[str, Any] | None = None
        if run_id and ledger_path.exists():
            for line in ledger_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                refs = row.get("refs") if isinstance(row, dict) else {}
                if (
                    row.get("run_id") == run_id
                    and row.get("agent") == auditor_id.strip()
                    and row.get("action") == "verify"
                    and isinstance(refs, dict)
                    and refs.get("item_id") == item_id
                    and refs.get("session_id") == auditor_session.strip()
                ):
                    ledger_receipt = row
        if ledger_receipt is None:
            raise ValueError("independent audit requires a matching current-run agent-ledger receipt")
        completed_at = _now()
        receipt = {
            "auditor_id": auditor_id.strip(),
            "auditor_session": auditor_session.strip(),
            "evidence_sha256": hashlib.sha256(evidence.strip().encode()).hexdigest(),
            "ledger_receipt_sha256": hashlib.sha256(
                json.dumps(ledger_receipt, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "completed_at": completed_at,
        }
        current = {
            **current,
            "audit_required": False,
            "audit_evidence": evidence.strip(),
            "audit_receipt": receipt,
            "audit_completed_at": completed_at,
            "action": "continue",
        }
        state["items"][item_id] = current
        _atomic_json(path, state)
    return {"item_id": item_id, **current}


def backpressure_action(signals: dict[str, Any]) -> dict[str, Any]:
    """Route new worker admission from provider, host, and cost evidence."""
    concurrency = max(0, int(signals.get("current_concurrency", 1)))
    maximum = max(0, int(signals.get("max_concurrency", concurrency)))
    rate_limits = max(0, int(signals.get("provider_429s", 0)))
    memory = float(signals.get("memory_percent", 0))
    disk_free = float(signals.get("disk_free_gb", 999))
    thermal = str(signals.get("thermal_state", "nominal")).lower()
    error_streak = max(0, int(signals.get("error_streak", 0)))
    load_ratio = max(0.0, float(signals.get("load_ratio", 0)))
    latency_p95 = max(0.0, float(signals.get("latency_p95_ms", 0)))
    latency_baseline = max(0.0, float(signals.get("latency_baseline_ms", 0)))
    cost_used = max(0.0, float(signals.get("cost_used", 0)))
    cost_ceiling = max(0.0, float(signals.get("cost_ceiling", 0)))
    cost_ratio = cost_used / cost_ceiling if cost_ceiling else 0.0
    critical: list[str] = []
    pressure: list[str] = []
    if disk_free < 1:
        critical.append("disk_free_below_1gb")
    if memory >= 95:
        critical.append("memory_at_or_above_95_percent")
    if thermal == "critical":
        critical.append("thermal_critical")
    if cost_ceiling and cost_ratio >= 1:
        critical.append("cost_ceiling_reached")
    if rate_limits >= 2:
        pressure.append("repeated_provider_429")
    if memory >= 85:
        pressure.append("memory_at_or_above_85_percent")
    if thermal in {"serious", "critical"}:
        pressure.append(f"thermal_{thermal}")
    if error_streak >= 3:
        pressure.append("worker_error_streak")
    if load_ratio >= 0.9:
        pressure.append("host_load_at_or_above_90_percent")
    if latency_baseline and latency_p95 >= latency_baseline * 2:
        pressure.append("p95_latency_doubled")
    if cost_ceiling and cost_ratio >= 0.8:
        pressure.append("cost_at_or_above_80_percent")
    if concurrency > maximum:
        pressure.append("current_concurrency_exceeds_capacity")
    if critical:
        action, next_concurrency, reasons = "pause_new_work", 0, critical
    elif maximum == 0:
        action, next_concurrency, reasons = "pause_new_work", 0, ["capacity_exhausted"]
    elif concurrency > maximum:
        action, next_concurrency, reasons = "reduce_concurrency", maximum, pressure
    elif pressure:
        action, next_concurrency, reasons = (
            "pause_new_work" if concurrency == 0 else "reduce_concurrency",
            0 if concurrency == 0 else max(1, min(maximum, concurrency // 2)),
            pressure,
        )
    elif int(signals.get("stable_windows", 0)) >= 2 and concurrency < maximum:
        action, next_concurrency, reasons = "recover_one", concurrency + 1, ["two_stable_windows"]
    else:
        action, next_concurrency, reasons = "steady", min(concurrency, maximum), ["signals_within_ceiling"]
    return {
        "action": action,
        "current_concurrency": concurrency,
        "next_concurrency": next_concurrency,
        "reasons": reasons,
        "cost_ratio": round(cost_ratio, 3) if cost_ceiling else None,
        "load_ratio": round(load_ratio, 3),
        "latency_ratio": round(latency_p95 / latency_baseline, 3) if latency_baseline else None,
    }


def _probe(command: list[str]) -> str:
    """Run a short, read-only host probe and fail soft when unavailable."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _thermal_state(output: str) -> str:
    normalized = output.lower()
    if not normalized:
        return "unknown"
    speed_match = re.search(r"cpu_speed_limit\s*=\s*(\d+)", normalized)
    thermal_match = re.search(r"thermal(?:_pressure)?_level\s*=\s*(\d+)", normalized)
    if speed_match and int(speed_match.group(1)) < 60:
        return "critical"
    if (speed_match and int(speed_match.group(1)) < 100) or (
        thermal_match and int(thermal_match.group(1)) > 0
    ):
        return "serious"
    if "no thermal warning" in normalized or "thermal level = 0" in normalized:
        return "nominal"
    return "unknown"


def host_signals(workdir: Path) -> dict[str, Any]:
    """Collect portable host pressure with optional macOS thermal detail."""
    cpu_count = max(1, os.cpu_count() or 1)
    try:
        load_ratio = os.getloadavg()[0] / cpu_count
    except OSError:
        load_ratio = 0.0
    try:
        disk_free_gb = shutil.disk_usage(workdir).free / (1024 ** 3)
    except OSError:
        disk_free_gb = 999.0

    signals: dict[str, Any] = {
        "load_ratio": round(load_ratio, 3),
        "disk_free_gb": round(disk_free_gb, 2),
        "thermal_state": _thermal_state(_probe(["pmset", "-g", "therm"])),
        "signal_source": "host_probe",
    }
    memory_output = _probe(["memory_pressure", "-Q"])
    free_match = re.search(r"free percentage:\s*(\d+(?:\.\d+)?)%", memory_output, re.I)
    if free_match:
        signals["memory_percent"] = round(100 - float(free_match.group(1)), 1)
    return signals


def _current_run_id(workdir: Path) -> str | None:
    state = _read_json(workdir / STATE_PATH, {})
    execution = state.get("execution") if isinstance(state, dict) else {}
    return str((execution or {}).get("run_id") or state.get("run_id") or "") or None


def _update_pressure_state(workdir: Path, signals: dict[str, Any]) -> dict[str, Any]:
    path = workdir / BACKPRESSURE_PATH
    with LockedFile(path):
        state = _read_json(path, {})
        trace_totals = {
            "provider_429s": max(0, int(signals.get("trace_provider_429_total", 0))),
            "tool_errors": max(0, int(signals.get("trace_tool_error_total", 0))),
        }
        prior_totals = state.get("trace_totals", {}) if isinstance(state.get("trace_totals"), dict) else {}
        trace_deltas = {
            key: value if value < int(prior_totals.get(key, 0)) else value - int(prior_totals.get(key, 0))
            for key, value in trace_totals.items()
        }
        signals.setdefault("provider_429s", trace_deltas["provider_429s"])
        signals.setdefault("error_streak", min(3, trace_deltas["tool_errors"]))
        cost_ceiling = float(signals.get("cost_ceiling", 0))
        cost_ratio = float(signals.get("cost_used", 0)) / cost_ceiling if cost_ceiling else 0
        latency = float(signals.get("latency_p95_ms", 0))
        baseline = float(state.get("latency_baseline_ms") or 0)
        stable = (
            int(signals.get("provider_429s", 0)) == 0
            and int(signals.get("error_streak", 0)) == 0
            and float(signals.get("memory_percent", 0)) < 75
            and float(signals.get("disk_free_gb", 999)) >= 5
            and float(signals.get("load_ratio", 0)) < 0.75
            and str(signals.get("thermal_state", "unknown")).lower() in {"nominal", "unknown"}
            and cost_ratio < 0.7
            and (not baseline or not latency or latency < baseline * 1.5)
        )
        observed_at = _now()
        last_observation = state.get("last_stable_observation_at")
        elapsed = STABLE_WINDOW_SECONDS
        if last_observation:
            try:
                elapsed = (
                    datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                    - datetime.fromisoformat(str(last_observation).replace("Z", "+00:00"))
                ).total_seconds()
            except ValueError:
                elapsed = STABLE_WINDOW_SECONDS
        advances_window = stable and elapsed >= STABLE_WINDOW_SECONDS
        stable_windows = (
            int(state.get("stable_windows", 0)) + 1
            if advances_window
            else int(state.get("stable_windows", 0)) if stable
            else 0
        )
        if advances_window and latency:
            baseline = latency if not baseline else baseline * 0.8 + latency * 0.2
        state = {
            "stable_windows": stable_windows,
            "latency_baseline_ms": round(baseline, 3) if baseline else None,
            "trace_totals": trace_totals,
            "trace_deltas": trace_deltas,
            "last_stable_observation_at": observed_at if advances_window else last_observation if stable else None,
            "updated_at": observed_at,
        }
        _atomic_json(path, state)
    return state


def select_fanout(workdir: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Let the supervisor admit only the capacity justified by work and telemetry."""
    ready_items = max(0, int(request.get("independent_items", 1)))
    hints = {
        key: request[key]
        for key in (
            "execution_location", "provider", "model", "model_size", "output_size",
            "effort", "segment", "tier", "token_budget", "measured_tokens", "agent", "shared_capacity",
            "active_elsewhere",
        )
        if request.get(key) is not None
    }
    capacity = resolve_fanout(
        workdir,
        requested=max(1, ready_items),
        independent_items=ready_items,
        **hints,
    )
    signals = host_signals(workdir)
    trace = summarize_tool_traces(
        workdir,
        str(request.get("run_id") or _current_run_id(workdir) or "") or None,
        recent_seconds=300,
    )
    signals.update({
        "trace_provider_429_total": trace.get("provider_429s", 0),
        "trace_tool_error_total": trace.get("tool_errors", 0),
        "latency_p95_ms": trace.get("p95_duration_ms") or 0,
        "telemetry_repeated_calls": trace.get("repeated_calls", 0),
    })
    signals.update(request.get("signals") or {})
    pressure_state = _update_pressure_state(workdir, signals)
    signals.setdefault("stable_windows", pressure_state["stable_windows"])
    signals.setdefault("latency_baseline_ms", pressure_state.get("latency_baseline_ms") or 0)
    current = max(0, int(signals.get("current_concurrency", 0)))
    if capacity["effective_max"] == 0:
        return {
            "decision_owner": "autonomy_supervisor",
            "absolute_ceiling": HARD_CEILING,
            "capacity": capacity,
            "admission": {
                "action": "pause_new_work",
                "current_concurrency": current,
                "next_concurrency": 0,
                "reasons": [
                    "no_independent_work"
                    if "independent_work" in capacity["limiting_factors"]
                    else "shared_capacity_exhausted"
                ],
                "cost_ratio": None,
                "load_ratio": signals.get("load_ratio"),
                "latency_ratio": None,
            },
            "observed_signals": signals,
        }
    signals["current_concurrency"] = current
    signals["max_concurrency"] = capacity["effective_max"]
    assessed = backpressure_action(signals)
    if current == 0 and assessed["action"] in {"steady", "recover_one"}:
        initial = min(4, capacity["effective_max"])
        pressure = {
            "action": "admit_initial",
            "current_concurrency": 0,
            "next_concurrency": initial,
            "reasons": ["bounded_initial_ramp"],
            "cost_ratio": None,
            "load_ratio": assessed["load_ratio"],
            "latency_ratio": assessed["latency_ratio"],
        }
    else:
        pressure = assessed
    return {
        "decision_owner": "autonomy_supervisor",
        "absolute_ceiling": HARD_CEILING,
        "capacity": capacity,
        "admission": pressure,
        "observed_signals": signals,
    }


def _payload(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("payload must be a JSON object")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Loop autonomy supervisor")
    parser.add_argument("--workdir", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    initialize = sub.add_parser("initialize")
    initialize.add_argument("--goal", required=True)
    initialize.add_argument("--run-id", required=True)
    initialize.add_argument("--budget")
    initialize.add_argument("--long", action="store_true")
    initialize.add_argument("--autonomous", choices=("true", "false"), default="true")
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--request", required=True)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--goal", required=True)
    snapshot.add_argument("--limit", type=int)
    related = sub.add_parser("classify-related")
    related.add_argument("--issue", required=True)
    verdict = sub.add_parser("verdict")
    verdict.add_argument("--item", required=True)
    verdict.add_argument("--verdict", required=True)
    verdict.add_argument("--limit", type=int, default=DEFAULT_SAME_VERDICT_LIMIT)
    verdict.add_argument("--audit-at", type=int, default=DEFAULT_AUDIT_VERDICT_COUNT)
    verdict.add_argument("--resolved", action="store_true")
    verdict.add_argument("--actor-id", required=True)
    verdict.add_argument("--actor-session", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--item", required=True)
    audit.add_argument("--evidence", required=True)
    audit.add_argument("--auditor-id", required=True)
    audit.add_argument("--auditor-session", required=True)
    completed = sub.add_parser("record-run")
    completed.add_argument("--record", required=True)
    profile = sub.add_parser("profile")
    profile.add_argument("--goal", required=True)
    pressure = sub.add_parser("backpressure")
    pressure.add_argument("--signals", required=True)
    fanout = sub.add_parser("fanout")
    fanout.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    workdir = Path(args.workdir).resolve()
    try:
        if args.command == "initialize":
            result = initialize_run(
                workdir, args.goal, run_id=args.run_id, budget=args.budget,
                long=args.long, autonomous=args.autonomous == "true",
            )
        elif args.command == "preflight":
            result = assess_preflight(workdir, _payload(args.request))
        elif args.command == "snapshot":
            result = snapshot_queue(workdir, args.goal, args.limit)
        elif args.command == "classify-related":
            result = classify_related_issue(_payload(args.issue))
        elif args.command == "verdict":
            result = record_verdict(
                workdir, args.item, args.verdict, args.limit,
                audit_at=args.audit_at, resolved=args.resolved,
                actor_id=args.actor_id, actor_session=args.actor_session,
            )
        elif args.command == "audit":
            result = record_independent_audit(
                workdir,
                args.item,
                args.evidence,
                auditor_id=args.auditor_id,
                auditor_session=args.auditor_session,
            )
        elif args.command == "record-run":
            result = record_run(workdir, _payload(args.record))
        elif args.command == "backpressure":
            result = backpressure_action(_payload(args.signals))
        elif args.command == "fanout":
            result = select_fanout(workdir, _payload(args.request))
        else:
            result = task_profile(workdir, args.goal)
    except (OSError, ValueError, TimeoutError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
