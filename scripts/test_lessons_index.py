#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/lessons_index/.

TDD-first. Run: uv run pytest scripts/test_lessons_index.py -v

All tests pass with ZERO external deps — no MLX, no Ollama, no network.
Embeddings are forcibly skipped via EMBED_BACKEND_UNAVAILABLE env trick.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

POSTGRES_LESSON = """\
---
name: postgres-connection-pooling
description: Use pgBouncer in transaction mode for Django connections
type: lesson
---

# Postgres Connection Pooling

Always configure a connection pooler between Django and Postgres in production.
pgBouncer in transaction mode reduces open connections from N*workers to a small
fixed pool. Without pooling, under load the DB hits `max_connections` and new
connections are refused.

Key config: `pool_mode=transaction`, `max_client_conn=200`, `default_pool_size=20`.
"""

REACT_LESSON = """\
---
name: react-hydration-mismatch
description: Avoid server/client hydration mismatches in React 18
type: lesson
---

# React Hydration Mismatch

React 18 strict mode throws on server/client HTML mismatches. Common causes:
- `Date.now()` or `Math.random()` called during render
- Browser-only globals accessed unconditionally
- Timezone differences between server and client

Fix: wrap non-deterministic values in `useEffect` or use `suppressHydrationWarning`
sparingly. For dynamic content, defer to client-only rendering via `"use client"`.
"""

GIT_LESSON = """\
---
name: git-worktree-cleanup
description: Always remove worktrees after a parallel build to avoid git confusion
type: lesson
---

# Git Worktree Cleanup

After finishing parallel branch work via `git worktree add`, always prune:

    git worktree remove <path>
    git worktree prune

Stale worktrees block branch deletion and confuse IDE indexers. The build-loop
collapse_run.py script handles this automatically at Phase D.
"""

DESIGN_ITEM = """\
---
name: calm-precision-spacing
description: Use 8pt grid for all spacing decisions
type: design-guidance
---

Spacing follows an 8pt grid. Tokens: 4px, 8px, 16px, 24px, 32px, 48px, 64px.
Never use odd multiples. Component padding is always a token value.
"""


def _write_lesson(directory: Path, filename: str, content: str) -> Path:
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


