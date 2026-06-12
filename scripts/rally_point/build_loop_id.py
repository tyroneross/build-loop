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
    state.execution.run_worktree_path        (str, abs path; set when isolation provisioned)
    state.execution.run_worktree_branch      (str, e.g. "bl/run-<id>"; set when isolation provisioned)

API
---

``generate_or_resume(workdir, tool, session_id, *, provision_worktree, base)``
    — Phase 1 Assess entry point. Reads state, generates a fresh id when
    absent (collision-guarded), or preserves the existing id and refreshes
    ``current_session_id`` on resume. When ``provision_worktree=True`` and
    this is a FRESH generate, also creates a dedicated git worktree under
    ``.build-loop/worktrees/run-<short>/`` on branch ``bl/run-<short>`` and
    records the path in ``state.execution.run_worktree_path``. On resume,
    the existing worktree path is preserved without re-creating. Raises
    :class:`RunWorktreeProvisionError` when provisioning is requested but
    fails — the orchestrator MUST abort rather than fall back to the
    canonical checkout.

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


class RunWorktreeProvisionError(RuntimeError):
    """Raised when run-worktree isolation is requested but cannot be provisioned.

    Fail-closed by design: the orchestrator MUST abort the run rather than
    silently fall back to the canonical checkout (which is the very class of
    leak this feature exists to prevent — see SPEC-run-worktree-isolation.md).
    """


def _short_run_suffix(build_loop_id: str) -> str:
    """Return the 6-digit numeric suffix of a build_loop_id (for short slugs)."""
    try:
        return build_loop_id.rsplit("-", 1)[-1]
    except Exception:
        return "000000"


def _provision_run_worktree(
    workdir_path: Path,
    build_loop_id: str,
    base: str,
) -> tuple[str, str]:
    """Create a guarded run worktree at ``.build-loop/worktrees/run-<short>/``.

    Returns ``(absolute_path, branch_name)``. Raises
    :class:`RunWorktreeProvisionError` on any failure — the run MUST abort
    rather than continue in the canonical checkout.

    Uses ``scripts/worktree_guard.create_guarded_worktree`` so the resulting
    worktree obeys the same canonical-root and branch-prefix rules as every
    other build-loop worktree. ``record=False`` keeps this provisioning step
    out of ``runs[N].createdRefs[]`` (no runs entry exists yet at Phase 1
    Assess preamble — write_run_entry fires at Review-G); the path is
    persisted to ``state.execution`` instead and ``collapse_run.py`` knows to
    merge it into the closeout ref set.
    """
    # Late, narrow import — keeps the build_loop_id module free of the
    # worktree_guard dependency for callers that don't request provisioning
    # (e.g. test fixtures that only exercise rally identity).
    import importlib.util
    import sys as _sys

    here = Path(__file__).resolve().parent
    scripts_dir = here.parent  # rally_point/ → scripts/
    if str(scripts_dir) not in _sys.path:
        _sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location(
        "worktree_guard", scripts_dir / "worktree_guard.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover — defensive
        raise RunWorktreeProvisionError(
            "could not import worktree_guard.py from scripts/"
        )
    wg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wg)  # type: ignore[arg-type]

    short = _short_run_suffix(build_loop_id)
    slug = f"run-{short}"
    branch = f"bl/run-{short}"
    result = wg.create_guarded_worktree(
        workdir=workdir_path,
        slug=slug,
        branch=branch,
        base=base,
        record=False,  # not in runs[].createdRefs[] yet — see docstring above
        purpose=f"build-loop run-entry isolation for {build_loop_id}",
        close_criteria=[
            f"{branch} is merged into {base} (or surfaced for operator)",
            "worktree folder is removed from .build-loop/worktrees",
            f"build_loop_id {build_loop_id} run reached closeout",
        ],
    )
    if not result.get("created"):
        raise RunWorktreeProvisionError(
            f"create_guarded_worktree failed for run {build_loop_id}: "
            f"{result.get('error') or 'unknown error'}"
        )
    return str(Path(result["path"]).resolve()), str(result["branch"])


def generate_or_resume(
    workdir: Path | str,
    *,
    tool: str,
    session_id: str,
    now: _dt.datetime | None = None,
    provision_worktree: bool = False,
    base: str = "main",
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

    CONTRACT: the production build-loop run entry (Phase 1 Assess preamble in
    ``agents/build-orchestrator.md`` + ``skills/build-loop/references/phase-1-assess.md``)
    MUST pass ``provision_worktree=True`` so every run is isolated. The ``False``
    default exists ONLY for identity-only callers (status checks, tests, demos)
    that legitimately must not provision a worktree. See
    ``docs/SPEC-run-worktree-isolation.md``.

    When ``provision_worktree=True``:
      - Fresh-generate: create ``.build-loop/worktrees/run-<short>/`` on
        ``bl/run-<short>`` off ``base`` (default ``main``); persist the
        absolute path to ``state.execution.run_worktree_path`` and the
        branch to ``state.execution.run_worktree_branch``. Raises
        :class:`RunWorktreeProvisionError` on failure (fail-closed).
      - Resume: preserve the existing path verbatim — never re-create.
        When ``run_worktree_path`` is missing but the run is resumed
        (existing build_loop_id), this is a state-corruption case; we
        leave the field unset and let the orchestrator decide (the
        contract is "fresh-generate provisions, resume preserves").

    Fire-and-forget on persistence: if the state write fails the
    in-memory dict is still returned so the caller can attach rally
    fields for this run. Worktree provisioning, by contrast, is
    fail-closed.
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
    # A fresh run must not inherit the PREVIOUS run's per-run assessment state:
    # stale `phase: done` would let the Stop-hook closeout record a crashed new
    # run as `pass`, and stale `triggers` would attribute the old run's stakes
    # to the new one. Phase 1 Assess re-writes both for the new run.
    for stale_key in ("phase", "triggers"):
        state.pop(stale_key, None)
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

    # Run-entry worktree provisioning (Phase 1a — SPEC-run-worktree-isolation).
    # Fail-closed: any failure aborts the run rather than leaking work to the
    # canonical checkout. State is still persisted (with execution.build_loop_id
    # set) so the orchestrator's diagnostics see what happened.
    if provision_worktree:
        try:
            wt_path, wt_branch = _provision_run_worktree(
                workdir_path, build_loop_id, base
            )
            execution["run_worktree_path"] = wt_path
            execution["run_worktree_branch"] = wt_branch
        except RunWorktreeProvisionError:
            state["execution"] = execution
            _atomic_write_state(workdir_path, state)
            raise

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
