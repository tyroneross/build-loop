#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Backend 1: state.json runs[] reader for memory_facade."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import _parse_iso, _q_match


def _workdir_project(workdir: Path) -> Optional[str]:
    """Resolve the project slug that owns ``workdir``, or None when unknown."""
    try:
        from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415

        return resolve_project(workdir)
    except Exception:  # noqa: BLE001 — best-effort, never break the read path
        return None



def _files_text(value: object) -> str:
    """Searchable text for a run's filesTouched, whatever shape an older run wrote.

    Most runs record a list of paths; some wrote a count (for example 30). One
    malformed row must not make the whole runs backend unreadable.
    """
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return "" if value is None else str(value)

def read_runs(
    workdir: Path, query: str, limit: int, project: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read ``.build-loop/state.json`` runs[] for *workdir*.

    ``project`` scopes the read. state.json is per-repository, so every run it
    holds belongs to the project that owns ``workdir`` unless the run record
    names its own. A scoped recall for a DIFFERENT project must therefore
    return nothing here rather than the local repo's runs -- the defect that
    put build-loop runs at rank 1 of a ``project='ross-labs-astro'`` recall.
    ``project=None`` keeps the previous unscoped behaviour exactly.
    """
    state_path = workdir / ".build-loop" / "state.json"
    reasons: List[str] = []
    if not state_path.is_file():
        return [], reasons
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        reasons.append(f"runs_read_error: {e}")
        return [], reasons
    runs = state.get("runs") or []
    # Resolved once, not per run: only needed when the caller scoped the read.
    local_project = _workdir_project(workdir) if project else None
    out: List[Dict[str, Any]] = []
    for r in runs:
        if project:
            run_project = r.get("project") or local_project
            # An unresolvable project (no project_resolver, unregistered repo)
            # degrades to "cannot prove it is foreign" and stays visible; a
            # KNOWN, different project is filtered out.
            if run_project and run_project != project:
                continue
        text = " ".join([
            str(r.get("goal", "")),
            str(r.get("outcome", "")),
            _files_text(r.get("filesTouched")),
        ])
        if not _q_match(text, query):
            continue
        out.append({
            "_kind": "runs",
            "_recency_ts": _parse_iso(r.get("date")),
            "run_id": r.get("run_id"),
            "goal": r.get("goal"),
            "outcome": r.get("outcome"),
            "date": r.get("date"),
            "files_touched": r.get("filesTouched", []),
        })
    out.sort(key=lambda x: x["_recency_ts"] or 0, reverse=True)
    return out[:limit], reasons
