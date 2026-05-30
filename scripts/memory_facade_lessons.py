#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Backend 2.5: lessons reader for memory_facade.

Reads free-form feedback/pattern/reference/decision_* markdown files from
build-loop-memory/lessons/ plus projects/<slug>/lessons/.  Distinct from the
decisions backend (sequence-numbered project-tagged store).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from memory_facade_common import _LESSON_FRONTMATTER_RE, _q_match


def _resolve_memory_dirs(workdir: Path) -> List[Tuple[Path, str]]:
    """Return ``[(dir, scope), ...]`` for canonical lesson memory.

    Order: top-level lessons first, project lessons second, so project
    entries override global entries with the same filename.
    """
    out: List[Tuple[Path, str]] = []
    try:
        from _paths import (  # type: ignore  # noqa: PLC0415
            project_lessons_dir,
            top_level_lessons_dir,
        )
        from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — best-effort
        return out

    global_dir = top_level_lessons_dir()
    if global_dir.is_dir():
        out.append((global_dir, "global"))

    proj = resolve_project(workdir)
    if proj and proj != "_unscoped":
        try:
            project_dir = project_lessons_dir(proj)
        except ValueError:
            project_dir = None  # type: ignore[assignment]
        if project_dir is not None and project_dir.is_dir():
            out.append((project_dir, "project"))

    return out


def _parse_lesson_frontmatter(text: str) -> Tuple[str, str]:
    """Extract (title, metadata_type) from YAML frontmatter, or ("", "")."""
    title = ""
    mtype = ""
    m = _LESSON_FRONTMATTER_RE.match(text)
    if not m:
        return title, mtype
    for line in m.group(1).splitlines():
        s = line.strip()
        if s.startswith("name:"):
            title = s.split(":", 1)[1].strip().strip('"').strip("'")
        elif s.startswith("description:") and not title:
            title = s.split(":", 1)[1].strip().strip('"').strip("'")
        elif s.startswith("- type:") or (
            s.startswith("type:") and "metadata" not in s
        ):
            mtype = s.split(":", 1)[1].strip().strip('"').strip("'")
    return title, mtype


_SKIP_NAMES = {"MEMORY.md", "constitution.md", "README.md"}


def read_lessons(
    workdir: Path, query: str, limit: int
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read free-form lessons across global + project tiers.

    Dedup rule: same filename across tiers — later-listed tier wins
    (project > global).  Result carries ``_scope`` ("global" | "project").
    """
    reasons: List[str] = []
    dirs = _resolve_memory_dirs(workdir)
    if not dirs:
        return [], reasons

    by_name: Dict[str, Dict[str, Any]] = {}
    for mem_dir, scope in dirs:
        for p in sorted(mem_dir.glob("*.md")):
            if p.name in _SKIP_NAMES:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError as e:
                reasons.append(f"lesson_read_error: {p.name} {e}")
                continue
            title, mtype = _parse_lesson_frontmatter(text)
            if not _q_match(text + " " + title + " " + p.name, query):
                continue
            try:
                ts: Optional[float] = p.stat().st_mtime
            except OSError:
                ts = None
            by_name[p.name] = {
                "_kind": "lessons",
                "_scope": scope,
                "_recency_ts": ts,
                "id": p.stem,
                "name": p.name,
                "title": title or p.stem,
                "metadata_type": mtype,
                "path": str(p),
            }

    out = list(by_name.values())
    out.sort(key=lambda x: x.get("_recency_ts") or 0, reverse=True)
    return out[:limit], reasons
