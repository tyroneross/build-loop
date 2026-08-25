#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Backend 4: build-loop native structured debugging incident reader."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .common import _parse_iso


def _score_text(text: str, query: str) -> int:
    """Simple deterministic keyword score for local incident notes."""
    terms = [part.lower() for part in query.split() if len(part) > 2]
    if not terms:
        return 1
    lower = text.lower()
    return sum(1 for term in terms if term in lower)


def _read_structured_incidents(workdir: Path, query: str, limit: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read the same structured store used by the native debugger writer."""
    incidents_dir = workdir / ".claude" / "memory" / "incidents"
    if not incidents_dir.is_dir():
        return [], [f"debugger_unavailable: structured incident dir absent: {incidents_dir}"]

    scored: List[Tuple[int, float, Path, Dict[str, Any]]] = []
    for note in incidents_dir.glob("*.json"):
        try:
            incident = json.loads(note.read_text(encoding="utf-8"))
            stat = note.stat()
        except (OSError, json.JSONDecodeError):
            continue
        root_cause = incident.get("root_cause") or {}
        fix = incident.get("fix") or {}
        searchable = " ".join(
            str(value)
            for value in (
                incident.get("symptom"),
                root_cause.get("description") if isinstance(root_cause, dict) else root_cause,
                fix.get("approach") if isinstance(fix, dict) else fix,
                " ".join(incident.get("tags") or []),
            )
            if value
        )
        score = _score_text(searchable, query or "*")
        if score > 0:
            timestamp = incident.get("timestamp")
            recency = float(timestamp) / 1000 if isinstance(timestamp, (int, float)) else stat.st_mtime
            scored.append((score, recency, note, incident))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    entries: List[Dict[str, Any]] = []
    for _score, recency, note, incident in scored[:limit]:
        root_cause = incident.get("root_cause") or {}
        fix = incident.get("fix") or {}
        entries.append(
            {
                "_kind": "debugger",
                "_recency_ts": recency,
                "id": incident.get("incident_id") or note.stem,
                "symptom": incident.get("symptom"),
                "root_cause": root_cause.get("description") if isinstance(root_cause, dict) else root_cause,
                "fix": fix.get("approach") if isinstance(fix, dict) else fix,
                "project": workdir.name,
                "path": str(note),
            }
        )
    return entries, []


def _parse_incidents(out_text: str, limit: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parse JSON payload into incident entries."""
    try:
        payload = json.loads(out_text) if out_text else {"incidents": []}
    except json.JSONDecodeError as e:
        return [], [f"debugger_unavailable: bad json: {e}"]
    incidents = payload.get("incidents") or payload.get("results") or []
    return [
        {
            "_kind": "debugger",
            "_recency_ts": _parse_iso(inc.get("created_at") or inc.get("date")),
            "id": inc.get("id") or inc.get("incident_id"),
            "symptom": inc.get("symptom"),
            "root_cause": inc.get("root_cause"),
            "fix": inc.get("fix"),
            "project": inc.get("project"),
        }
        for inc in incidents[:limit]
    ], []


def read_debugger_impl(
    workdir: Path,
    query: str,
    limit: int,
    project: Optional[str],
    runner: Optional[Callable[..., str]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Core debugger read; ``runner`` substitutes structured search in tests.

    Callers (the facade) pass the runner from their own module-level state so
    the test injection via ``monkeypatch.setattr(mf, '_DEBUGGER_RUNNER_OVERRIDE', ...)``
    is visible without a circular import.
    """
    if runner is not None:
        out_text = runner(query=query, limit=limit, project=project)
        reasons: List[str] = []
        entries, parse_reasons = _parse_incidents(out_text, limit)
        return entries, reasons + parse_reasons
    else:
        return _read_structured_incidents(workdir, query, limit)
