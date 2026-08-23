#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""M3 — Resume resolver for /build-loop:run --resume.

Validates an existing .build-loop/state.json.execution block, computes the
remaining work list (queued + in-flight that didn't return), and runs the
concurrent-modification check on already-completed chunks.

Inputs (CLI):
  --workdir            project root (contains .build-loop/)
  --resume-arg         literal run_id, or 'latest', or '' (no --resume present)
  --staleness-minutes  threshold for the heartbeat-staleness path (default 5)
  --current-session-id explicit host/thread identity for resume continuity
  --archive-terminal-legacy-crash  atomically archive proven terminal residue

Output (stdout, JSON):
  {
    "decision": "resume" | "fresh" | "prompt_user" | "abort",
    "reason": "<human-readable>",
    "run_id": "<resolved-or-null>",
    "remaining_chunks": [{chunk_id, files, prior_status_if_any}],
    "iterate_attempt": <int>,
    "concurrent_modifications": [{chunk_id, files}],
    "execution_block": {<copy>},
    "envelopes": {chunk_id: [<envelope>, ...]}
  }

Exit codes: 0 success / 1 validation error (incompatible schema, no run, etc.)
            2 filesystem error
Zero deps. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import Any

from atomic_io import LockedFile, atomic_write_bytes

EXPECTED_SCHEMA_VERSION = 1
MAX_RESULT_FILES = 1_024
MAX_RESULT_FILE_BYTES = 512 * 1_024
MAX_RESULT_TOTAL_BYTES = 16 * 1_024 * 1_024
MAX_RESULT_ATTEMPTS_PER_CHUNK = 64
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,254}\Z")


class StateReadError(ValueError):
    """An existing state file could not be trusted as a JSON object."""


# Source of truth for the 9 return statuses is the writer, not a copy here.
# `scripts/` is on sys.path when this file runs as a script; the fallback keeps
# the module importable from a host that maps scripts/ differently.
try:
    from write_run_entry.execstate import EXECUTION_RETURN_STATUSES
except ImportError:  # pragma: no cover — exercised only on an unusual sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from write_run_entry.execstate import EXECUTION_RETURN_STATUSES
    except ImportError:
        EXECUTION_RETURN_STATUSES = set()  # degrade to "never warn", never crash


