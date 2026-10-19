#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""io_cache — JSONL streaming, processed-cache persistence, timestamp parsing."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterator


def load_processed(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def save_processed(path: Path, processed: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(processed, indent=2, sort_keys=True))
    tmp.replace(path)


def file_signature(path: Path) -> str:
    """Verified content plus parser version; same-size rewrites invalidate reuse."""
    from transcript_adapter import PARSER_VERSION, _source_revision
    return f"{PARSER_VERSION}:{_source_revision(path)}"


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    from transcript_adapter import iter_events
    yield from iter_events(path, include_provenance=True, require_complete=True)


def parse_ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return dt.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
