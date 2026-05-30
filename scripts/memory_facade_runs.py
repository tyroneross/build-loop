#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Backend 1: state.json runs[] reader for memory_facade."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from memory_facade_common import _parse_iso, _q_match


def read_runs(workdir: Path, query: str, limit: int) -> Tuple[List[Dict[str, Any]], List[str]]:
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
    out: List[Dict[str, Any]] = []
    for r in runs:
        text = " ".join([
            str(r.get("goal", "")),
            str(r.get("outcome", "")),
            " ".join(r.get("filesTouched", []) or []),
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
