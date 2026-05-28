#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for backend_health.probe_fts. Zero third-party deps. Run: python3 test_backend_health.py

Strategy:
  - `probe_fts` does `from db import query` lazily inside the function. We inject a
    fake `db` module into sys.modules so the probe reads our canned rows instead of
    hitting Postgres. `db.query()` returns dict rows (psycopg `dict_row` shape), which
    is the exact shape that regressed `r[0]` indexing into `query_failed:KeyError`.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import backend_health  # noqa: E402


def _install_fake_db(handler) -> None:
    """Put a fake `db` module on sys.modules whose query() delegates to `handler`."""
    mod = types.ModuleType("db")
    mod.query = handler  # type: ignore[attr-defined]
    sys.modules["db"] = mod


class ProbeFtsRowShapeTest(unittest.TestCase):
    def tearDown(self) -> None:
        sys.modules.pop("db", None)

    def test_dict_rows_no_keyerror_and_flags_correct(self) -> None:
        """dict_row-shaped rows must parse without KeyError and compute every flag."""
        def query(sql, params=None):
            if "pg_extension" in sql:
                return [{"extname": "pg_trgm"}, {"extname": "vector"}]
            return [
                {"indexname": "sf_embedding_hnsw",
                 "indexdef": "CREATE INDEX sf_embedding_hnsw ON semantic_facts USING hnsw (embedding vector_cosine_ops)"},
                {"indexname": "sf_object_gin",
                 "indexdef": "CREATE INDEX sf_object_gin ON semantic_facts USING gin (to_tsvector('english', object))"},
            ]

        _install_fake_db(query)
        out = backend_health.probe_fts(Path("."))

        self.assertNotIn("query_failed", str(out.get("reason", "")))
        self.assertTrue(out["pg_trgm"])
        self.assertTrue(out["pgvector"])
        self.assertTrue(out["hnsw_on_embedding"])
        self.assertTrue(out["gin_on_object"])
        self.assertTrue(out["ok"])

    def test_missing_pieces_reported(self) -> None:
        """Absent extensions/indexes yield ok=false with a missing: reason, not a crash."""
        def query(sql, params=None):
            if "pg_extension" in sql:
                return [{"extname": "pg_trgm"}]  # no pgvector
            return []  # no indexes

        _install_fake_db(query)
        out = backend_health.probe_fts(Path("."))

        self.assertFalse(out["ok"])
        self.assertTrue(out["reason"].startswith("missing:"))
        self.assertIn("pgvector", out["reason"])
        self.assertIn("hnsw_on_embedding", out["reason"])

    def test_connection_refused_classifies_as_unavailable(self) -> None:
        """A real outage must stay 'postgres_unavailable', not 'query_failed'."""
        def query(sql, params=None):
            raise RuntimeError("connection refused: could not connect to server")

        _install_fake_db(query)
        out = backend_health.probe_fts(Path("."))

        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "postgres_unavailable")

    def test_other_query_error_still_classified(self) -> None:
        """Non-connection errors classify as query_failed:<Type> (not from our row access)."""
        def query(sql, params=None):
            raise ValueError("syntax error at or near")

        _install_fake_db(query)
        out = backend_health.probe_fts(Path("."))

        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "query_failed:ValueError")


if __name__ == "__main__":
    unittest.main()
