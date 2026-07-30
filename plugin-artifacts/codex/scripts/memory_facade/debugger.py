#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Backend 4: build-loop native debugging incident reader."""
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


def _section(text: str, heading: str) -> Optional[str]:
    """Extract a short Markdown section body by heading name."""
    lines = text.splitlines()
    start: Optional[int] = None
    for idx, line in enumerate(lines):
        if line.lstrip("# ").strip().lower() == heading.lower():
            start = idx + 1
            break
    if start is None:
        return None
    body: List[str] = []
    for line in lines[start:]:
        if line.startswith("#"):
            break
        if line.strip():
            body.append(line.strip())
        if len(" ".join(body)) > 500:
            break
    return " ".join(body) or None


def _read_local_issues(workdir: Path, query: str, limit: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read build-loop native incident notes from .build-loop/issues."""
    issues_dir = workdir / ".build-loop" / "issues"
    if not issues_dir.is_dir():
        return [], [f"debugger_unavailable: local issue dir absent: {issues_dir}"]

    scored: List[Tuple[int, float, Path, str]] = []
    for note in issues_dir.rglob("*.md"):
        try:
            text = note.read_text(encoding="utf-8")
            stat = note.stat()
        except OSError:
            continue
        score = _score_text(text, query or "*")
        if score > 0:
            scored.append((score, stat.st_mtime, note, text))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    entries: List[Dict[str, Any]] = []
    for _score, mtime, note, text in scored[:limit]:
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")), note.stem)
        entries.append(
            {
                "_kind": "debugger",
                "_recency_ts": mtime,
                "id": note.stem,
                "symptom": _section(text, "Symptom") or title,
                "root_cause": _section(text, "Root Cause") or _section(text, "Root cause"),
                "fix": _section(text, "Fix") or _section(text, "Fix approach"),
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
        return _read_local_issues(workdir, query, limit)
