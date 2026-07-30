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


# Stakes evidence that judgment_gate.stakes_reasons reads off the run record.
# When a richer Review-G record replaces a thin Stop record (source: append_run),
# these are carried forward if the incoming record omits them — they are facts of
# the RUN (what it was gated on), not of whoever wrote the record, and erasing
# them would flip the gate from a true WARN to a vacuous stakes_gated:false PASS.
# Judgment STATUS fields (auditor_status/advisor_status) are deliberately NOT
# carried — Review-G legitimately owns and overwrites those.
_STAKES_CARRY_KEYS = ("synthesisDensity", "triggers", "stakes", "dispatch_tier", "riskSurfaceChange")


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
        # A thin Stop-hook record (source: append_run) may already exist for this
        # run_id (the structural inline closeout fired before this orchestrator
        # Review-G write). Replace it in place rather than blind-appending a
        # duplicate — two entries for one run_id would let judgment_gate resolve
        # the thin one and FAIL a run whose auditor genuinely ran. A richer
        # (non-append_run) existing record is left untouched.
        run_id = entry.get("run_id")
        if run_id:
            for i, r in enumerate(runs):
                if isinstance(r, dict) and r.get("run_id") == run_id and r.get("source") == "append_run":
                    # Preserve the run's stakes evidence the thin record captured,
                    # unless this richer record already carries it. Merge into a
                    # copy so the caller's entry dict is never mutated in place.
                    merged = dict(entry)
                    for k in _STAKES_CARRY_KEYS:
                        if k in r and k not in merged:
                            merged[k] = r[k]
                    runs[i] = merged
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