def _load_state(workdir: Path) -> dict | None:
    p = workdir / ".build-loop" / "state.json"
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        if p.is_symlink():
            raise StateReadError(f"cannot read existing {p}: dangling symlink") from exc
        return None
    except (OSError, UnicodeError) as exc:
        raise StateReadError(f"cannot read existing {p}: {exc}") from exc
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StateReadError(
            f"existing {p} is invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(state, dict):
        raise StateReadError(f"existing {p} must contain a JSON object")
    return state


def _abort_state_read(reason: str) -> dict:
    return {
        "decision": "abort",
        "reason": reason,
        "run_id": None,
        "remaining_chunks": [],
        "iterate_attempt": 0,
        "concurrent_modifications": [],
        "execution_block": None,
        "envelopes": {},
    }


def _same_session_continuity(execution: dict, current_session_id: str | None) -> bool:
    if not isinstance(current_session_id, str) or not current_session_id.strip():
        return False
    recorded = execution.get("current_session_id")
    return isinstance(recorded, str) and recorded.strip() == current_session_id.strip()


def _validate_execution_v1(execution: dict) -> str | None:
    """Return the first structural error in a schema-v1 execution block."""
    run_id = execution.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return "execution.run_id must be a non-empty string"
    if _RUN_ID_RE.fullmatch(run_id) is None:
        return (
            "execution.run_id must be a single safe path component of at most "
            "255 ASCII characters"
        )
    if execution.get("phase") not in {"execute", "review", "iterate", "report"}:
        return "execution.phase must be execute, review, iterate, or report"
    heartbeat = execution.get("last_heartbeat_at")
    if not isinstance(heartbeat, str) or _parse_iso(heartbeat) is None:
        return "execution.last_heartbeat_at is missing or unparseable"
    # ABSENT is valid and means zero. The consumer below already reads this as
    # `int(execution.get("iterate_attempt", 0))`, so demanding the key here made
    # the validator stricter than the code it guards — it rejected states the
    # resolver could read perfectly well, including every state written before
    # the key existed. A PRESENT value must still be a nonnegative int; bools are
    # rejected on purpose (`type(...) is not int`), since True would read as 1.
    iterate_attempt = execution.get("iterate_attempt", 0)
    if type(iterate_attempt) is not int or iterate_attempt < 0:
        return "execution.iterate_attempt must be a nonnegative integer"
    for key in ("queued_chunks", "in_flight_chunks"):
        value = execution.get(key)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value
        ):
            return f"execution.{key} must be a list of non-empty strings"
    completed = execution.get("completed_chunks")
    if not isinstance(completed, list):
        return "execution.completed_chunks must be a list"
    for entry in completed:
        if isinstance(entry, str) and entry:
            continue
        if not isinstance(entry, dict):
            return "execution.completed_chunks entries must be strings or objects"
        chunk_id = entry.get("chunk_id") or entry.get("id")
        if not isinstance(chunk_id, str) or not chunk_id:
            return "execution.completed_chunks object entries require chunk_id or id"
    ownership = execution.get("file_ownership")
    if not isinstance(ownership, dict):
        return "execution.file_ownership must be an object"
    for chunk_id, files in ownership.items():
        if (
            not isinstance(chunk_id, str)
            or not isinstance(files, list)
            or any(not isinstance(path, str) or not path for path in files)
        ):
            return "execution.file_ownership must map strings to lists of strings"
    budget = execution.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            return "execution.budget must be an object when present"
        for key in ("commits_since_push", "checkin_interval_pct"):
            value = budget.get(key)
            if value is not None and (type(value) is not int or value < 0):
                return f"execution.budget.{key} must be a nonnegative integer"
        for key in ("started_at", "deadline_at"):
            value = budget.get(key)
            if value is not None and (
                not isinstance(value, str) or _parse_iso(value) is None
            ):
                return f"execution.budget.{key} must be a parseable ISO-8601 string"
    return None


def _abort_invalid_execution(execution: dict, reason: str) -> dict:
    return {
        "decision": "abort",
        "reason": f"invalid schema-v1 execution: {reason}",
        "run_id": execution.get("run_id") if isinstance(execution.get("run_id"), str) else None,
        "remaining_chunks": [],
        "iterate_attempt": 0,
        "concurrent_modifications": [],
        "execution_block": execution,
        "envelopes": {},
        "ownership_verified": False,
    }


def _heartbeat_age(now: datetime, heartbeat: datetime | None) -> timedelta | None:
    if heartbeat is None or heartbeat.tzinfo is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc) - heartbeat.astimezone(timezone.utc)


