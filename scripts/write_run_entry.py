#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Deterministic Review-F writer for build-loop.

Appends a run entry to `.build-loop/state.json.runs[]` and, for each active
experimental artifact, appends an `applied` row to
`.build-loop/experiments/<name>.jsonl` with correct confound tracking.

Contract:
  stdout      -> run_id on success, nothing else
  stderr      -> human-readable log lines
  exit 0      -> success
  exit 1      -> validation error (bad args, missing required field, wrong type)
  exit 2      -> filesystem error (permission denied, disk full, lock timeout)

Atomicity: fcntl.flock(LOCK_EX) on a sidecar .lock file + tmpfile + os.replace.
Additive migration: existing non-runs[] top-level keys are never touched.
Zero dependencies. Python 3.11+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Local sibling import — atomic primitives live in one place (scripts/atomic_io.py).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from atomic_io import LockedFile, atomic_write_bytes  # type: ignore  # noqa: E402,F401

REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "run_id": str,
    "date": str,
    "goal": str,
    "outcome": str,
    "phases": dict,
    "filesTouched": list,
    "diagnosticCommands": list,
    "manualInterventions": list,
    "active_experimental_artifacts": list,
}
VALID_OUTCOMES = {"pass", "fail", "partial"}
VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
VALID_JUDGE_VERDICTS = {"approve", "rethink", "new_approach"}
VALID_JUDGE_SPEC_ALIGNMENT = {"aligned", "partial", "misaligned"}
VALID_BUDGET_MODES = {"default", "long", "custom"}

