# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Stable per-build-loop-run identity for Rally Point records.

Empirical motivation: 37% of channel records were arriving with
``run_id=unknown`` because callers had no stable per-run handle. With
multiple concurrent build-loop runs across Claude + Codex + CI, that
fraction made cross-run attribution unreliable.

This module owns the **run-instance identity** axis. It is orthogonal to
``producer_metadata`` (which captures the *runtime* identity — what
code/version is producing) — both can be attached to a single record
without overlap.

Canonical shape (top-level on rally records, NOT nested under
``producer_metadata``)::

    record["build_loop_id"]         = "bl-<TS>-<tool>-<6digit>"
    record["build_loop_started_at"] = "<ISO8601 UTC>"
    record["build_loop_run_label"]  = "<tool>#<6digit> <ISO8601 UTC>"

State persistence (``<workdir>/.build-loop/state.json``)::

    state.execution.build_loop_id            (str, format frozen above)
    state.execution.started_at               (ISO8601 UTC)
    state.execution.started_by_tool          (str)
    state.execution.started_by_session_id    (str, immutable post-generation)
    state.execution.current_session_id       (str, mutable on resume)
    state.execution.run_label                (str, human-readable)

API
---

``generate_or_resume(workdir, tool, session_id)`` — Phase 1 Assess entry
point. Reads state, generates a fresh id when absent (collision-guarded),
or preserves the existing id and refreshes ``current_session_id`` on
resume. Returns the full execution block as a dict.

``rally_fields_for(workdir)`` — writer-time entry point. Returns the
three top-level fields ready to merge into a rally record. Returns an
empty dict when state.json or the execution block is missing (writes
proceed without the fields — graceful degradation, never blocks).

Fire-and-forget on every read failure. The substrate must not crash a
host action because a state.json was malformed.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
from pathlib import Path
from typing import Any

STATE_REL = Path(".build-loop") / "state.json"
RUNS_REL = Path(".build-loop") / "runs"

_MAX_COLLISION_ATTEMPTS = 5


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _ts_compact(now: _dt.datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


def _ts_iso(now: _dt.datetime) -> str:
    return now.isoformat().replace("+00:00", "Z")


def _suffix() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _state_path(workdir: Path) -> Path:
    return Path(workdir) / STATE_REL


def _runs_dir(workdir: Path) -> Path:
    return Path(workdir) / RUNS_REL


def _read_state(workdir: Path) -> dict[str, Any]:
    try:
        return json.loads(_state_path(workdir).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}


def _atomic_write_state(workdir: Path, state: dict[str, Any]) -> bool:
    """Atomic tmp+rename. Returns False on any IO failure (never raises)."""
    try:
        target = _state_path(workdir)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".{target.name}.tmp.{os.getpid()}"
        tmp.write_text(
            json.dumps(state, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(target))
        return True
    except (OSError, TypeError, ValueError):
        return False


def _candidate_id(tool: str, now: _dt.datetime) -> str:
    return f"bl-{_ts_compact(now)}-{(tool or 'unknown')}-{_suffix()}"


def _new_unique_id(
    workdir: Path, tool: str, now: _dt.datetime
) -> str:
    """Generate a fresh id, retrying up to _MAX_COLLISION_ATTEMPTS times.

    The collision domain is the ``.build-loop/runs/<id>`` directory: if a
    directory of that name already exists we treat the suffix as taken
    and retry. The window is process-local (no cross-process lock) but
    six-digit random space + monotonic timestamps make the practical
    collision probability vanishing.
    """
    attempts = 0
    while True:
        candidate = _candidate_id(tool, now)
        if not _runs_dir(workdir).joinpath(candidate).exists():
            return candidate
        attempts += 1
        if attempts >= _MAX_COLLISION_ATTEMPTS:
            # Last attempt — return it anyway. Two runs holding the same
            # id is recoverable (records carry session_id + tool); a
            # blocking loop is not.
            return candidate


def _run_label(tool: str, build_loop_id: str, started_at_iso: str) -> str:
    # Format: "<tool>#<6digit> <ISO>"
    # 6-digit suffix is the trailing token of the id.
    try:
        suffix = build_loop_id.rsplit("-", 1)[-1]
    except Exception:
        suffix = "000000"
    return f"{tool or 'unknown'}#{suffix} {started_at_iso}"


def generate_or_resume(
    workdir: Path | str,
    *,
    tool: str,
    session_id: str,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """Return the execution block for ``workdir``.

    Behaviour:
      - When ``state.execution.build_loop_id`` is absent: generate a
        fresh id, set immutable fields (build_loop_id, started_at,
        started_by_tool, started_by_session_id, run_label) once,
        ``current_session_id = session_id``, create the runs dir,
        persist state.json, return the execution block.
      - When it is present: preserve every immutable field exactly,
        update ONLY ``current_session_id`` to the new ``session_id``,
        persist state.json, return the execution block.

    Fire-and-forget on persistence: if the state write fails the
    in-memory dict is still returned so the caller can attach rally
    fields for this run.
    """
    workdir_path = Path(workdir)
    state = _read_state(workdir_path)
    execution = dict(state.get("execution") or {})

    if execution.get("build_loop_id"):
        # Resume path: ONLY current_session_id mutates.
        execution["current_session_id"] = session_id
        state["execution"] = execution
        _atomic_write_state(workdir_path, state)
        return execution

    # Fresh-generate path.
    when = now or _now_utc()
    started_at_iso = _ts_iso(when)
    build_loop_id = _new_unique_id(workdir_path, tool, when)
    execution.update(
        {
            "build_loop_id": build_loop_id,
            "started_at": started_at_iso,
            "started_by_tool": tool or "unknown",
            "started_by_session_id": session_id,
            "current_session_id": session_id,
            "run_label": _run_label(tool, build_loop_id, started_at_iso),
        }
    )
    # Reserve the runs directory so a future collision check sees it.
    try:
        _runs_dir(workdir_path).joinpath(build_loop_id).mkdir(
            parents=True, exist_ok=True
        )
    except OSError:
        pass
    state["execution"] = execution
    _atomic_write_state(workdir_path, state)
    return execution


def rally_fields_for(workdir: Path | str | None) -> dict[str, Any]:
    """Return the three top-level rally-record fields, or ``{}`` if absent.

    Callers merge into the outgoing record AFTER ``producer_metadata`` so
    that an existing run's identity overrides nothing the producer layer
    sets. Keys deliberately distinct from any ``producer_*`` field.
    """
    if workdir is None:
        return {}
    state = _read_state(Path(workdir))
    execution = state.get("execution") or {}
    bid = execution.get("build_loop_id")
    if not bid:
        return {}
    return {
        "build_loop_id": bid,
        "build_loop_started_at": execution.get("started_at"),
        "build_loop_run_label": execution.get("run_label"),
    }
