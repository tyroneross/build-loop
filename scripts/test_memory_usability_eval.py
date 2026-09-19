# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_usability_eval: replayed queries, unvetted share, known answers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import memory_usability_eval as mue  # noqa: E402


def _store(root: Path) -> None:
    lesson = root / "projects" / "demo" / "lessons" / "sqlite-wal.md"
    lesson.parent.mkdir(parents=True)
    lesson.write_text("# SQLite WAL checkpoint budget", encoding="utf-8")
    (root / "indexes").mkdir()
    queries = ["sqlite wal checkpoint", "sqlite wal checkpoint", "unrelated zebra"]
    (root / "indexes" / "TELEMETRY.jsonl").write_text(
        "\n".join(json.dumps({"kind": "memory-read", "query": q}) for q in queries) + "\n", encoding="utf-8")


def test_replays_distinct_real_queries_newest_first(tmp_path: Path) -> None:
    _store(tmp_path)
    assert mue.real_queries(tmp_path / "indexes" / "TELEMETRY.jsonl", 5) == ["unrelated zebra", "sqlite wal checkpoint"]


def test_reports_hit_and_zero_unvetted_share(tmp_path: Path) -> None:
    _store(tmp_path)
    cases = [{"query": "sqlite wal checkpoint", "expect": ["sqlite-wal"], "project": "demo"},
             {"query": "zebra migration", "expect": ["zebra"], "project": "demo"}]
    report = mue.run(tmp_path, ["sqlite wal checkpoint"], cases, 5, "demo")
    assert report["hit_at_k"] == 0.5
    assert report["known_misses"] == ["zebra migration"]
    assert report["unvetted_share"] == 0.0
