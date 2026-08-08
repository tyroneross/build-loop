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
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_io import LockedFile, atomic_write_bytes  # noqa: E402

AUTONOMY_DIR = Path(".build-loop/autonomy")
HISTORY_PATH = AUTONOMY_DIR / "task-history.jsonl"
MANIFEST_PATH = AUTONOMY_DIR / "queue-manifest.json"
CONVERGENCE_PATH = AUTONOMY_DIR / "convergence.json"
STATE_PATH = Path(".build-loop/state.json")
DEFAULT_QUEUE_LIMIT = 12
DEFAULT_SAME_VERDICT_LIMIT = 3
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
    raw_limit = autonomy_config.get("queueLimit", DEFAULT_QUEUE_LIMIT) if isinstance(autonomy_config, dict) else DEFAULT_QUEUE_LIMIT
    queue_limit = int(raw_limit) if str(raw_limit).isdigit() else DEFAULT_QUEUE_LIMIT
    queue_limit = max(1, min(100, queue_limit))
    execution = {
        "run_id": run_id,
        "goal": goal.strip(),
        "autonomous": bool(autonomous),
        "outcome_first": True,
        "related_issue_policy": "execute_related_reversible_testable",
        "queue_limit": queue_limit,
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
    sample_count = len(matching)
    median_duration = int(statistics.median(durations)) if durations else None
    mean_discovered = round(statistics.mean(discovered), 1) if discovered else 0.0
    completion_rate = round(sum(completed) / sum(discovered), 2) if sum(discovered) else None
    supervision_recommended = bool(
        sample_count >= 2 and ((median_duration or 0) >= 3600 or mean_discovered >= 2)
    )
    return {
        "task_type": task_type,
        "sample_count": sample_count,
        "median_duration_seconds": median_duration,
        "mean_related_discovered": mean_discovered,
        "related_completion_rate": completion_rate,
        "supervision_recommended": supervision_recommended,
        "basis": "history" if sample_count >= 2 else "insufficient_history",
    }


def record_run(workdir: Path, record: dict[str, Any]) -> dict[str, Any]:
    goal = str(record.get("goal") or "").strip()
    if not goal:
        raise ValueError("goal is required")
    row = {
        "run_id": str(record.get("run_id") or f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"),
        "recorded_at": _now(),
        "goal": goal,
        "task_type": str(record.get("task_type") or infer_task_type(goal)),
        "duration_seconds": max(0, int(record.get("duration_seconds", 0))),
        "related_discovered": max(0, int(record.get("related_discovered", 0))),
        "related_completed": max(0, int(record.get("related_completed", 0))),
        "interventions": max(0, int(record.get("interventions", 0))),
        "outcome": str(record.get("outcome") or "unknown"),
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


def snapshot_queue(workdir: Path, goal: str, limit: int = DEFAULT_QUEUE_LIMIT) -> dict[str, Any]:
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
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
    selected = ranked[:limit]
    manifest = {
        "created_at": _now(),
        "goal": goal,
        "limit": limit,
        "selected": selected,
        "deferred_aligned_count": max(0, len(ranked) - len(selected)),
        "later_arrivals_policy": "next_manifest",
    }
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
        return {"route": "decision", "why": decision_reason, "choices": issue.get("choices", []), "impacts": impacts}
    if not issue.get("intent_aligned", False):
        return {"route": "followup", "why": "The issue lacks a resolved link to the current intent.", "choices": [], "impacts": impacts}
    if not issue.get("validation_available", False):
        return {"route": "followup", "why": "No deterministic check can yet prove the fix.", "choices": [], "impacts": impacts}
    if not issue.get("reversible", True):
        return {"route": "decision", "why": "The proposed fix cannot be safely reversed.", "choices": issue.get("choices", []), "impacts": impacts}
    return {"route": "execute", "why": "The issue is related, in scope, reversible, and deterministically testable.", "choices": [], "impacts": impacts}


def record_verdict(workdir: Path, item_id: str, verdict: str, limit: int = DEFAULT_SAME_VERDICT_LIMIT) -> dict[str, Any]:
    if not item_id.strip() or not verdict.strip():
        raise ValueError("item_id and verdict are required")
    path = workdir / CONVERGENCE_PATH
    with LockedFile(path):
        state = _read_json(path, {"items": {}})
        items = state.setdefault("items", {})
        prior = items.get(item_id, {"last_verdict": None, "same_verdict_count": 0})
        count = int(prior.get("same_verdict_count", 0)) + 1 if prior.get("last_verdict") == verdict else 1
        action = "quarantine" if count >= limit else "continue"
        current = {"last_verdict": verdict, "same_verdict_count": count, "action": action, "updated_at": _now()}
        items[item_id] = current
        _atomic_json(path, state)
    return {"item_id": item_id, **current, "limit": limit}


def backpressure_action(signals: dict[str, Any]) -> dict[str, Any]:
    """Route new worker admission from provider, host, and cost evidence."""
    concurrency = max(1, int(signals.get("current_concurrency", 1)))
    maximum = max(concurrency, int(signals.get("max_concurrency", concurrency)))
    rate_limits = max(0, int(signals.get("provider_429s", 0)))
    memory = float(signals.get("memory_percent", 0))
    disk_free = float(signals.get("disk_free_gb", 999))
    thermal = str(signals.get("thermal_state", "nominal")).lower()
    error_streak = max(0, int(signals.get("error_streak", 0)))
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
    if cost_ceiling and cost_ratio >= 0.8:
        pressure.append("cost_at_or_above_80_percent")
    if critical:
        action, next_concurrency, reasons = "pause_new_work", 0, critical
    elif pressure:
        action, next_concurrency, reasons = "reduce_concurrency", max(1, concurrency // 2), pressure
    elif int(signals.get("stable_windows", 0)) >= 2 and concurrency < maximum:
        action, next_concurrency, reasons = "recover_one", concurrency + 1, ["two_stable_windows"]
    else:
        action, next_concurrency, reasons = "steady", concurrency, ["signals_within_ceiling"]
    return {
        "action": action,
        "current_concurrency": concurrency,
        "next_concurrency": next_concurrency,
        "reasons": reasons,
        "cost_ratio": round(cost_ratio, 3) if cost_ceiling else None,
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
    snapshot.add_argument("--limit", type=int, default=DEFAULT_QUEUE_LIMIT)
    related = sub.add_parser("classify-related")
    related.add_argument("--issue", required=True)
    verdict = sub.add_parser("verdict")
    verdict.add_argument("--item", required=True)
    verdict.add_argument("--verdict", required=True)
    verdict.add_argument("--limit", type=int, default=DEFAULT_SAME_VERDICT_LIMIT)
    completed = sub.add_parser("record-run")
    completed.add_argument("--record", required=True)
    profile = sub.add_parser("profile")
    profile.add_argument("--goal", required=True)
    pressure = sub.add_parser("backpressure")
    pressure.add_argument("--signals", required=True)
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
            result = record_verdict(workdir, args.item, args.verdict, args.limit)
        elif args.command == "record-run":
            result = record_run(workdir, _payload(args.record))
        elif args.command == "backpressure":
            result = backpressure_action(_payload(args.signals))
        else:
            result = task_profile(workdir, args.goal)
    except (OSError, ValueError, TimeoutError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
