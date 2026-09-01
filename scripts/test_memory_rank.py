#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_rank.py.

The two properties that matter most are asserted directly: ranking must never
drop a candidate (so it cannot reduce recall), and a real term match must beat
recency (the defect measured on the live store).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import memory_rank as mr  # noqa: E402


def row(rid="x", title="", tags=None, ts=0, **kw):
    d = {"id": rid, "title": title, "tags": tags or [], "_recency_ts": ts}
    d.update(kw)
    return d


class TokenizeTest(unittest.TestCase):
    def test_stopwords_and_short_tokens_dropped(self):
        self.assertEqual(mr.query_terms("the AI of a migration"), ["migration"],
                         "'the'/'of'/'a' are stopwords and 'ai' is under MIN_TOKEN_LEN")

    def test_distinct_terms_preserve_order(self):
        self.assertEqual(mr.query_terms("memory bootstrap memory"), ["memory", "bootstrap"])


class WordBoundaryTest(unittest.TestCase):
    def test_substring_false_positive_is_gone(self):
        """THE ORIGINAL BUG: token-OR substring let 'ai' match 'main'/'domain'."""
        words = {"main", "domain", "explain", "chain"}
        self.assertFalse(mr._term_hit("ai", words))

    def test_exact_word_matches(self):
        self.assertTrue(mr._term_hit("migration", {"migration", "ledger"}))

    def test_prefix_morphology_still_matches(self):
        self.assertTrue(mr._term_hit("migration", {"migrations"}))
        self.assertTrue(mr._term_hit("deploy", {"deployment"}))


