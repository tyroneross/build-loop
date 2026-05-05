"""Lessons store — read/write ``.build-loop/architecture/lessons.json``.

Mirrors NavGator's lesson shape (id, category, pattern, signature, severity,
context, example, validation, promoted). Chunk 1 ships the storage primitives
only; Chunk 8 adds ingestion, promotion logic, and back-pressure into Phase 1
Assess.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schemas import Lesson, SCHEMA_VERSION
from .storage import arch_dir, atomic_write_json, read_json

LESSONS_FILENAME = "lessons.json"


def lessons_path(repo_root: Path | str) -> Path:
    return arch_dir(repo_root) / LESSONS_FILENAME


def read_lessons(repo_root: Path | str) -> List[Lesson]:
    raw = read_json(lessons_path(repo_root))
    if not raw:
        return []
    items = raw.get("lessons") or []
    return [Lesson.from_dict(x) for x in items]


def write_lessons(repo_root: Path | str, lessons: List[Lesson]) -> Path:
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": int(time.time() * 1000),
        "count": len(lessons),
        "lessons": [l.to_dict() for l in lessons],
    }
    p = lessons_path(repo_root)
    atomic_write_json(p, payload)
    return p


def append_lesson(repo_root: Path | str, lesson: Lesson) -> Path:
    """Append (or replace by id) a single lesson."""
    existing = read_lessons(repo_root)
    by_id: Dict[str, Lesson] = {l.id: l for l in existing}
    by_id[lesson.id] = lesson
    return write_lessons(repo_root, list(by_id.values()))


def find_by_signature(repo_root: Path | str, signature: str) -> Optional[Lesson]:
    for l in read_lessons(repo_root):
        if l.signature == signature:
            return l
    return None
