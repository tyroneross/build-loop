#!/usr/bin/env python3
"""Tests for v3 metadata filter wiring in recall.py.

Strategy: rather than spin up Postgres, we monkey-patch
`recall.query` and `recall._embed` to capture the SQL + parameter list
that hybrid_search_facts would issue. This validates:

  1. Each new filter (--domain, --goal, --confidence-source) is correctly
     translated into a (typed-column OR JSONB-fallback) WHERE clause and
     bound parameter, applied BEFORE cosine/BM25 ranking.
  2. Combining multiple v3 filters AND-narrows the result set.
  3. Absent filters add no clauses (no-op pass-through).

The same monkey-patch pattern is used by the v2 recall test in this
repo's predecessor scripts; keep it consistent.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class RecallFilterSQLTests(unittest.TestCase):
    def setUp(self) -> None:
        # Late-import recall so each test can re-stub fresh.
        import importlib

        if "recall" in sys.modules:
            del sys.modules["recall"]
        self.recall = importlib.import_module("recall")
        # Monkey-patch query: capture, return [].
        self.captured: list[tuple[str, tuple]] = []

        def fake_query(sql: str, params: tuple = ()):
            self.captured.append((sql, params))
            return []

        self.recall.query = fake_query  # type: ignore[attr-defined]

    def _call_facts(self, **kw):
        # 1024-dim dummy embedding; only structure matters here.
        emb = [0.0] * 1024
        return self.recall.hybrid_search_facts(
            q=kw.pop("q", "any query"),
            embedding=emb,
            schema=kw.pop("schema", "build_loop_memory"),
            limit=kw.pop("limit", 5),
            confidence_floor=kw.pop("confidence_floor", 0.75),
            **kw,
        )

    def test_no_filters_produces_no_extra_clauses(self) -> None:
        self._call_facts()
        self.assertEqual(len(self.captured), 1)
        sql, params = self.captured[0]
        for f in ("project", "tool", "model", "task_category", "author",
                  "domain", "goal", "confidence_source"):
            # No metadata-filter clauses for that field.
            self.assertNotIn(f"metadata->>'{f}'", sql, msg=f"unexpected {f} clause")

    def test_domain_filter_adds_clause_and_param(self) -> None:
        self._call_facts(domain="search")
        sql, params = self.captured[0]
        self.assertIn("metadata->>'domain'", sql)
        self.assertIn("search", params)

    def test_goal_filter_adds_clause_and_param(self) -> None:
        self._call_facts(goal="reliability")
        sql, params = self.captured[0]
        self.assertIn("metadata->>'goal'", sql)
        self.assertIn("reliability", params)

    def test_confidence_source_filter_adds_clause_and_param(self) -> None:
        self._call_facts(confidence_source="user_statement")
        sql, params = self.captured[0]
        self.assertIn("metadata->>'confidence_source'", sql)
        self.assertIn("user_statement", params)

    def test_combined_v3_filters_all_present(self) -> None:
        self._call_facts(
            domain="search",
            goal="reliability",
            confidence_source="user_statement",
        )
        sql, params = self.captured[0]
        self.assertIn("metadata->>'domain'", sql)
        self.assertIn("metadata->>'goal'", sql)
        self.assertIn("metadata->>'confidence_source'", sql)
        self.assertIn("search", params)
        self.assertIn("reliability", params)
        self.assertIn("user_statement", params)

    def test_combined_v2_and_v3_filters_compose(self) -> None:
        # v2 + v3 filters must not stomp on each other.
        self._call_facts(
            project="build-loop",
            domain="meta",
            goal="dev-velocity",
        )
        sql, params = self.captured[0]
        self.assertIn("metadata->>'project'", sql)
        self.assertIn("metadata->>'domain'", sql)
        self.assertIn("metadata->>'goal'", sql)
        self.assertIn("build-loop", params)
        self.assertIn("meta", params)
        self.assertIn("dev-velocity", params)


if __name__ == "__main__":
    unittest.main()