def _load_envelopes(
    workdir: Path, run_id: str
) -> tuple[dict[str, list[dict]], list[str]]:
    results_root = workdir / ".build-loop" / "subagent-results"
    base = results_root / run_id
    out: dict[str, list[dict]] = {}
    warnings: list[str] = []
    if not base.exists():
        return out, warnings
    if base.is_symlink():
        return out, ["ignored subagent results directory: symlinks are not trusted"]
    try:
        if base.resolve().parent != results_root.resolve():
            return out, ["ignored subagent results directory outside the run-results root"]
    except (OSError, RuntimeError) as exc:
        return out, [f"could not validate subagent results directory: {exc}"]
    try:
        sampled_entries = list(islice(base.iterdir(), MAX_RESULT_FILES + 1))
    except OSError as exc:
        return out, [f"could not read subagent results directory: {exc}"]
    if len(sampled_entries) > MAX_RESULT_FILES:
        warnings.append(
            f"subagent results truncated at {MAX_RESULT_FILES} directory entries"
        )
        sampled_entries = sampled_entries[:MAX_RESULT_FILES]
    entries = sorted(sampled_entries, key=lambda entry: entry.name)
    total_bytes_read = 0
    for fp in entries:
        if not fp.name.endswith(".json"):
            continue
        if fp.is_symlink():
            warnings.append(f"ignored subagent result {fp.name}: symlink")
            continue
        remaining_bytes = MAX_RESULT_TOTAL_BYTES - total_bytes_read
        if remaining_bytes <= 0:
            warnings.append(
                f"subagent result scan stopped at {MAX_RESULT_TOTAL_BYTES} total bytes"
            )
            break
        read_limit = min(MAX_RESULT_FILE_BYTES, remaining_bytes)
        try:
            with fp.open("rb") as handle:
                raw = handle.read(read_limit + 1)
            if len(raw) > read_limit:
                if read_limit < MAX_RESULT_FILE_BYTES:
                    warnings.append(
                        f"subagent result scan stopped at {MAX_RESULT_TOTAL_BYTES} total bytes"
                    )
                    break
                warnings.append(
                    f"ignored subagent result {fp.name}: exceeds {MAX_RESULT_FILE_BYTES} bytes"
                )
                continue
            total_bytes_read += len(raw)
            data = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            warnings.append(f"ignored subagent result {fp.name}: {type(exc).__name__}")
            continue
        if not isinstance(data, dict):
            warnings.append(f"ignored subagent result {fp.name}: expected JSON object")
            continue
        cid = data.get("chunk_id")
        if not isinstance(cid, str) or not cid:
            warnings.append(f"ignored subagent result {fp.name}: invalid chunk_id")
            continue
        if "attempt" in data and type(data.get("attempt")) is not int:
            warnings.append(
                f"subagent result {fp.name} has a non-integer attempt; sorted last"
            )
        out.setdefault(cid, []).append(data)
    for cid in out:
        out[cid].sort(
            key=lambda envelope: (
                0,
                envelope.get("attempt"),
            )
            if type(envelope.get("attempt")) is int
            else (1, str(envelope.get("attempt", "")))
        )
        if len(out[cid]) > MAX_RESULT_ATTEMPTS_PER_CHUNK:
            omitted = len(out[cid]) - MAX_RESULT_ATTEMPTS_PER_CHUNK
            out[cid] = out[cid][-MAX_RESULT_ATTEMPTS_PER_CHUNK:]
            warnings.append(
                f"subagent results for {cid!r} retained the newest "
                f"{MAX_RESULT_ATTEMPTS_PER_CHUNK} attempts and omitted {omitted}"
            )
    return out, warnings


