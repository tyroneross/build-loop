#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Backend 4: build-loop debugger MCP (claude-code-debugger) reader."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from memory_facade_common import _parse_iso


def _run_npx(query: str, limit: int) -> Tuple[Optional[str], List[str]]:
    """Invoke npx CLI; returns (stdout_text, reasons).  stdout is None on failure."""
    try:
        proc = subprocess.run(
            [
                "npx", "--no-install",
                "@tyroneross/claude-code-debugger",
                "search", "--query", query or "*",
                "--limit", str(limit), "--json",
            ],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return None, [f"mcp_unavailable: {type(e).__name__}: {e}"]
    if proc.returncode != 0:
        return None, [f"mcp_unavailable: cli rc={proc.returncode}"]
    return proc.stdout, []


def _parse_incidents(out_text: str, limit: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parse JSON payload into incident entries."""
    try:
        payload = json.loads(out_text) if out_text else {"incidents": []}
    except json.JSONDecodeError as e:
        return [], [f"mcp_unavailable: bad json: {e}"]
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
    """Core debugger read; ``runner`` substitutes the npx CLI in tests.

    Callers (the facade) pass the runner from their own module-level state so
    the test injection via ``monkeypatch.setattr(mf, '_DEBUGGER_RUNNER_OVERRIDE', ...)``
    is visible without a circular import.
    """
    if runner is not None:
        out_text = runner(query=query, limit=limit, project=project)
        reasons: List[str] = []
    else:
        out_text, reasons = _run_npx(query, limit)
        if out_text is None:
            return [], reasons

    entries, parse_reasons = _parse_incidents(out_text, limit)
    return entries, reasons + parse_reasons
