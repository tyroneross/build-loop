#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Deterministically inspect one Build Loop run without invoking a model.

The command reads ``.build-loop/state.json`` and bounded local Git metadata,
then emits one stable JSON envelope.  It does not decide whether the work is
correct and it does not mutate lifecycle state.  ``--emit-telemetry`` appends
a custom event to a repo-local JSONL stream for a later exporter to map.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "build-loop.run-status.v1"
TELEMETRY_SCHEMA = "build-loop.run-status-observation.v1"


def _identity(row: dict[str, Any]) -> str | None:
    for key in ("build_loop_id", "run_id", "id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _git(workdir: Path, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(workdir), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return result.returncode, result.stdout.strip()


def _managed_worktree(root: Path, raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        managed_root = (root / ".build-loop" / "worktrees").resolve()
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve(strict=False)
        candidate.relative_to(managed_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def _registered_worktrees(root: Path) -> dict[Path, str | None]:
    """Return exact Git-registered worktree roots and their local branch names."""
    code, output = _git(root, "worktree", "list", "--porcelain")
    if code != 0:
        return {}
    registered: dict[Path, str | None] = {}
    current_path: Path | None = None
    for line in [*output.splitlines(), ""]:
        if line.startswith("worktree "):
            try:
                current_path = Path(line.removeprefix("worktree ")).resolve(strict=False)
            except (OSError, RuntimeError):
                current_path = None
            if current_path is not None:
                registered[current_path] = None
        elif line.startswith("branch refs/heads/") and current_path is not None:
            registered[current_path] = line.removeprefix("branch refs/heads/")
        elif not line:
            current_path = None
    return registered


def _worktree_status(root: Path, execution: dict[str, Any], run_rows: list[dict[str, Any]]) -> dict[str, Any]:
    raw_path = execution.get("run_worktree_path")
    raw_branch = execution.get("run_worktree_branch")
    if not raw_path or not raw_branch:
        for row in run_rows:
            refs = row.get("createdRefs")
            for ref in refs if isinstance(refs, list) else []:
                if not isinstance(ref, dict) or ref.get("kind") != "worktree":
                    continue
                raw_path = raw_path or ref.get("path")
                raw_branch = raw_branch or ref.get("branch")
                break

    candidate_path = _managed_worktree(root, raw_path)
    registered = _registered_worktrees(root)
    managed_path = candidate_path if candidate_path in registered else None
    registered_branch = registered.get(managed_path) if managed_path is not None else None
    branch_matches = (
        registered_branch == raw_branch
        if isinstance(raw_branch, str) and managed_path is not None
        else None
    )
    result: dict[str, Any] = {
        "path": raw_path if isinstance(raw_path, str) else None,
        "path_is_managed": managed_path is not None,
        "exists": managed_path.exists() if managed_path is not None else None,
        "branch": raw_branch if isinstance(raw_branch, str) else None,
        "registered_branch": registered_branch,
        "branch_matches_worktree": branch_matches,
        "branch_exists": None,
        "clean": None,
        "head": None,
        "commits_ahead_of_current_head": None,
        "integrated_into_current_head": None,
    }

    if managed_path is not None and managed_path.exists() and branch_matches is not False:
        code, porcelain = _git(managed_path, "status", "--porcelain")
        result["clean"] = not bool(porcelain) if code == 0 else None
        code, head = _git(managed_path, "rev-parse", "HEAD")
        result["head"] = head if code == 0 else None

    if isinstance(raw_branch, str) and raw_branch.strip():
        code, branch_head = _git(root, "rev-parse", "--verify", f"refs/heads/{raw_branch}")
        result["branch_exists"] = code == 0
        if code == 0:
            result["head"] = result["head"] or branch_head
            ancestor_code, _ = _git(root, "merge-base", "--is-ancestor", raw_branch, "HEAD")
            result["integrated_into_current_head"] = ancestor_code == 0
            count_code, count = _git(root, "rev-list", "--count", f"HEAD..{raw_branch}")
            if count_code == 0 and count.isdigit():
                result["commits_ahead_of_current_head"] = int(count)
    return result


def _terminal_run(row: dict[str, Any]) -> bool:
    closeout = row.get("closeout")
    branch_closeout = row.get("branch_closeout")
    if isinstance(branch_closeout, dict):
        return branch_closeout.get("status") == "complete"
    return bool(
        row.get("status") == "complete"
        or row.get("phase") == "complete"
        or row.get("outcome") == "pass"
        or (isinstance(closeout, dict) and closeout.get("strict_success") is True)
    )


def inspect_run(
    workdir: Path,
    run_id: str,
    *,
    staleness_minutes: int = 5,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = workdir.resolve()
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state_path = root / ".build-loop" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
        state_error = "state.json is missing"
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        state = {}
        state_error = f"state.json is unreadable: {exc}"
    else:
        if isinstance(state, dict):
            state_error = None
        else:
            state = {}
            state_error = "state.json root must be a JSON object"

    active = state.get("execution")
    active_match = active if isinstance(active, dict) and _identity(active) == run_id else None
    history = state.get("historicalExecutions")
    historical = [
        row for row in (history if isinstance(history, list) else [])
        if isinstance(row, dict) and _identity(row) == run_id
    ]
    runs = state.get("runs")
    run_rows = [
        row for row in (runs if isinstance(runs, list) else [])
        if isinstance(row, dict) and _identity(row) == run_id
    ]
    records = ([active_match] if active_match else []) + historical + run_rows
    found = bool(records)
    execution = active_match or (historical[-1] if historical else (run_rows[-1] if run_rows else {}))

    reasons: list[str] = []
    sources: list[str] = []
    if active_match:
        sources.append("execution")
    if historical:
        sources.append("historicalExecutions")
    if run_rows:
        sources.append("runs")

    heartbeat = _parse_iso(execution.get("last_heartbeat_at")) if execution else None
    heartbeat_age = max(0.0, (observed_at - heartbeat).total_seconds()) if heartbeat else None

    if not found:
        status = "unknown"
        reasons.append(state_error or "run id is not present in state.json")
    elif any(row.get("archive_disposition") == "explicitly_abandoned" for row in historical):
        status = "abandoned"
        reasons.append("historical execution records archive_disposition=explicitly_abandoned")
    elif any(
        isinstance(row.get("branch_closeout"), dict)
        and row["branch_closeout"].get("status") in {"pending_external_merge", "prepared", "deferred"}
        for row in run_rows
    ):
        status = "awaiting_closeout"
        pending = [
            row["branch_closeout"].get("status")
            for row in run_rows
            if isinstance(row.get("branch_closeout"), dict)
            and row["branch_closeout"].get("status") in {"pending_external_merge", "prepared", "deferred"}
        ]
        reasons.append(f"run ledger branch_closeout is {pending[-1]}")
    elif any(_terminal_run(row) for row in run_rows) or any(
        row.get("archive_disposition") == "terminal_branch_closeout" for row in historical
    ):
        status = "completed"
        reasons.append("persisted lifecycle records contain terminal closeout evidence")
    elif execution.get("crashed_at") or execution.get("crash_signal"):
        status = "crashed"
        reasons.append("execution contains a durable crash marker")
    elif active_match and execution.get("phase") == "report":
        status = "awaiting_closeout"
        reasons.append("execution reached report but no terminal closeout is recorded")
    elif active_match and heartbeat_age is not None and heartbeat_age > staleness_minutes * 60:
        status = "stale"
        reasons.append(f"last heartbeat is older than {staleness_minutes} minutes")
    elif active_match and heartbeat_age is not None:
        status = "unknown"
        reasons.append(
            f"last heartbeat is within {staleness_minutes} minutes, but process liveness is not persisted"
        )
    elif any(isinstance(row.get("branch_closeout"), dict) for row in run_rows):
        status = "unknown"
        branch_statuses = [
            row["branch_closeout"].get("status")
            for row in run_rows
            if isinstance(row.get("branch_closeout"), dict)
        ]
        reasons.append(f"run ledger branch_closeout is nonterminal: {branch_statuses[-1]}")
    else:
        status = "unknown"
        reasons.append("records exist but do not prove active or terminal lifecycle state")

    worktree = _worktree_status(root, execution, run_rows) if found else _worktree_status(root, {}, [])
    if worktree.get("branch_exists") and not worktree.get("integrated_into_current_head"):
        ahead = worktree.get("commits_ahead_of_current_head")
        suffix = f" with {ahead} commit(s) ahead" if isinstance(ahead, int) else ""
        reasons.append(f"preserved run branch is not integrated into current HEAD{suffix}")

    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "found": found,
        "status": status,
        "summary": f"{run_id} is {status}",
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "sources": sources,
        "reasons": reasons,
        "execution": {
            "schema_version": execution.get("schema_version") if execution else None,
            "phase": execution.get("phase") if execution else None,
            "started_at": execution.get("started_at") if execution else None,
            "last_heartbeat_at": execution.get("last_heartbeat_at") if execution else None,
            "heartbeat_age_seconds": round(heartbeat_age, 3) if heartbeat_age is not None else None,
            "heartbeat_status": (
                "fresh"
                if heartbeat_age is not None and heartbeat_age <= staleness_minutes * 60
                else ("stale" if heartbeat_age is not None else "unknown")
            ),
            "crashed_at": execution.get("crashed_at") if execution else None,
            "crash_signal": execution.get("crash_signal") if execution else None,
            "archive_disposition": execution.get("archive_disposition") if execution else None,
        },
        "run_ledger": {
            "matches": len(run_rows),
            "outcomes": [row.get("outcome") for row in run_rows if row.get("outcome") is not None],
            "statuses": [row.get("status") for row in run_rows if row.get("status") is not None],
            "closeout_statuses": [
                row["branch_closeout"].get("status")
                for row in run_rows
                if isinstance(row.get("branch_closeout"), dict)
            ],
        },
        "worktree": worktree,
        "process_liveness": {
            "status": "unknown",
            "reason": "execution state does not yet persist a process identity that can be verified safely",
        },
        "cost": {
            "model_calls": 0,
            "tokens": 0,
            "method": "deterministic local JSON and bounded Git inspection",
        },
    }


def _append_telemetry(workdir: Path, envelope: dict[str, Any]) -> Path:
    root = workdir.resolve()
    path = root / ".build-loop" / "telemetry" / "run-status.jsonl"
    event = {
        "schema": TELEMETRY_SCHEMA,
        "name": "build_loop.run_status_observed",
        "timestamp": envelope["observed_at"],
        "attributes": envelope,
    }
    payload = (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(root, directory_flags)
    try:
        try:
            os.mkdir(".build-loop", mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        build_loop_fd = os.open(".build-loop", directory_flags, dir_fd=root_fd)
        try:
            try:
                os.mkdir("telemetry", mode=0o700, dir_fd=build_loop_fd)
            except FileExistsError:
                pass
            telemetry_fd = os.open("telemetry", directory_flags, dir_fd=build_loop_fd)
            try:
                fd = os.open("run-status.jsonl", file_flags, 0o600, dir_fd=telemetry_fd)
                try:
                    os.write(fd, payload)
                finally:
                    os.close(fd)
            finally:
                os.close(telemetry_fd)
        finally:
            os.close(build_loop_fd)
    finally:
        os.close(root_fd)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", help="exact Build Loop run id")
    parser.add_argument("--workdir", default=".", help="repository root")
    parser.add_argument("--staleness-minutes", type=int, default=5)
    parser.add_argument(
        "--emit-telemetry",
        action="store_true",
        help="append the observation to .build-loop/telemetry/run-status.jsonl",
    )
    args = parser.parse_args()
    if args.staleness_minutes < 1:
        parser.error("--staleness-minutes must be positive")
    workdir = Path(args.workdir).resolve()
    envelope = inspect_run(workdir, args.run_id, staleness_minutes=args.staleness_minutes)
    if args.emit_telemetry:
        envelope["telemetry_path"] = str(_append_telemetry(workdir, envelope))
    print(json.dumps(envelope, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
