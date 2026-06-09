#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""execstate.py — M2 execution-state heartbeat update for write_run_entry."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

# Flat intra-package imports (package dir on sys.path via __init__.py)
from atomic_io import LockedFile, atomic_write_bytes  # type: ignore  # noqa: E402,F401
from iohelpers import CorruptStateError, read_json  # type: ignore  # noqa: E402

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
    "heartbeat",        # pure liveness refresh — touch last_heartbeat_at, no state change
}
EXECUTION_RETURN_STATUSES = {
    "fixed", "partial", "scope_breach", "deferred_architecture",
    "evidence_stale", "plan_malformed", "needs_dependency", "failed",
    "concurrent_modification_detected",
}


def _encode(state: Any) -> bytes:
    return (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Per-action helpers (each linear/flat)
# ---------------------------------------------------------------------------

def _build_start_block(
    run_id: str | None,
    queued_chunks: list[str] | None,
    file_ownership: dict[str, list[str]] | None,
    ts: str,
) -> dict:
    if not run_id or not isinstance(run_id, str):
        raise ValueError("action='start' requires run_id")
    if queued_chunks is None or not isinstance(queued_chunks, list):
        raise ValueError("action='start' requires queued_chunks: list[str]")
    if file_ownership is None or not isinstance(file_ownership, dict):
        raise ValueError("action='start' requires file_ownership: dict[str, list[str]]")
    return {
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


def _mutate_dispatch_chunk(execution: dict, chunk_id: str | None) -> None:
    if not chunk_id:
        raise ValueError("action='dispatch_chunk' requires chunk_id")
    if chunk_id in execution.get("queued_chunks", []):
        execution["queued_chunks"].remove(chunk_id)
    if chunk_id not in execution.setdefault("in_flight_chunks", []):
        execution["in_flight_chunks"].append(chunk_id)


def _mutate_return_chunk(execution: dict, chunk_id: str | None, status: str | None, ts: str) -> None:
    if not chunk_id:
        raise ValueError("action='return_chunk' requires chunk_id")
    if status not in EXECUTION_RETURN_STATUSES:
        raise ValueError(f"status must be one of {sorted(EXECUTION_RETURN_STATUSES)}, got {status!r}")
    if chunk_id in execution.get("in_flight_chunks", []):
        execution["in_flight_chunks"].remove(chunk_id)
    execution.setdefault("completed_chunks", []).append(
        {"chunk_id": chunk_id, "status": status, "completed_at": ts}
    )


def _mutate_phase_transition(execution: dict, phase: str | None) -> None:
    if phase not in EXECUTION_VALID_PHASES:
        raise ValueError(f"phase must be one of {sorted(EXECUTION_VALID_PHASES)}, got {phase!r}")
    execution["phase"] = phase


def _dispatch_action(action: str, execution: Any, chunk_id: str | None, status: str | None, phase: str | None, ts: str) -> dict:
    """Apply a non-start, non-review_e_pass action. Returns the mutated execution block."""
    if not isinstance(execution, dict):
        raise ValueError(f"action={action!r} requires an existing execution block (run start first)")
    _ACTION_TABLE[action](execution, chunk_id, status, phase, ts)
    return execution


def _noop_iterate(execution: dict, _chunk: object, _status: object, _phase: object, _ts: str) -> None:
    execution["iterate_attempt"] = int(execution.get("iterate_attempt", 0)) + 1


def _noop_complete(execution: dict, _chunk: object, _status: object, _phase: object, _ts: str) -> None:
    execution["phase"] = "report"


def _noop_heartbeat(execution: dict, _chunk: object, _status: object, _phase: object, _ts: str) -> None:
    # Pure liveness: the shared tail of update_execution_state refreshes
    # last_heartbeat_at; this action mutates nothing else. Used by the
    # orchestrator-heartbeat wrapper at phase/commit boundaries on long runs so a
    # watcher can see the run is alive even between the six M2 trigger points.
    return None


def _wrap_dispatch_chunk(execution: dict, chunk_id: object, _status: object, _phase: object, _ts: str) -> None:
    _mutate_dispatch_chunk(execution, chunk_id)  # type: ignore[arg-type]


def _wrap_return_chunk(execution: dict, chunk_id: object, status: object, _phase: object, ts: str) -> None:
    _mutate_return_chunk(execution, chunk_id, status, ts)  # type: ignore[arg-type]


def _wrap_phase_transition(execution: dict, _chunk: object, _status: object, phase: object, _ts: str) -> None:
    _mutate_phase_transition(execution, phase)  # type: ignore[arg-type]


_ACTION_TABLE: dict = {
    "dispatch_chunk": _wrap_dispatch_chunk,
    "return_chunk": _wrap_return_chunk,
    "phase_transition": _wrap_phase_transition,
    "iterate_attempt": _noop_iterate,
    "complete": _noop_complete,
    "heartbeat": _noop_heartbeat,
}


# ---------------------------------------------------------------------------
# review_e_pass: independent of the heartbeat block — owns its own locked write
# ---------------------------------------------------------------------------

def _write_review_e_pass(
    state_path: Path,
    files_scanned: list[str] | None,
    is_final: bool | None,
) -> dict:
    if files_scanned is None or not isinstance(files_scanned, list):
        raise ValueError("action='review_e_pass' requires files_scanned: list[str]")
    if not isinstance(is_final, bool):
        raise ValueError("action='review_e_pass' requires is_final: bool")
    with LockedFile(state_path):
        state = read_json(state_path) or {}
        if not isinstance(state, dict):
            raise ValueError(f"{state_path} is not a JSON object at top level")
        review_e = state.get("reviewE")
        if review_e is None:
            review_e = []
        elif not isinstance(review_e, list):
            raise ValueError(
                f"{state_path}.reviewE exists but is not a list (got {type(review_e).__name__})"
            )
        review_e.append({"pass_idx": len(review_e), "files_scanned": list(files_scanned), "is_final": is_final})
        state["reviewE"] = review_e
        atomic_write_bytes(state_path, _encode(state))
    return {"reviewE": review_e}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

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

    # review_e_pass is independent: owns its own lock + write and returns early.
    if action == "review_e_pass":
        return _write_review_e_pass(state_path, files_scanned, is_final)

    from idtime import iso_utc  # type: ignore  # flat local import; idtime has no deps
    ts = iso_utc(now)

    with LockedFile(state_path):
        state = read_json(state_path) or {}
        if not isinstance(state, dict):
            raise ValueError(f"{state_path} is not a JSON object at top level")
        execution = state.get("execution")
        if execution is not None and not isinstance(execution, dict):
            raise ValueError(
                f"{state_path}.execution exists but is not an object "
                f"(got {type(execution).__name__})"
            )
        if action == "start":
            execution = _build_start_block(run_id, queued_chunks, file_ownership, ts)
        else:
            execution = _dispatch_action(action, execution, chunk_id, status, phase, ts)
        execution["last_heartbeat_at"] = ts
        state["execution"] = execution
        atomic_write_bytes(state_path, _encode(state))
    return execution
