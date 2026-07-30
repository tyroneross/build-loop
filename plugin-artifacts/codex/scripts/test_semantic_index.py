#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/semantic_index."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from semantic_index import query_facts, stats, upsert_fact, upsert_lesson  # noqa: E402


def _lesson(idx: int, *, promoted: bool = False) -> dict:
    return {
        "id": f"lesson-{idx}",
        "category": "architecture",
        "pattern": f"Adapter boundary lesson {idx}",
        "promoted": promoted,
        "context": {"files_affected": ["src/a.py"]},
    }


def _row_count(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM semantic_facts").fetchone()[0])
    finally:
        conn.close()


def test_upsert_fact_converges_on_subject_project(tmp_path: Path) -> None:
    db = tmp_path / "semantic.sqlite"
    upsert_fact(
        subject="fact:one",
        predicate="uses",
        object_text="old object",
        project="build-loop",
        db_path=db,
    )
    upsert_fact(
        subject="fact:one",
        predicate="uses",
        object_text="new object",
        project="build-loop",
        db_path=db,
    )

    assert _row_count(db) == 1
    out = query_facts(query="new", project="build-loop", db_path=db)
    assert len(out) == 1
    assert out[0]["object"] == "new object"


def test_project_filter_and_global_rows(tmp_path: Path) -> None:
    db = tmp_path / "semantic.sqlite"
    upsert_lesson(
        lesson=_lesson(1),
        project="build-loop",
        confidence=0.5,
        confidence_source="auto-inferred",
        db_path=db,
    )
    upsert_lesson(
        lesson=_lesson(2, promoted=True),
        project=None,
        confidence=0.75,
        confidence_source="auto-confirmed",
        db_path=db,
    )

    assert stats(db)["rows"] == 2
    scoped = query_facts(query="adapter", project="build-loop", db_path=db)
    assert len(scoped) == 1
    assert scoped[0]["project"] == "build-loop"
    all_rows = query_facts(query="adapter", db_path=db)
    assert len(all_rows) == 2
    assert any(row["project"] is None for row in all_rows)


def test_empty_missing_index_is_clean(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite"
    assert query_facts(query="anything", db_path=missing) == []
    assert stats(missing) == {"db_path": str(missing), "exists": False, "rows": 0}


def test_blank_query_returns_recent_rows(tmp_path: Path) -> None:
    db = tmp_path / "semantic.sqlite"
    upsert_lesson(lesson=_lesson(1), project="build-loop", db_path=db)
    upsert_lesson(lesson=_lesson(2), project="build-loop", db_path=db)

    out = query_facts(query="", limit=10, project="build-loop", db_path=db)
    assert [row["subject"] for row in out] == [
        "lesson:nav:lesson-2",
        "lesson:nav:lesson-1",
    ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