class LessonsIndexTests(unittest.TestCase):
    """Core TDD tests for the lessons_index package."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

        # Override memory store root to our tmp dir.
        os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = str(self.root)

        # Force embed backend unavailable so FTS-only path is exercised.
        # We do NOT want any real embed backend in tests.
        os.environ["EMBED_BACKEND_UNAVAILABLE"] = "1"
        os.environ["MLX_FORCE_FAIL"] = "1"
        os.environ["EMBED_FORCE_INPROCESS"] = "1"

        # Seed lesson files in the test project's lessons dir.
        lessons_dir = self.root / "projects" / "test-project" / "lessons"
        lessons_dir.mkdir(parents=True, exist_ok=True)
        _write_lesson(lessons_dir, "postgres-pooling.md", POSTGRES_LESSON)
        _write_lesson(lessons_dir, "react-hydration.md", REACT_LESSON)
        _write_lesson(lessons_dir, "git-worktree.md", GIT_LESSON)

        # Seed a top-level lessons item.
        top_lessons = self.root / "lessons"
        top_lessons.mkdir(parents=True, exist_ok=True)
        _write_lesson(top_lessons, "design-spacing.md", DESIGN_ITEM)

        # Default DB path for this test run.
        idx_dir = self.root / "indexes"
        idx_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = str(idx_dir / "lessons_index.db")

        # Import AFTER env vars are set to pick up overrides.
        self._import_package()

    def _import_package(self) -> None:
        # Clear any cached module state from prior test runs.
        for mod in list(sys.modules.keys()):
            if "lessons_index" in mod:
                del sys.modules[mod]

        pkg_dir = HERE / "lessons_index"
        if str(pkg_dir.parent) not in sys.path:
            sys.path.insert(0, str(pkg_dir.parent))

        import lessons_index as li  # noqa: PLC0415
        self.li = li

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        for key in ("BUILD_LOOP_MEMORY_STORE_ROOT", "EMBED_BACKEND_UNAVAILABLE",
                    "MLX_FORCE_FAIL", "EMBED_FORCE_INPROCESS"):
            os.environ.pop(key, None)

    # ------------------------------------------------------------------
    # (a) BM25 query ranks the right lesson first
    # ------------------------------------------------------------------

    def test_query_ranks_postgres_lesson_first_for_database_pooling(self) -> None:
        """query('database pooling') must return the postgres lesson at rank 0."""
        self.li.ingest(project="test-project", db_path=self.db_path)
        results = self.li.query(
            "database connection pooling",
            project="test-project",
            limit=5,
            db_path=self.db_path,
        )
        self.assertGreater(len(results), 0, "Expected at least one result")
        top = results[0]
        self.assertIn("postgres", top["name"].lower(),
                      f"Expected postgres lesson first, got: {top['name']}")

    def test_query_ranks_react_lesson_for_hydration(self) -> None:
        """query('react hydration') must return the React lesson at rank 0."""
        self.li.ingest(project="test-project", db_path=self.db_path)
        results = self.li.query(
            "react hydration client server mismatch",
            project="test-project",
            limit=5,
            db_path=self.db_path,
        )
        self.assertGreater(len(results), 0)
        self.assertIn("react", results[0]["name"].lower(),
                      f"Expected react lesson first, got: {results[0]['name']}")

    # ------------------------------------------------------------------
    # (b) FTS works with NO embedding backend
    # ------------------------------------------------------------------

    def test_fts_works_without_embedding_backend(self) -> None:
        """Ingest + query must work even when embeddings are unavailable."""
        # EMBED_BACKEND_UNAVAILABLE + MLX_FORCE_FAIL are set in setUp.
        self.li.ingest(project="test-project", db_path=self.db_path)
        results = self.li.query(
            "worktree prune git parallel",
            project="test-project",
            limit=3,
            db_path=self.db_path,
        )
        self.assertGreater(len(results), 0,
                           "FTS query returned no results without embed backend")
        self.assertIn("git", results[0]["name"].lower(),
                      f"Expected git worktree lesson, got: {results[0]['name']}")

    def test_result_has_required_fields(self) -> None:
        """Each result dict must carry name, description, snippet, score, source_path, lane."""
        self.li.ingest(project="test-project", db_path=self.db_path)
        # Use "pooling" which appears in the postgres lesson name + body.
        results = self.li.query("connection pooling", project="test-project",
                                limit=3, db_path=self.db_path)
        self.assertGreater(len(results), 0)
        required = {"name", "description", "snippet", "score", "source_path", "lane"}
        for r in results:
            missing = required - set(r.keys())
            self.assertEqual(missing, set(), f"Missing fields in result: {missing}")

    # ------------------------------------------------------------------
    # (c) Incremental: re-ingest with no changes touches 0 rows
    # ------------------------------------------------------------------

    def test_incremental_no_changes_touches_zero_rows(self) -> None:
        """Second ingest of unchanged files must produce upserted=0, skipped=N."""
        self.li.ingest(project="test-project", db_path=self.db_path)
        stats_before = self.li.stats(db_path=self.db_path)

        result2 = self.li.ingest(project="test-project", db_path=self.db_path)
        stats_after = self.li.stats(db_path=self.db_path)

        # Row count must not change.
        self.assertEqual(stats_before["total_facts"], stats_after["total_facts"])
        # All files should be skipped (sha256 unchanged).
        self.assertEqual(result2["upserted"], 0,
                         f"Expected 0 upserted on re-ingest, got: {result2['upserted']}")
        self.assertGreater(result2["skipped"], 0,
                           "Expected some skipped on re-ingest")

    # ------------------------------------------------------------------
    # (d) Editing one file + re-ingest updates only that row
    # ------------------------------------------------------------------

    def test_incremental_edits_only_changed_file(self) -> None:
        """Re-ingest after editing exactly one file must upsert exactly 1 row."""
        self.li.ingest(project="test-project", db_path=self.db_path)
        stats_before = self.li.stats(db_path=self.db_path)

        # Modify the react lesson.
        lessons_dir = self.root / "projects" / "test-project" / "lessons"
        react_path = lessons_dir / "react-hydration.md"
        original = react_path.read_text(encoding="utf-8")
        react_path.write_text(
            original + "\nExtra line to force sha256 change.", encoding="utf-8"
        )

        result2 = self.li.ingest(project="test-project", db_path=self.db_path)
        stats_after = self.li.stats(db_path=self.db_path)

        self.assertEqual(result2["upserted"], 1,
                         f"Expected 1 upserted after 1 file edit, got: {result2['upserted']}")
        # Total row count stays the same (update, not insert).
        self.assertEqual(stats_before["total_facts"], stats_after["total_facts"])

    # ------------------------------------------------------------------
    # (e) Stats returns correct counts
    # ------------------------------------------------------------------

    def test_stats_returns_correct_counts(self) -> None:
        """stats() must reflect the number of ingested files."""
        self.li.ingest(project="test-project", db_path=self.db_path)
        # Also ingest top-level (design item).
        self.li.ingest(project=None, db_path=self.db_path)

        s = self.li.stats(db_path=self.db_path)
        self.assertIn("total_facts", s)
        # 3 project lessons + 1 top-level = 4 total
        self.assertEqual(s["total_facts"], 4,
                         f"Expected 4 total facts, got: {s['total_facts']}")
        self.assertIn("schema_version", s)
        self.assertIn("last_ingest_ts", s)

    # ------------------------------------------------------------------
    # (f) open_db creates schema tables
    # ------------------------------------------------------------------

    def test_open_db_creates_schema(self) -> None:
        """open_db must create all required tables."""
        import sqlite3  # noqa: PLC0415
        self.li.open_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cur.fetchall()}
        conn.close()
        for expected in ("facts", "embeddings", "meta"):
            self.assertIn(expected, tables, f"Missing table: {expected}")
        # FTS5 virtual table.
        self.assertIn("facts_fts", tables, "Missing FTS5 virtual table: facts_fts")

    # ------------------------------------------------------------------
    # (g) FTS5 confirmation: BM25 scoring differentiates documents
    # ------------------------------------------------------------------

    def test_fts5_bm25_differentiates_topics(self) -> None:
        """BM25 scores must rank topic-matched documents above off-topic ones."""
        self.li.ingest(project="test-project", db_path=self.db_path)
        results = self.li.query("connection pool database postgres",
                                project="test-project", limit=5,
                                db_path=self.db_path)
        names = [r["name"] for r in results]
        # postgres lesson must appear before git or react
        postgres_idx = next((i for i, n in enumerate(names) if "postgres" in n), None)
        self.assertIsNotNone(postgres_idx, f"Postgres lesson not in results: {names}")
        if len(names) > 1:
            # Verify it's ranked above non-postgres results.
            non_postgres = [i for i, n in enumerate(names) if "postgres" not in n]
            if non_postgres:
                self.assertLess(postgres_idx, min(non_postgres),
                                f"Postgres lesson not ranked first: {names}")

    # ------------------------------------------------------------------
    # (h) Scoping: project=None ingests top-level, not project lessons
    # ------------------------------------------------------------------

    def test_top_level_ingest_and_query(self) -> None:
        """Ingesting project=None picks up top-level lessons only."""
        result = self.li.ingest(project=None, db_path=self.db_path)
        self.assertEqual(result["upserted"], 1,
                         f"Expected 1 file from top-level, got: {result['upserted']}")
        s = self.li.stats(db_path=self.db_path)
        self.assertEqual(s["total_facts"], 1)

    # ------------------------------------------------------------------
    # (i) Idempotent: ingest twice produces correct totals
    # ------------------------------------------------------------------

    def test_idempotent_multi_ingest(self) -> None:
        """Ingesting different projects multiple times produces correct total."""
        self.li.ingest(project="test-project", db_path=self.db_path)
        self.li.ingest(project=None, db_path=self.db_path)
        # Re-ingest both — should produce 0 upserted.
        r1 = self.li.ingest(project="test-project", db_path=self.db_path)
        r2 = self.li.ingest(project=None, db_path=self.db_path)
        self.assertEqual(r1["upserted"], 0)
        self.assertEqual(r2["upserted"], 0)
        s = self.li.stats(db_path=self.db_path)
        self.assertEqual(s["total_facts"], 4)


if __name__ == "__main__":
    unittest.main()