class RankTest(unittest.TestCase):
    def test_ranking_never_drops_a_row(self):
        """Ordering only -- this must be incapable of reducing recall."""
        rows = [row(rid=f"id-{i}", ts=i) for i in range(25)]
        out = mr.rank(rows, "something entirely unrelated")
        self.assertEqual(len(out), 25)
        self.assertEqual({r["id"] for r in out}, {r["id"] for r in rows})

    def test_relevance_beats_recency(self):
        """PLANTED: the measured live defect -- newest doc matched zero terms."""
        newest_irrelevant = row(rid="newest", title="unrelated notes", ts=9_999_999)
        older_relevant = row(rid="match",
                             title="ledger migration reconciliation blockers", ts=1)
        out = mr.rank([newest_irrelevant, older_relevant],
                      "ledger migration reconciliation blockers")
        self.assertEqual(out[0]["id"], "match")

    def test_higher_coverage_wins(self):
        two = row(rid="two", title="ledger migration", ts=1)
        one = row(rid="one", title="ledger only", ts=1)
        out = mr.rank([one, two], "ledger migration reconciliation")
        self.assertEqual(out[0]["id"], "two")

    def test_breadth_of_match_beats_depth_on_one_field(self):
        """Breadth must beat a single loud hit, even when the loud one is newer.

        The score sums field_weight * idf over matched terms, so three body hits
        on rarer terms outweigh one title hit on a term both documents share.
        This is the property that replaced the removed coverage multiplier.
        """
        narrow_but_newer = row(rid="narrow", title="ledger", ts=9_999_999)
        broad = row(rid="broad", title="unrelated heading",
                    summary="ledger migration reconciliation", ts=1)
        out = mr.rank([narrow_but_newer, broad],
                      "ledger migration reconciliation")
        self.assertEqual(out[0]["id"], "broad")

    def test_title_hit_outranks_body_hit(self):
        t = row(rid="in-title", title="migration", ts=1)
        b = row(rid="in-body", title="unrelated", summary="migration", ts=1)
        out = mr.rank([b, t], "migration")
        self.assertEqual(out[0]["id"], "in-title")

    def test_recency_breaks_ties_only(self):
        old = row(rid="old", title="ledger migration", ts=1)
        new = row(rid="new", title="ledger migration", ts=9_999_999)
        out = mr.rank([old, new], "ledger migration")
        self.assertEqual(out[0]["id"], "new", "equal relevance -> newer first")

    def test_recency_cannot_overturn_real_relevance(self):
        """The nudge is capped at 1.15x, so it can never flip a coverage gap."""
        new_weak = row(rid="new", title="ledger", ts=9_999_999)
        old_strong = row(rid="old", title="ledger migration reconciliation", ts=1)
        out = mr.rank([new_weak, old_strong], "ledger migration reconciliation")
        self.assertEqual(out[0]["id"], "old")

    def test_zero_match_rows_sort_last(self):
        hit = row(rid="hit", title="migration", ts=1)
        miss = row(rid="miss", title="nothing here", ts=9_999_999)
        out = mr.rank([miss, hit], "migration")
        self.assertEqual(out[0]["id"], "hit")

    def test_empty_query_falls_back_to_recency(self):
        old, new = row(rid="old", ts=1), row(rid="new", ts=5)
        self.assertEqual([r["id"] for r in mr.rank([old, new], "")], ["new", "old"])

    def test_stopword_only_query_falls_back_to_recency(self):
        old, new = row(rid="old", ts=1), row(rid="new", ts=5)
        self.assertEqual([r["id"] for r in mr.rank([old, new], "the and of a")],
                         ["new", "old"])

    def test_deterministic_across_runs(self):
        rows = [row(rid=f"id-{i}", title="ledger migration", ts=0) for i in range(10)]
        a = [r["id"] for r in mr.rank(list(rows), "ledger")]
        b = [r["id"] for r in mr.rank(list(rows), "ledger")]
        self.assertEqual(a, b)

    def test_empty_input(self):
        self.assertEqual(mr.rank([], "anything"), [])

    def test_idf_downweights_a_term_present_in_everything(self):
        """A term every candidate shares carries no signal; the rare one decides."""
        rows = [
            row(rid="common-only", title="project alpha", ts=9_999_999),
            row(rid="has-rare", title="project reconciliation", ts=1),
        ]
        out = mr.rank(rows, "project reconciliation")
        self.assertEqual(out[0]["id"], "has-rare")

    def test_tags_are_searched(self):
        tagged = row(rid="tagged", title="unrelated", tags=["migration"], ts=1)
        out = mr.rank([row(rid="plain", title="unrelated", ts=5), tagged], "migration")
        self.assertEqual(out[0]["id"], "tagged")


class ExplainTest(unittest.TestCase):
    def test_explain_reports_matched_terms_and_field(self):
        r = row(rid="x", title="ledger migration", ts=1)
        e = mr.explain(r, "ledger migration missingterm")
        self.assertEqual(sorted(e["matched"]), ["ledger", "migration"])
        self.assertEqual(e["fields"]["ledger"], "title")
        self.assertIsNone(e["fields"]["missingterm"])
        self.assertAlmostEqual(e["coverage"], 2 / 3, places=3)


class JoinKeyTest(unittest.TestCase):
    """recall() must emit returned_paths -- the join key to tool-trace spans.

    A PostToolUse hook already records every file open with session.id and an
    absolute path. The read row carried memory IDS and no PATHS, so there was
    nothing to join on. That single gap, not missing instrumentation, is why
    usefulness was unmeasurable.
    """

    def test_facade_emits_paths_aligned_with_ids(self):
        import json, os, tempfile
        import memory_facade as mf
        with tempfile.TemporaryDirectory() as d:
            tp = os.path.join(d, "t.jsonl")
            prev = dict(os.environ)
            os.environ["BUILD_LOOP_TELEMETRY_SOURCE"] = "test"
            os.environ["BUILD_LOOP_TEST_TELEMETRY_PATH"] = tp
            try:
                merged = [row(rid="a", title="ledger", ts=1, path="/tmp/a.md"),
                          row(rid="b", title="ledger migration", ts=2, path="/tmp/b.md")]
                mf._emit_telemetry(mr.rank(merged, "ledger migration"), "ledger migration")
                emitted = json.loads(open(tp).read().strip().splitlines()[-1])
            finally:
                os.environ.clear(); os.environ.update(prev)
        self.assertEqual(len(emitted["returned_paths"]), len(emitted["memory_ids_seen"]),
                         "paths must be index-aligned with ids or the join is wrong")
        self.assertTrue(all(p.startswith("/") for p in emitted["returned_paths"]),
                        "paths must be absolute to match tool-trace spans")


