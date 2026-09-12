#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""io.py — JSON read/write primitives for write_run_entry."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Flat intra-package import (works when package dir is on sys.path, as set by __init__.py)
from atomic_io import LockedFile, atomic_write_bytes  # type: ignore  # noqa: E402,F401


# A run's row is written by MORE THAN ONE process: write_run_entry (Review-G),
# append_run (the Stop hook), scripts/learn/runner.py (`learn`), and
# stop_closeout.py (`auditor_status`, `branch_closeout`, `createdRefs`,
# `provenance`). An allowlist of keys to carry forward can only ever enumerate
# what THIS writer knows about, so it silently deletes every other writer's
# evidence on a correction. The merge therefore starts from the existing row and
# lets the incoming entry overwrite it, which preserves position, lets a
# correction win on every field it carries, and cannot lose a key this module
# has never heard of. A field that must be REMOVED is removed explicitly, never
# by omission.


# Keys that identify the WRITER rather than the run. `source: append_run` marks a
# thin Stop-hook record, and append_run.py and stop_closeout.py both refuse to
# overwrite a row whose source is anything else — so a Review-G write that kept
# the label would invite the Stop hook to clobber a record it must not.
# run_close_lint reads a SECOND writer signature that this merge cannot change:
# a `hook_` run-id prefix marks scripts/audit_before_commit.py, and run_id is the
# merge key, so a Review-G write over a `hook_*` row still reads as floor-grade.
# Dropping `source` is necessary, not sufficient.
_WRITER_OWNED_KEYS = ("source",)


def upsert_merge(existing: dict, entry: dict, defaulted: set[str] | None = None) -> dict:
    """Build the replacement row for an existing run_id.

    `defaulted` names fields the caller filled with an EMPTY default rather than
    a supplied value. A CLI cannot otherwise tell "not supplied this pass" from
    "deliberately empty", and the difference decides whether a correction that
    restates only --goal wipes the run's filesTouched, phases, and the auditor
    verdict scoped to them. An empty default never overwrites a non-empty
    recorded value; an explicitly supplied empty value still does.

    Returns a new dict — neither argument is mutated, so callers keep their own
    entry intact.
    """
    defaulted = defaulted or set()
    merged = dict(existing)
    for key, value in entry.items():
        if key in defaulted and not value and existing.get(key):
            continue
        merged[key] = value
    for key in _WRITER_OWNED_KEYS:
        if key not in entry:
            merged.pop(key, None)
    # A judge verdict is scoped to the file set it was rendered against. A
    # correction that CHANGES filesTouched and supplies no new verdict would
    # otherwise re-attribute the old one to files no judge ever saw, so the
    # stale verdict is dropped rather than silently re-scoped. A correction that
    # merely does not restate the file set has changed nothing and keeps it.
    if (
        "judge_decisions" not in entry
        and "filesTouched" not in defaulted
        and "filesTouched" in entry
        and entry.get("filesTouched") != existing.get("filesTouched")
    ):
        merged.pop("judge_decisions", None)
    return merged


def dedupe_runs(runs: list) -> tuple[list, list[str]]:
    """Collapse duplicate run_id rows, keeping each id at its FIRST position.

    Later rows for the same run_id win on value (they are the corrections) while
    the earliest row's index is preserved, so ledger ordering does not shuffle.
    Returns (deduped_rows, duplicate_run_ids).
    """
    index_of: dict[str, int] = {}
    out: list = []
    duplicates: list[str] = []
    for row in runs:
        run_id = row.get("run_id") if isinstance(row, dict) else None
        if not isinstance(run_id, str) or not run_id:
            out.append(row)
            continue
        if run_id in index_of:
            i = index_of[run_id]
            prior = out[i]
            out[i] = upsert_merge(prior, row) if isinstance(prior, dict) else row
            if run_id not in duplicates:
                duplicates.append(run_id)
            continue
        index_of[run_id] = len(out)
        out.append(row)
    return out, duplicates


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


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


def _encode(state: Any) -> bytes:
    return (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def append_run_entry(state_path: Path, entry: dict, defaulted: set[str] | None = None) -> None:
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
        # UPSERT on run_id: one run_id owns exactly one row. A prior row may be a
        # thin Stop-hook record (source: append_run, written before this Review-G
        # write) or an earlier Review-G record this write is CORRECTING. Either
        # way, replace it in place at its original index. Blind-appending a second
        # row double-counts the run for every consumer that aggregates over runs[]
        # — Phase 6 Learn's sample counter and recurring-pattern-detector's 3-run
        # threshold — and lets judgment_gate resolve the stale row.
        run_id = entry.get("run_id")
        if run_id:
            matches = [
                i for i, r in enumerate(runs)
                if isinstance(r, dict) and r.get("run_id") == run_id
            ]
            if matches:
                # Replace at the FIRST match and drop the rest. A ledger written
                # before this fix can already hold two rows for one run_id;
                # returning on the first match would correct one and leave the
                # stale twin, so the writer heals what it finds rather than
                # waiting for dedupe_run_ledger.py to be remembered.
                first = matches[0]
                runs[first] = upsert_merge(runs[first], entry, defaulted)
                for i in reversed(matches[1:]):
                    del runs[i]
                atomic_write_bytes(state_path, _encode(state))
                return
        runs.append(entry)
        atomic_write_bytes(state_path, _encode(state))


def append_experiment_row(jsonl_path: Path, row: dict) -> None:
    with LockedFile(jsonl_path):
        existing = jsonl_path.read_bytes() if jsonl_path.exists() else b""
        line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        atomic_write_bytes(jsonl_path, existing + line)


def _co_applied(all_names: list[str], exclude: str) -> list[str]:
    """Return names with one entry excluded — O(n), called per artifact."""
    return [n for n in all_names if n != exclude]


def append_experiment_rows(
    experiments_dir: Path, run_id: str, active: list[str], outcome: str, date: str
) -> None:
    for name in active:
        path = experiments_dir / f"{name}.jsonl"
        if not path.exists():
            log(
                f"warn: no baseline for experiment '{name}' at {path}; "
                "skipping applied row (run a Phase 6 Learn scan first)"
            )
            continue
        co_applied = _co_applied(active, name)
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
