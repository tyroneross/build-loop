#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for memory_facade sub-modules.

Stdlib only. Imported by every memory_facade_*.py module.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Frontmatter regex used by both decisions and lessons backends.
DECISION_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_LESSON_FRONTMATTER_RE = DECISION_FRONTMATTER_RE


def _parse_iso(ts: Any) -> Optional[float]:
    """Best-effort parse of an ISO-8601 timestamp into a float (Unix seconds)."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return ts / 1000.0 if ts > 1e12 else float(ts)
    if not isinstance(ts, str):
        return None
    s = ts.strip().rstrip("Z")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, AttributeError):
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, AttributeError):
            return None


def _q_match(text: str, query: str) -> bool:
    """Case-insensitive substring match. Empty query matches everything."""
    if not query:
        return True
    return query.lower() in (text or "").lower()


def _read_jsonl(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    rows: List[Dict[str, Any]] = []
    reasons: List[str] = []
    if not path.is_file():
        return rows, reasons
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return rows, [f"index_read_error: {path.name} {e}"]
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as e:
            reasons.append(f"index_parse_error: {path.name}:{lineno} {e}")
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows, reasons