def _parse_iso(s: str) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _execution_identity(row: dict) -> str | None:
    for key in ("build_loop_id", "run_id", "id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _matching_terminal_evidence(state: dict, run_id: str, source: str) -> list[str]:
    """Return durable evidence that *run_id* already reached a terminal state."""
    evidence: list[str] = []
    history = state.get("historicalExecutions")
    for row in history if isinstance(history, list) else []:
        if isinstance(row, dict) and _execution_identity(row) == run_id:
            evidence.append(f"{source}.historicalExecutions contains the run")
            break
    runs = state.get("runs")
    for row in runs if isinstance(runs, list) else []:
        if not isinstance(row, dict):
            continue
        if _execution_identity(row) == run_id and row.get("outcome") == "pass":
            evidence.append(f"{source}.runs records outcome=pass")
            break
    return evidence


def _managed_worktree_path(workdir: Path, raw_path: Any) -> Path | None:
    """Resolve a referenced run worktree only inside Build Loop's managed root."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    try:
        managed_root = (workdir / ".build-loop" / "worktrees").resolve()
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = workdir / candidate
        candidate = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    try:
        relative = candidate.relative_to(managed_root)
    except ValueError:
        return None
    return candidate if relative.parts else None


def _worktree_is_dead(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return True
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False  # Unknown is potentially active: fail closed.
    if result.returncode != 0:
        return True
    try:
        return Path(result.stdout.strip()).resolve() != path.resolve()
    except OSError:
        return False


def _branch_is_absent(workdir: Path, raw_branch: Any) -> bool | None:
    """Return True/False for an absent/present local branch; None when unknown."""
    if not isinstance(raw_branch, str) or not raw_branch.strip():
        return None
    branch = raw_branch.strip()
    try:
        valid = subprocess.run(
            ["git", "check-ref-format", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if valid.returncode != 0:
            return None
        result = subprocess.run(
            ["git", "-C", str(workdir), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    return None


def _classify_legacy_crash(workdir: Path, state: dict, execution: dict) -> dict:
    """Classify a schema-less crash residue without assuming that old means dead."""
    run_id = _execution_identity(execution)
    crashed_at = _parse_iso(execution.get("crashed_at", ""))
    result = {
        "classification": "ambiguous_or_potentially_active",
        "run_id": run_id,
        "archive_safe": False,
        "evidence": [],
    }
    if execution.get("schema_version") is not None or run_id is None or crashed_at is None:
        return result

    heartbeat = _parse_iso(execution.get("last_heartbeat_at", ""))
    if heartbeat is not None:
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        if crashed_at.tzinfo is None:
            crashed_at = crashed_at.replace(tzinfo=timezone.utc)
        if heartbeat > crashed_at:
            return result

    evidence = _matching_terminal_evidence(state, run_id, "state")
    path_value = execution.get("run_worktree_path")
    branch_value = execution.get("run_worktree_branch")
    managed_path = _managed_worktree_path(workdir, path_value)

    if managed_path is not None and managed_path.exists():
        try:
            child_state = _load_state(managed_path)
        except StateReadError:
            child_state = None
        if isinstance(child_state, dict):
            evidence.extend(_matching_terminal_evidence(child_state, run_id, "run_worktree.state"))

    referenced: list[tuple[str, bool | None]] = []
    if path_value:
        referenced.append(("run worktree", _worktree_is_dead(managed_path) if managed_path else None))
    if branch_value:
        referenced.append(("run branch", _branch_is_absent(workdir, branch_value)))
    if referenced and all(is_dead is True for _, is_dead in referenced):
        evidence.append("all referenced run worktree/branch resources are absent or dead")

    if evidence:
        result.update(
            classification="terminal_legacy_crash",
            archive_safe=True,
            evidence=evidence,
        )
    return result


def _archive_legacy_crash(workdir: Path, expected_execution: dict) -> tuple[bool, str]:
    """Atomically archive the exact terminal legacy execution and clear its identity."""
    state_path = workdir / ".build-loop" / "state.json"
    with LockedFile(state_path):
        try:
            state = _load_state(workdir)
        except StateReadError as exc:
            return False, str(exc)
        if not isinstance(state, dict):
            return False, "state.json disappeared or became unreadable before archive"
        execution = state.get("execution")
        if execution != expected_execution:
            return False, "execution changed before archive; refusing to clear a different run"
        if not isinstance(execution, dict):
            return False, "execution is no longer an object"
        classification = _classify_legacy_crash(workdir, state, execution)
        if not classification["archive_safe"]:
            return False, "execution no longer has terminal legacy-crash evidence"
        history = state.get("historicalExecutions")
        if history is not None and not isinstance(history, list):
            return False, "historicalExecutions is not a list; refusing a lossy archive"
        history = list(history or [])
        if not any(row == execution for row in history):
            history.append(dict(execution))
        state["historicalExecutions"] = history[-10:]
        state["execution"] = {}
        encoded = (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        atomic_write_bytes(state_path, encoded)
    return True, "terminal legacy crash archived; execution identity cleared"


def _resolve_latest(state: dict, now: datetime, staleness_minutes: int) -> tuple[str | None, str]:
    """Return (run_id, reason) for --resume latest. None if no resumable run."""
    execution = state.get("execution")
    if not isinstance(execution, dict):
        return None, "no execution block in state.json"
    if execution.get("phase") == "report":
        return None, "last run completed cleanly (phase=report)"
    last = _parse_iso(execution.get("last_heartbeat_at", ""))
    age = _heartbeat_age(now, last)
    if age is None:
        return None, "execution.last_heartbeat_at is missing or unparseable"
    if age < timedelta(minutes=staleness_minutes):
        return None, f"latest run heartbeat is fresh ({age.total_seconds():.0f}s old; threshold {staleness_minutes*60}s)"
    return execution.get("run_id"), f"latest run heartbeat is {age.total_seconds():.0f}s old"


def _files_for_chunk(execution: dict, chunk_id: str) -> list[str]:
    return list(execution.get("file_ownership", {}).get(chunk_id, []))


def _git_unstaged_files(workdir: Path) -> set[str]:
    """Files with unstaged or untracked modifications, relative to workdir."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(workdir), "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    files: set[str] = set()
    for line in out.splitlines():
        if not line.strip():
            continue
        # porcelain: "XY filename" — XY is two-char status; rest is path
        path = line[3:].strip()
        # rename has " -> " separator
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        # strip surrounding quotes git may add
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        files.add(path)
    return files


def _file_mtime(workdir: Path, rel: str) -> datetime | None:
    p = workdir / rel
    if not p.exists():
        return None
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _normalize_completed_chunks(execution: dict) -> tuple[list[dict], list[str]]:
    """Read `completed_chunks` in every shape that exists on disk.

    The canonical writer (`write_run_entry/execstate.py:_mutate_return_chunk`)
    appends ``{"chunk_id", "status", "completed_at"}``. Two other shapes are in
    live state files and both used to reach this module unguarded:

      * bare id strings — ``"c1"`` — which raised AttributeError on `.get`,
        killing the run with a traceback instead of a decision envelope;
      * a legacy dict keyed ``{"id", "sha", "status"}`` whose status vocabulary
        ("completed") is outside EXECUTION_RETURN_STATUSES, so the caller's
        ``status == "fixed"`` test silently skipped the chunk.

    Returns the normalized entries plus a warning per non-canonical entry, so a
    degraded read is reported rather than passing as a clean one.
    """
    normalized: list[dict] = []
    warnings: list[str] = []

    for entry in execution.get("completed_chunks", []):
        if isinstance(entry, str):
            normalized.append({"chunk_id": entry, "status": None, "completed_at": None})
            warnings.append(
                f"completed_chunks entry {entry!r} is a bare id (pre-schema state); "
                "concurrent-modification detection skipped for it"
            )
            continue
        if not isinstance(entry, dict):
            warnings.append(f"completed_chunks entry of type {type(entry).__name__} ignored")
            continue

        cid = entry.get("chunk_id") or entry.get("id")
        if not cid:
            warnings.append("completed_chunks entry has neither 'chunk_id' nor 'id'; ignored")
            continue
        status = entry.get("status")
        if status is not None and status not in EXECUTION_RETURN_STATUSES:
            warnings.append(
                f"completed_chunks entry {cid!r} has status {status!r}, outside the "
                "canonical return statuses; treated as unverified"
            )
        normalized.append({
            "chunk_id": cid,
            "status": status,
            "completed_at": entry.get("completed_at"),
        })

    return normalized, warnings


def _detect_concurrent_modifications(
    workdir: Path,
    execution: dict,
) -> list[dict]:
    """Walk completed_chunks; flag any whose owned files have changed since completed_at.

    Two signals (either triggers):
      1. file appears in `git status --porcelain` (unstaged/untracked)
      2. file mtime > completed_at
    """
    git_dirty = _git_unstaged_files(workdir)
    flagged: list[dict] = []
    for entry in _normalize_completed_chunks(execution)[0]:
        cid = entry.get("chunk_id")
        if not cid or entry.get("status") != "fixed":
            continue
        completed_at = _parse_iso(entry.get("completed_at") or "")
        owned = _files_for_chunk(execution, cid)
        modified: list[str] = []
        for rel in owned:
            if rel in git_dirty:
                modified.append(rel)
                continue
            if completed_at is not None:
                mtime = _file_mtime(workdir, rel)
                if mtime is not None and mtime > completed_at + timedelta(seconds=2):
                    modified.append(rel)
        if modified:
            flagged.append({"chunk_id": cid, "files": modified})
    return flagged


def _compute_remaining(
    execution: dict,
    envelopes: dict[str, list[dict]],
    concurrent_mods: list[dict],
) -> list[dict]:
    """remaining = queued + in_flight (no envelope or non-fixed envelope) + concurrent-mod-demoted."""
    flagged_ids = {m["chunk_id"] for m in concurrent_mods}
    remaining: list[dict] = []

    for cid in execution.get("queued_chunks", []):
        remaining.append({
            "chunk_id": cid,
            "files": _files_for_chunk(execution, cid),
            "prior_status": None,
            "reason": "queued",
        })

    for cid in execution.get("in_flight_chunks", []):
        envs = envelopes.get(cid, [])
        latest_status = envs[-1].get("status") if envs else None
        if latest_status == "fixed":
            continue
        remaining.append({
            "chunk_id": cid,
            "files": _files_for_chunk(execution, cid),
            "prior_status": latest_status,
            "reason": "in_flight_no_clean_return",
        })

    for entry in _normalize_completed_chunks(execution)[0]:
        cid = entry.get("chunk_id")
        if cid in flagged_ids:
            remaining.append({
                "chunk_id": cid,
                "files": _files_for_chunk(execution, cid),
                "prior_status": "concurrent_modification_detected",
                "reason": "completed_then_hand_modified",
            })
    return remaining


def _resume_envelope(workdir: Path, execution: dict, run_id: str, reason: str) -> dict:
    envelopes, envelope_warnings = _load_envelopes(workdir, run_id)
    concurrent_mods = _detect_concurrent_modifications(workdir, execution)
    remaining = _compute_remaining(execution, envelopes, concurrent_mods)
    return {
        "decision": "resume",
        "reason": f"{reason} with {len(remaining)} chunk(s) remaining",
        "run_id": run_id,
        "remaining_chunks": remaining,
        "iterate_attempt": int(execution.get("iterate_attempt", 0)),
        "concurrent_modifications": concurrent_mods,
        "execution_block": execution,
        "envelopes": envelopes,
        "budget_resume": _resolve_budget_on_resume(execution),
        "state_warnings": [
            *_normalize_completed_chunks(execution)[1],
            *envelope_warnings,
        ],
    }


def resolve(
    workdir: Path,
    resume_arg: str,
    *,
    staleness_minutes: int = 5,
    now: datetime | None = None,
    archive_terminal_legacy_crash: bool = False,
    current_session_id: str | None = None,
) -> dict:
    """Top-level resolver. Returns the decision envelope (see module docstring)."""
    now = now or datetime.now(timezone.utc)
    try:
        state = _load_state(workdir)
    except StateReadError as exc:
        return _abort_state_read(str(exc))
    if state is None:
        return {
            "decision": "fresh" if not resume_arg and not archive_terminal_legacy_crash else "abort",
            "reason": "no .build-loop/state.json present",
            "run_id": None,
            "remaining_chunks": [],
            "iterate_attempt": 0,
            "concurrent_modifications": [],
            "execution_block": None,
            "envelopes": {},
        }

    execution = state.get("execution") if isinstance(state, dict) else None

    if archive_terminal_legacy_crash and resume_arg:
        return {
            "decision": "abort",
            "reason": "--archive-terminal-legacy-crash cannot be combined with --resume-arg",
            "run_id": None, "remaining_chunks": [], "iterate_attempt": 0,
            "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
        }

    # Schema compatibility is checked BEFORE either branch. It used to be
    # checked only on the --resume path, so the no-argument path could hand the
    # user "resume with --resume <run-id>" and that exact command would then
    # abort on an incompatible schema. Advice the tool's own next step refuses
    # is worse than no advice.
    if isinstance(execution, dict) and execution and execution.get("phase") != "report" \
            and execution.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        legacy = _classify_legacy_crash(workdir, state, execution)
        if legacy["archive_safe"] and not resume_arg:
            if archive_terminal_legacy_crash:
                archived, archive_reason = _archive_legacy_crash(workdir, execution)
                if not archived:
                    return {
                        "decision": "abort", "reason": archive_reason, "run_id": legacy["run_id"],
                        "remaining_chunks": [], "iterate_attempt": 0,
                        "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
                        "legacy_crash": legacy, "archive_applied": False, "fresh_ready": False,
                    }
                return {
                    "decision": "fresh", "reason": archive_reason, "run_id": None,
                    "remaining_chunks": [], "iterate_attempt": 0,
                    "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
                    "legacy_crash": legacy, "required_action": None,
                    "archive_applied": True, "fresh_ready": True,
                }
            return {
                "decision": "abort",
                "reason": "terminal schema-less crash residue detected; archive is required before starting fresh",
                "run_id": legacy["run_id"], "remaining_chunks": [], "iterate_attempt": 0,
                "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
                "legacy_crash": legacy, "required_action": "archive_legacy_crash",
                "archive_flag": "--archive-terminal-legacy-crash",
                "archive_applied": False, "fresh_ready": False,
            }
        return {
            "decision": "abort",
            "reason": f"incompatible schema_version {execution.get('schema_version')!r} "
                      f"(expected {EXPECTED_SCHEMA_VERSION}); execution is ambiguous or potentially active",
            "run_id": None, "remaining_chunks": [], "iterate_attempt": 0,
            "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
            "legacy_crash": legacy,
        }

    if archive_terminal_legacy_crash:
        return {
            "decision": "abort", "reason": "no terminal schema-less crash residue to archive",
            "run_id": None, "remaining_chunks": [], "iterate_attempt": 0,
            "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
        }

    if (
        isinstance(execution, dict)
        and execution
        and execution.get("schema_version") == EXPECTED_SCHEMA_VERSION
    ):
        structural_error = _validate_execution_v1(execution)
        if structural_error:
            return _abort_invalid_execution(execution, structural_error)

    # No --resume: surface heartbeat staleness check (M4 primary signal).
    if not resume_arg:
        if execution is not None and not isinstance(execution, dict):
            return {
                "decision": "abort", "reason": "state.execution must be a JSON object",
                "run_id": None, "remaining_chunks": [], "iterate_attempt": 0,
                "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
            }
        if not execution or execution.get("phase") == "report":
            return {
                "decision": "fresh", "reason": "no incomplete run", "run_id": None,
                "remaining_chunks": [], "iterate_attempt": 0,
                "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
            }
        run_id = execution.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            return {
                "decision": "abort", "reason": "nonterminal execution has no resumable run_id",
                "run_id": None, "remaining_chunks": [], "iterate_attempt": 0,
                "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
                "ownership_verified": False,
            }
        if _same_session_continuity(execution, current_session_id):
            result = _resume_envelope(
                workdir,
                execution,
                run_id,
                "current session matches the nonterminal execution; continue that run",
            )
            result.update(session_continuity_verified=True, ownership_verified=False)
            return result
        last = _parse_iso(execution.get("last_heartbeat_at", ""))
        age = _heartbeat_age(now, last)
        if age is None:
            return {
                "decision": "abort",
                "reason": "nonterminal execution heartbeat is missing or unparseable; ownership is unproven",
                "run_id": run_id,
                "remaining_chunks": [], "iterate_attempt": 0,
                "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
                "ownership_verified": False,
            }
        if age < timedelta(minutes=staleness_minutes):
            return {
                "decision": "abort",
                "reason": f"nonterminal execution heartbeat is fresh ({age.total_seconds():.0f}s old); "
                          "current-invocation ownership is unproven",
                "run_id": run_id, "remaining_chunks": [],
                "iterate_attempt": int(execution.get("iterate_attempt", 0)),
                "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
                "ownership_verified": False,
            }
        return {
            "decision": "prompt_user",
            "reason": f"incomplete build detected (run_id={run_id}, "
                      f"last heartbeat {age.total_seconds()/60:.1f} min ago); "
                      f"resume with --resume {run_id} or start fresh",
            "run_id": run_id,
            "remaining_chunks": [],  # caller re-runs us with the literal run_id to compute
            "iterate_attempt": int(execution.get("iterate_attempt", 0)),
            "concurrent_modifications": [],
            "execution_block": execution,
            "envelopes": {},
            "ownership_verified": False,
        }

    # --resume present.
    if resume_arg == "latest":
        run_id, reason = _resolve_latest(state, now, staleness_minutes)
        if run_id is None:
            return {
                "decision": "abort", "reason": f"--resume latest: {reason}", "run_id": None,
                "remaining_chunks": [], "iterate_attempt": 0,
                "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
            }
        resume_arg = run_id

    if not isinstance(execution, dict) or not execution:
        return {
            "decision": "abort", "reason": "no execution block to resume from",
            "run_id": None, "remaining_chunks": [], "iterate_attempt": 0,
            "concurrent_modifications": [], "execution_block": None, "envelopes": {},
        }
    if execution.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        return {
            "decision": "abort",
            "reason": f"incompatible schema_version {execution.get('schema_version')!r} "
                      f"(expected {EXPECTED_SCHEMA_VERSION}); upgrade build-loop or start fresh",
            "run_id": None, "remaining_chunks": [], "iterate_attempt": 0,
            "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
        }
    if execution.get("run_id") != resume_arg:
        return {
            "decision": "abort",
            "reason": f"--resume {resume_arg!r} does not match active run_id {execution.get('run_id')!r}",
            "run_id": None, "remaining_chunks": [], "iterate_attempt": 0,
            "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
        }
    if execution.get("phase") == "report":
        return {
            "decision": "abort", "reason": "run is already complete (phase=report); nothing to resume",
            "run_id": resume_arg, "remaining_chunks": [], "iterate_attempt": 0,
            "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
        }

    return _resume_envelope(
        workdir,
        execution,
        resume_arg,
        f"resuming {resume_arg} at phase={execution.get('phase')}",
    )


def _resolve_budget_on_resume(execution: dict) -> dict | None:
    """Return the budget block to reuse on resume (plan §14.4 + §14.5).

    Contract: when an autonomous-mode budget block is present, the resumed
    orchestrator MUST preserve the original `deadline_at` so a 2h budget that
    crashed at 1h59m does NOT get a fresh 2h. The caller (orchestrator) writes
    the budget back into state.execution.budget on resume init using exactly
    these fields.

    Returns None when no budget block exists (resume happened in a build that
    pre-dates autonomous mode — orchestrator treats as classic single-pass).

    The decision to preserve vs reset lives HERE, not in the orchestrator,
    so that the contract is single-sourced and testable.
    """
    budget = execution.get("budget")
    if not isinstance(budget, dict):
        return None
    started_at = budget.get("started_at")
    deadline_at = budget.get("deadline_at")
    if not (isinstance(started_at, str) and isinstance(deadline_at, str)):
        return None
    return {
        "preserve_deadline": True,
        "mode": budget.get("mode", "default"),
        "started_at": started_at,
        "deadline_at": deadline_at,             # MUST NOT be recomputed on resume
        "last_checkin_at": budget.get("last_checkin_at"),
        "commits_since_push": int(budget.get("commits_since_push") or 0),
        "checkin_interval_pct": int(budget.get("checkin_interval_pct") or 50),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Resume resolver for /build-loop:run --resume (M3).")
    p.add_argument("--workdir", required=True)
    p.add_argument("--resume-arg", default="", help="Literal run_id, 'latest', or '' for no-resume staleness check")
    p.add_argument("--staleness-minutes", type=int, default=5)
    p.add_argument(
        "--current-session-id",
        help="Explicit host session id; an exact match continues, never replaces, its execution",
    )
    p.add_argument(
        "--archive-terminal-legacy-crash",
        action="store_true",
        help="Atomically archive a proven terminal schema-less crash before starting fresh",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        env = resolve(
            Path(args.workdir).resolve(),
            args.resume_arg,
            staleness_minutes=args.staleness_minutes,
            archive_terminal_legacy_crash=args.archive_terminal_legacy_crash,
            current_session_id=args.current_session_id,
        )
    except OSError as e:
        print(f"filesystem error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(env, indent=2))
    return 0 if env["decision"] in {"resume", "fresh", "prompt_user"} else 1


if __name__ == "__main__":
    sys.exit(main())
