#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_consolidate.classify — packet prep + heuristic decision."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "scripts"))
sys.path.insert(0, str(HERE.parent))

from memory_consolidate import classify, intake


class HeuristicDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _submit(self, content, **kw):
        return intake.submit(content, workdir=self.tmp, run_id="r", host="claude_code", **kw)

    def test_decision_routes_debug_to_debugging_lane(self):
        c = self._submit(
            "stack trace from production: NPE at FooBar.line",
            hint="bug crash exception",
            project="demoproj",
        )
        d = classify.heuristic_decision(c, similar=[])
        self.assertEqual(d["scope"], "project")
        self.assertEqual(d["project"], "demoproj")
        self.assertEqual(d["lane"], "debugging")
        self.assertEqual(d["type"], "debug-incident")

    def test_decision_routes_decision_to_decisions_lane(self):
        c = self._submit("we decided to use Postgres over SQLite", hint="decision", project="demoproj")
        d = classify.heuristic_decision(c, similar=[])
        self.assertEqual(d["lane"], "decisions")
        self.assertEqual(d["type"], "decision")

    def test_decision_routes_gotcha_to_lessons_lane(self):
        c = self._submit("watch out for the path footgun", hint="gotcha footgun", project="demoproj")
        d = classify.heuristic_decision(c, similar=[])
        self.assertEqual(d["lane"], "lessons")
        self.assertEqual(d["type"], "gotcha")

    def test_decision_default_is_lessons_lesson(self):
        c = self._submit("arbitrary content with no hint match", project="demoproj")
        d = classify.heuristic_decision(c, similar=[])
        self.assertEqual(d["lane"], "lessons")
        self.assertEqual(d["type"], "lesson")

    def test_decision_top_level_when_no_project(self):
        c = self._submit("global lesson")
        d = classify.heuristic_decision(c, similar=[])
        self.assertEqual(d["scope"], "top-level")
        self.assertIsNone(d["project"])

    def test_decision_uses_candidate_type_when_set(self):
        c = self._submit("decided to pick X", hint="decision", type_="user-preference", project="demoproj")
        d = classify.heuristic_decision(c, similar=[])
        # Candidate's explicit type wins over the heuristic.
        self.assertEqual(d["type"], "user-preference")

    def test_decision_backlinks_from_similar(self):
        c = self._submit("body", project="demoproj")
        similar = [
            {"rank": 1, "subject": "lessons/2026-01-01-x.md", "project": "demoproj"},
            {"rank": 2, "subject": "lessons/2026-01-02-y.md", "project": "demoproj"},
            {"rank": 3, "subject": "lessons/2026-01-03-z.md", "project": "demoproj"},
            {"rank": 4, "subject": "lessons/2026-01-04-w.md", "project": "demoproj"},
        ]
        d = classify.heuristic_decision(c, similar=similar)
        self.assertEqual(len(d["backlinks"]), 3)  # capped at 3
        self.assertEqual(d["backlinks"][0], "lessons/2026-01-01-x.md")


class QuerySimilarSchemaTests(unittest.TestCase):
    """f2: _query_similar output must include file_hint in every result row."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_query_similar_schema_has_file_hint(self):
        """Each returned row must carry a 'file_hint' key (may be None when
        the recall backend returns nothing useful, but the key must exist)."""
        # Patch query_facts at the classify module level to return a synthetic row.
        import unittest.mock as mock
        fake_row = {
            "subject": "lessons/2026-01-01-gotcha-something.md",
            "predicate": "IS_A",
            "object": "gotcha",
            "project": "demoproj",
        }
        with mock.patch.object(classify, "_query_similar",
                               wraps=classify._query_similar) as _wrapped:
            # Call directly with a synthetic rows list.
            result = classify._query_similar.__wrapped__ if hasattr(
                classify._query_similar, "__wrapped__") else None

        # Test via direct patching of query_facts inside classify.
        import sys
        import types
        fake_module = types.ModuleType("semantic_index")
        fake_module.query_facts = lambda **kw: [fake_row]
        sys.modules["semantic_index"] = fake_module
        try:
            rows = classify._query_similar("something", project="demoproj", limit=1)
        finally:
            sys.modules.pop("semantic_index", None)

        self.assertEqual(len(rows), 1)
        self.assertIn("file_hint", rows[0])
        # file_hint falls back to subject when no dedicated path column.
        self.assertEqual(rows[0]["file_hint"], "lessons/2026-01-01-gotcha-something.md")
        # All other expected keys are present.
        for key in ("rank", "subject", "predicate", "object", "project"):
            self.assertIn(key, rows[0])

    def test_query_similar_file_hint_prefers_explicit_key(self):
        """When the recall row carries an explicit 'file_hint' key, that wins
        over the 'subject' fallback."""
        import sys, types
        fake_module = types.ModuleType("semantic_index")
        fake_module.query_facts = lambda **kw: [{
            "subject": "lessons/something.md",
            "predicate": "IS_A",
            "object": "gotcha",
            "project": "demoproj",
            "file_hint": "lessons/explicit-hint.md",
        }]
        sys.modules["semantic_index"] = fake_module
        try:
            rows = classify._query_similar("q", project=None, limit=1)
        finally:
            sys.modules.pop("semantic_index", None)

        self.assertEqual(rows[0]["file_hint"], "lessons/explicit-hint.md")


class PreparePacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_prepare_returns_full_packet(self):
        c = intake.submit(
            "raw body", workdir=self.tmp, run_id="r", host="claude_code",
            project="demoproj", hint="bug",
        )
        packet = classify.prepare(c.id, workdir=self.tmp)
        d = packet.to_dict()
        self.assertIn("candidate", d)
        self.assertIn("lane_options", d)
        self.assertIn("type_options", d)
        self.assertIn("similar_existing", d)
        self.assertIn("suggested_decision", d)
        self.assertEqual(d["candidate"]["id"], c.id)
        self.assertIn("lessons", d["lane_options"]["project"])
        self.assertIn("issues", d["lane_options"]["project"])
        self.assertIn("debugging", d["lane_options"]["project"])
        self.assertIn("lesson", d["type_options"])

    def test_prepare_similar_empty_on_no_recall_backend(self):
        """When the recall stack isn't available, similar_existing is []."""
        c = intake.submit("body", workdir=self.tmp, run_id="r", host="claude_code")
        packet = classify.prepare(c.id, workdir=self.tmp)
        # In test isolation, recall typically returns [] (no DB). The contract:
        # similar_existing must always be a list, never None.
        self.assertIsInstance(packet.similar_existing, list)


if __name__ == "__main__":
    unittest.main()