# M2 — execution-state heartbeat (crash-recovery)
EXECUTION_SCHEMA_VERSION = 1
EXECUTION_VALID_PHASES = {"execute", "review", "iterate", "report"}
EXECUTION_VALID_ACTIONS = {
    "start",            # initialize execution block (Phase 1 Assess complete, before chunk dispatch)
    "dispatch_chunk",   # move chunk_id queued → in_flight; refresh heartbeat
    "return_chunk",     # move chunk_id in_flight → completed with status; refresh heartbeat
    "phase_transition", # update phase field
    "iterate_attempt",  # increment iterate_attempt (preserves 5x cap across resume)
    "review_e_pass",    # append a Review Sub-step E telemetry row to state["reviewE"]
    "complete",         # phase=report; clean-completion sentinel
}
EXECUTION_RETURN_STATUSES = {
    "fixed", "partial", "scope_breach", "deferred_architecture",
    "evidence_stale", "plan_malformed", "needs_dependency", "failed",
    "concurrent_modification_detected",
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def iso_basic_utc(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def compute_run_id(goal: str, now: datetime | None = None) -> str:
    goal_hash = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:8]
    return f"run_{iso_basic_utc(now)}_{goal_hash}"


def iso_utc(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CorruptStateError(ValueError):
    """Raised when an existing state.json is present but unparseable."""


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise CorruptStateError(f"{path} is not valid JSON: {e}") from e


def append_run_entry(state_path: Path, entry: dict) -> None:
    with LockedFile(state_path):
        state = read_json(state_path)
        if state is None:
            state = {}
        if not isinstance(state, dict):
            raise ValueError(f"{state_path} is not a JSON object at top level")
        runs = state.get("runs")
        if runs is not None and not isinstance(runs, list):
            log(f"warn: existing 'runs' is not a list (got {type(runs).__name__}); preserving as 'runs_legacy'")
            state["runs_legacy"] = runs
            runs = None
        if runs is None:
            runs = []
            state["runs"] = runs
        runs.append(entry)
        atomic_write_bytes(state_path, (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def update_execution_state(
    state_path: Path,
    action: str,
    *,
    run_id: str | None = None,
    chunk_id: str | None = None,
    status: str | None = None,
    phase: str | None = None,
    queued_chunks: list[str] | None = None,
    file_ownership: dict[str, list[str]] | None = None,
    files_scanned: list[str] | None = None,
    is_final: bool | None = None,
    now: datetime | None = None,
) -> dict:
    """M2 — atomic update of state.json.execution heartbeat block.

    Args:
        state_path: path to .build-loop/state.json
        action: one of EXECUTION_VALID_ACTIONS
        run_id: required for action='start'; ignored otherwise (read from existing block)
        chunk_id: required for dispatch_chunk / return_chunk
        status: required for return_chunk; one of EXECUTION_RETURN_STATUSES
        phase: required for phase_transition; one of EXECUTION_VALID_PHASES
        queued_chunks: required for action='start'; the initial work list
        file_ownership: required for action='start'; chunk_id → list[file]
        files_scanned: required for action='review_e_pass'; files E inspected this pass
        is_final: required for action='review_e_pass'; True iff this is the last Review pass
        now: injection seam for tests

    Returns the new execution block. Raises ValueError on bad input. Atomic via LockedFile.
    Sub-5ms typical (one read, one write, one fsync, indented JSON).
    """
    if action not in EXECUTION_VALID_ACTIONS:
        raise ValueError(f"action must be one of {sorted(EXECUTION_VALID_ACTIONS)}, got {action!r}")
    ts = iso_utc(now)

    with LockedFile(state_path):
        state = read_json(state_path) or {}
        if not isinstance(state, dict):
            raise ValueError(f"{state_path} is not a JSON object at top level")
        execution = state.get("execution")
        if execution is not None and not isinstance(execution, dict):
            raise ValueError(f"{state_path}.execution exists but is not an object (got {type(execution).__name__})")

        if action == "start":
            if not run_id or not isinstance(run_id, str):
                raise ValueError("action='start' requires run_id")
            if queued_chunks is None or not isinstance(queued_chunks, list):
                raise ValueError("action='start' requires queued_chunks: list[str]")
            if file_ownership is None or not isinstance(file_ownership, dict):
                raise ValueError("action='start' requires file_ownership: dict[str, list[str]]")
            execution = {
                "schema_version": EXECUTION_SCHEMA_VERSION,
                "run_id": run_id,
                "phase": "execute",
                "iterate_attempt": 0,
                "in_flight_chunks": [],
                "completed_chunks": [],
                "queued_chunks": list(queued_chunks),
                "file_ownership": {k: list(v) for k, v in file_ownership.items()},
                "started_at": ts,
                "last_heartbeat_at": ts,
                "crashed_at": None,
            }
        elif action == "review_e_pass":
            # Telemetry only — records what Sub-step E actually scanned this Review
            # pass. Independent of the execution heartbeat block (does not require
            # 'start' first). pass_idx auto-derives from current reviewE length so
            # callers need not track it. Shape matches score_run.py:25-33 exactly.
            if files_scanned is None or not isinstance(files_scanned, list):
                raise ValueError("action='review_e_pass' requires files_scanned: list[str]")
            if not isinstance(is_final, bool):
                raise ValueError("action='review_e_pass' requires is_final: bool")
            review_e = state.get("reviewE")
            if review_e is None:
                review_e = []
            elif not isinstance(review_e, list):
                raise ValueError(f"{state_path}.reviewE exists but is not a list (got {type(review_e).__name__})")
            review_e.append({
                "pass_idx": len(review_e),
                "files_scanned": list(files_scanned),
                "is_final": is_final,
            })
            state["reviewE"] = review_e
            atomic_write_bytes(state_path, (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
            return {"reviewE": review_e}
        else:
            if not isinstance(execution, dict):
                raise ValueError(f"action={action!r} requires an existing execution block (run start first)")
            if action == "dispatch_chunk":
                if not chunk_id:
                    raise ValueError("action='dispatch_chunk' requires chunk_id")
                if chunk_id in execution.get("queued_chunks", []):
                    execution["queued_chunks"].remove(chunk_id)
                if chunk_id not in execution.setdefault("in_flight_chunks", []):
                    execution["in_flight_chunks"].append(chunk_id)
            elif action == "return_chunk":
                if not chunk_id:
                    raise ValueError("action='return_chunk' requires chunk_id")
                if status not in EXECUTION_RETURN_STATUSES:
                    raise ValueError(f"status must be one of {sorted(EXECUTION_RETURN_STATUSES)}, got {status!r}")
                if chunk_id in execution.get("in_flight_chunks", []):
                    execution["in_flight_chunks"].remove(chunk_id)
                execution.setdefault("completed_chunks", []).append({
                    "chunk_id": chunk_id,
                    "status": status,
                    "completed_at": ts,
                })
            elif action == "phase_transition":
                if phase not in EXECUTION_VALID_PHASES:
                    raise ValueError(f"phase must be one of {sorted(EXECUTION_VALID_PHASES)}, got {phase!r}")
                execution["phase"] = phase
            elif action == "iterate_attempt":
                execution["iterate_attempt"] = int(execution.get("iterate_attempt", 0)) + 1
            elif action == "complete":
                execution["phase"] = "report"

        execution["last_heartbeat_at"] = ts
        state["execution"] = execution
        atomic_write_bytes(state_path, (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        return execution


def append_experiment_row(jsonl_path: Path, row: dict) -> None:
    with LockedFile(jsonl_path):
        existing = jsonl_path.read_bytes() if jsonl_path.exists() else b""
        line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        atomic_write_bytes(jsonl_path, existing + line)


def append_experiment_rows(experiments_dir: Path, run_id: str, active: list[str], outcome: str, date: str) -> None:
    for name in active:
        path = experiments_dir / f"{name}.jsonl"
        if not path.exists():
            log(f"warn: no baseline for experiment '{name}' at {path}; skipping applied row (run a Phase 6 Learn scan first)")
            continue
        co_applied = [n for n in active if n != name]
        row = {
            "event": "applied",
            "date": date,
            "run_id": run_id,
            "triggered": True,
            "metric_value": None,
            "outcome": outcome,
            "co_applied_experimental_artifacts": co_applied,
            "confounded": len(co_applied) > 0,
        }
        append_experiment_row(path, row)
        log(f"appended applied row to {path.name} (confounded={row['confounded']})")


def validate_entry(entry: dict) -> None:
    for field, expected in REQUIRED_FIELDS.items():
        if field not in entry:
            raise ValueError(f"missing required field: {field}")
        if not isinstance(entry[field], expected):
            raise ValueError(f"field {field!r} must be {expected.__name__ if isinstance(expected, type) else expected}, got {type(entry[field]).__name__}")
    if entry["outcome"] not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(VALID_OUTCOMES)}, got {entry['outcome']!r}")


def files_touched_from_git(workdir: Path, pre_sha: str) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(workdir), "diff", "--name-only", f"{pre_sha}..HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deterministic Review-F writer for build-loop.")
    p.add_argument("--workdir", required=True, help="Project root containing .build-loop/")
    p.add_argument("--goal", required=True, help="Short goal text for this build")
    p.add_argument("--outcome", required=True, choices=sorted(VALID_OUTCOMES))
    p.add_argument("--phases-json", default="{}", help="Per-phase status dict as JSON string")
    p.add_argument("--files-touched", default="", help="Comma-separated list of files touched")
    p.add_argument("--files-touched-from-git", action="store_true", help="Derive from git diff <preBuildSha>..HEAD (preBuildSha read from state.json)")
    p.add_argument("--diagnostic-commands", default="", help="Newline-separated commands run during build")
    p.add_argument("--manual-interventions-json", default="[]", help="JSON list of {phase, note} objects")
    p.add_argument("--active-experimental-artifacts", default="", help="Comma-separated experimental artifact names that triggered this run")
    p.add_argument("--run-id", default=None, help="Override run_id (default: compute from goal + now)")
    p.add_argument(
        "--security-findings-json",
        default=None,
        help=(
            "Path to a JSON file containing a list of security-reviewer findings (or '-' for stdin). "
            "Each element must be an object with at minimum 'mapped_risks' (list of strings) and "
            "'severity' (CRITICAL|HIGH|MEDIUM|LOW). Other fields (id, title, evidence, snippet, "
            "recommendation) pass through. When omitted, no 'security_findings' key is written."
        ),
    )
    p.add_argument(
        "--judge-decisions-json",
        default=None,
        help=(
            "Path to a JSON file containing a list of advisory judge_decisions (or '-' for stdin). "
            "Each element must have 'judge_id' (str) and 'verdict' (approve|rethink|new_approach). "
            "Optional fields: checkpoint_id, confidence, spec_alignment, variances, meta_guidance, "
            "policy_refs, implementer_response, outcome. Judges are advisory — verdicts never block "
            "execution. Used by self-improvement-architect for prompt/rubric tuning."
        ),
    )
    p.add_argument(
        "--budget-summary-json",
        default=None,
        help=(
            "Path to a JSON file containing the autonomous-mode budget summary (or '-' for stdin). "
            "Shape: {mode: default|long|custom, budget_seconds: int, used_seconds: int, "
            "items_closed: int, items_deferred: int, commits: int, pushes: int}. Mirrors the "
            "--judge-decisions-json pattern. Omit for non-autonomous runs (default-mode 5-phase "
            "loop). Captured per plan §14.4 + §14.5 for telemetry / Phase 6 Learn pattern mining."
        ),
    )
    return p.parse_args(argv)


def _split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def load_security_findings(source: str) -> list[dict] | None:
    """Read findings JSON from a path or stdin ('-').

    Returns None when the file is missing or empty (caller should not write a 'security_findings'
    key — semantically equivalent to omitting the flag). Returns a list (possibly empty if the
    user explicitly passed `[]`) when the file decoded to a list shape.

    Validates that the decoded value is a list of objects, each with a 'mapped_risks' list of
    strings and a 'severity' string in VALID_SEVERITIES. Other fields pass through unchanged.
    Raises ValueError on malformed input.
    """
    if source == "-":
        raw = sys.stdin.read()
    else:
        path = Path(source)
        if not path.exists():
            log(f"note: --security-findings-json path {source} does not exist; treating as no findings (no security_findings key will be written)")
            return None
        raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"--security-findings-json is not valid JSON: {e}") from e
    # Accept either a bare list or the reviewer's full envelope ({"findings": [...], ...}).
    if isinstance(data, dict) and "findings" in data:
        data = data["findings"]
    if not isinstance(data, list):
        raise ValueError("--security-findings-json must decode to a list (or an object with a 'findings' list)")
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"security_findings[{i}] must be an object, got {type(item).__name__}")
        if "mapped_risks" not in item or not isinstance(item["mapped_risks"], list):
            raise ValueError(f"security_findings[{i}].mapped_risks must be a list of strings")
        if not all(isinstance(r, str) for r in item["mapped_risks"]):
            raise ValueError(f"security_findings[{i}].mapped_risks must contain only strings")
        sev = item.get("severity")
        if not isinstance(sev, str) or sev not in VALID_SEVERITIES:
            raise ValueError(f"security_findings[{i}].severity must be one of {sorted(VALID_SEVERITIES)}, got {sev!r}")
    return data


def load_budget_summary(source: str) -> dict | None:
    """Read autonomous-mode budget_summary JSON from a path or stdin ('-').

    Shape per plan §14.4 + §14.5: a single object capturing the run's wall-clock
    + queue-drain summary. All fields required (validate hard) so downstream
    pattern mining doesn't have to defend against partial shapes.

    Required fields:
      mode             one of VALID_BUDGET_MODES (default | long | custom)
      budget_seconds   int >= 0
      used_seconds     int >= 0
      items_closed     int >= 0    # queue items routed through Phase 2→3→4 to completion
      items_deferred   int >= 0    # queue items moved to .build-loop/followup/
      commits          int >= 0
      pushes           int >= 0

    Returns None when source path missing/empty (caller skips the key).
    Raises ValueError on type/shape errors.
    """
    if source == "-":
        raw = sys.stdin.read()
    else:
        path = Path(source)
        if not path.exists():
            log(f"note: --budget-summary-json path {source} does not exist; skipping")
            return None
        raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"--budget-summary-json is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("--budget-summary-json must decode to an object")
    mode = data.get("mode")
    if not isinstance(mode, str) or mode not in VALID_BUDGET_MODES:
        raise ValueError(f"budget_summary.mode must be one of {sorted(VALID_BUDGET_MODES)}, got {mode!r}")
    for field in ("budget_seconds", "used_seconds", "items_closed", "items_deferred", "commits", "pushes"):
        if field not in data:
            raise ValueError(f"budget_summary missing required field: {field}")
        if not isinstance(data[field], int) or isinstance(data[field], bool):
            raise ValueError(f"budget_summary.{field} must be int, got {type(data[field]).__name__}")
        if data[field] < 0:
            raise ValueError(f"budget_summary.{field} must be >= 0, got {data[field]}")
    return data


def load_judge_decisions(source: str) -> list[dict] | None:
    """Read advisory judge_decisions JSON from a path or stdin ('-').

    Shape per plan §12.5: advisory verdicts that never block execution. Each entry must have
    `judge_id` (str) and `verdict` (one of VALID_JUDGE_VERDICTS). Optional pass-through fields:
    checkpoint_id, confidence, spec_alignment, variances, meta_guidance, policy_refs,
    implementer_response, outcome.

    Returns None when missing/empty (caller skips the key). Returns a list otherwise.
    """
    if source == "-":
        raw = sys.stdin.read()
    else:
        path = Path(source)
        if not path.exists():
            log(f"note: --judge-decisions-json path {source} does not exist; skipping")
            return None
        raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"--judge-decisions-json is not valid JSON: {e}") from e
    if isinstance(data, dict) and "decisions" in data:
        data = data["decisions"]
    if not isinstance(data, list):
        raise ValueError("--judge-decisions-json must decode to a list (or an object with a 'decisions' list)")
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"judge_decisions[{i}] must be an object, got {type(item).__name__}")
        if not isinstance(item.get("judge_id"), str):
            raise ValueError(f"judge_decisions[{i}].judge_id must be a string")
        verdict = item.get("verdict")
        if not isinstance(verdict, str) or verdict not in VALID_JUDGE_VERDICTS:
            raise ValueError(f"judge_decisions[{i}].verdict must be one of {sorted(VALID_JUDGE_VERDICTS)}, got {verdict!r}")
        if "spec_alignment" in item:
            sa = item["spec_alignment"]
            if not isinstance(sa, str) or sa not in VALID_JUDGE_SPEC_ALIGNMENT:
                raise ValueError(f"judge_decisions[{i}].spec_alignment must be one of {sorted(VALID_JUDGE_SPEC_ALIGNMENT)}, got {sa!r}")
    return data


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return 1 if e.code else 0

    workdir = Path(args.workdir).resolve()
    build_dir = workdir / ".build-loop"
    state_path = build_dir / "state.json"
    experiments_dir = build_dir / "experiments"

    try:
        phases = json.loads(args.phases_json)
        if not isinstance(phases, dict):
            raise ValueError("--phases-json must decode to an object")
        manual_interventions = json.loads(args.manual_interventions_json)
        if not isinstance(manual_interventions, list):
            raise ValueError("--manual-interventions-json must decode to a list")
        security_findings = (
            load_security_findings(args.security_findings_json)
            if args.security_findings_json
            else None
        )
        judge_decisions = (
            load_judge_decisions(args.judge_decisions_json)
            if args.judge_decisions_json
            else None
        )
        budget_summary = (
            load_budget_summary(args.budget_summary_json)
            if args.budget_summary_json
            else None
        )
    except (json.JSONDecodeError, ValueError) as e:
        log(f"validation error: {e}")
        return 1

    files_touched = _split_csv(args.files_touched)
    if args.files_touched_from_git:
        state_existing = read_json(state_path) if state_path.exists() else {}
        pre_sha = state_existing.get("preBuildSha") if isinstance(state_existing, dict) else None
        if pre_sha:
            files_touched.extend(f for f in files_touched_from_git(workdir, pre_sha) if f not in files_touched)
        else:
            log("warn: --files-touched-from-git set but state.json has no preBuildSha; skipping git diff")

    active = _split_csv(args.active_experimental_artifacts)
    diagnostic_commands = [c for c in args.diagnostic_commands.splitlines() if c.strip()]
    run_id = args.run_id or compute_run_id(args.goal)
    date = iso_utc()

    entry = {
        "run_id": run_id,
        "date": date,
        "goal": args.goal,
        "outcome": args.outcome,
        "phases": phases,
        "diagnosticCommands": diagnostic_commands,
        "filesTouched": files_touched,
        "manualInterventions": manual_interventions,
        "active_experimental_artifacts": active,
    }
    if security_findings is not None:
        entry["security_findings"] = security_findings
    if judge_decisions is not None:
        entry["judge_decisions"] = judge_decisions
    if budget_summary is not None:
        entry["budget_summary"] = budget_summary

    try:
        validate_entry(entry)
    except ValueError as e:
        log(f"validation error: {e}")
        return 1

    try:
        append_run_entry(state_path, entry)
        log(f"appended run entry to {state_path} (run_id={run_id})")
        if active:
            append_experiment_rows(experiments_dir, run_id, active, args.outcome, date)
    except CorruptStateError as e:
        log(f"validation error: {e}")
        return 1
    except TimeoutError as e:
        log(f"filesystem error: {e}")
        return 2
    except OSError as e:
        log(f"filesystem error: {e}")
        return 2

    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
