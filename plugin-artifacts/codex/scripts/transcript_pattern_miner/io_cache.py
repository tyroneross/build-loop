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
    """Cheap signature: size + mtime. Avoids hashing GB of JSONL."""
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def parse_ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return dt.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