class ExposureTest(unittest.TestCase):
    """rank() must expose the score and position it already computes.

    Propensity cannot be reconstructed after the fact: once a use-signal exists,
    "this memory was opened" means different things at rank 0 and rank 40.
    """

    def test_rank_attaches_score_and_position(self):
        rows = [row(rid="a", title="ledger migration", ts=1),
                row(rid="b", title="unrelated", ts=5)]
        out = mr.rank(rows, "ledger migration")
        self.assertEqual([r["_rank"] for r in out], [0, 1])
        self.assertTrue(all("_rank_score" in r for r in out))

    def test_scores_are_descending_and_match_order(self):
        rows = [row(rid=f"r{i}", title=t, ts=1) for i, t in enumerate(
            ["ledger migration reconciliation", "ledger migration", "ledger", "nothing"])]
        out = mr.rank(rows, "ledger migration reconciliation")
        scores = [r["_rank_score"] for r in out]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_rank_is_position_in_returned_order(self):
        rows = [row(rid=f"r{i}", title="ledger", ts=i) for i in range(5)]
        out = mr.rank(rows, "ledger")
        self.assertEqual([r["_rank"] for r in out], list(range(5)))

    def test_zero_match_row_still_carries_exposure(self):
        """A row that matched nothing was still SHOWN; it needs a rank."""
        out = mr.rank([row(rid="miss", title="nothing here", ts=1)], "ledger")
        self.assertEqual(out[0]["_rank"], 0)
        self.assertEqual(out[0]["_rank_score"], 0.0)


class WiringTest(unittest.TestCase):
    """Both merge sites must rank by relevance and both must stay reversible."""

    def setUp(self):
        import os
        self._prev = os.environ.get("BUILD_LOOP_MEMORY_RANK")
        self.rows = [
            row(rid="newest-irrelevant", title="unrelated notes", ts=9_999_999),
            row(rid="older-match", title="ledger migration reconciliation", ts=1),
        ]
        self.q = "ledger migration reconciliation"

    def tearDown(self):
        import os
        if self._prev is None:
            os.environ.pop("BUILD_LOOP_MEMORY_RANK", None)
        else:
            os.environ["BUILD_LOOP_MEMORY_RANK"] = self._prev

    def _orderers(self):
        import memory_facade as mf
        import context_bootstrap as cb
        return {"memory_facade._order": mf._order,
                "context_bootstrap._order_by_relevance": cb._order_by_relevance}

    def test_both_merge_sites_rank_by_relevance(self):
        import os
        os.environ["BUILD_LOOP_MEMORY_RANK"] = "1"
        for name, fn in self._orderers().items():
            with self.subTest(site=name):
                self.assertEqual(fn(list(self.rows), self.q)[0]["id"], "older-match")

    def test_env_flag_restores_recency_at_both_sites(self):
        import os
        os.environ["BUILD_LOOP_MEMORY_RANK"] = "0"
        for name, fn in self._orderers().items():
            with self.subTest(site=name):
                self.assertEqual(fn(list(self.rows), self.q)[0]["id"], "newest-irrelevant")

    def test_neither_site_drops_rows(self):
        for name, fn in self._orderers().items():
            with self.subTest(site=name):
                self.assertEqual(len(fn(list(self.rows), self.q)), len(self.rows))

    def test_empty_input_is_safe_at_both_sites(self):
        for name, fn in self._orderers().items():
            with self.subTest(site=name):
                self.assertEqual(fn([], self.q), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
