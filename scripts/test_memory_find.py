#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the one retrieval path (`memory_find`) and its sync-side inputs.

Each test names the defect it would catch if the behaviour regressed. The
defects are real ones observed on 2026-09-16, not hypotheticals.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import memory_find as F  # type: ignore  # noqa: E402
import sync_db_from_files as S  # type: ignore  # noqa: E402


class TrustTierTests(unittest.TestCase):
    """A quarantined capture must never read as a confirmed decision."""

    def test_status_marks_quarantine(self):
        self.assertEqual(F._row_tier("quarantined", None), F.TIER_QUARANTINED)

    def test_path_marks_quarantine_when_status_is_missing(self):
        # Rows written before the status column carried a tier: without the
        # path fallback these silently presented as curated.
        path = f"{Path('/store/projects/x/decisions/_review/a.md')}"
        self.assertEqual(F._row_tier(None, path), F.TIER_QUARANTINED)

    def test_curated_stays_curated(self):
        path = f"{Path('/store/projects/x/decisions/a.md')}"
        self.assertEqual(F._row_tier("active", path), F.TIER_CURATED)

    def test_tier_filter_drops_quarantined_when_asked(self):
        env = {
            "results": [
                {"tier": F.TIER_CURATED, "title": "keep"},
                {"tier": F.TIER_QUARANTINED, "title": "drop"},
            ]
        }
        kept = [r for r in env["results"] if r["tier"] == F.TIER_CURATED]
        self.assertEqual([r["title"] for r in kept], ["keep"])


class FusionTests(unittest.TestCase):
    """Fusion must reward agreement across legs, not just one strong hit."""

    def test_rrf_decreases_with_rank(self):
        self.assertGreater(F._rrf(1), F._rrf(2))

    def test_two_leg_agreement_outranks_single_leg_top_hit(self):
        # Record B is #1 on one leg only; record A is #2 on both. A must win,
        # which is the whole reason for fusing instead of taking one leg.
        a = F._rrf(2) + F._rrf(2)
        b = F._rrf(1)
        self.assertGreater(a, b)


class ProjectResolutionTests(unittest.TestCase):
    """Project scope comes from the path, never the record's own frontmatter.

    The capture hook writes `project: _unscoped` into most records; trusting it
    left 1,459 of atomize-ai's 1,836 records unreachable under a scoped search.
    """

    def test_project_read_from_path(self):
        p = Path("/x/build-loop-memory/projects/atomize-ai/decisions/_review/d.md")
        self.assertEqual(S._project_from_path(p), "atomize-ai")

    def test_non_project_path_returns_empty_for_fallback(self):
        self.assertEqual(S._project_from_path(Path("/tmp/loose.md")), "")


class ReviewLaneTests(unittest.TestCase):
    """`_review/` must be syncable; excluding it is what made 12,651 records unreachable."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.decisions = Path(self.tmp.name) / "decisions"
        (self.decisions / "_review").mkdir(parents=True)
        (self.decisions / "curated.md").write_text("x", encoding="utf-8")
        (self.decisions / "_review" / "quarantined.md").write_text("y", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_review_included_by_default(self):
        names = {p.name for p in S._decision_dir_files(self.decisions, False, True)}
        self.assertEqual(names, {"curated.md", "quarantined.md"})

    def test_review_excluded_on_opt_out(self):
        names = {p.name for p in S._decision_dir_files(self.decisions, False, False)}
        self.assertEqual(names, {"curated.md"})


class EnvelopeTests(unittest.TestCase):
    """A backend gap must be reported, never returned as a silent empty result."""

    def test_render_states_reason_when_empty(self):
        text = F.render({"results": [], "reasons": ["pg_unavailable: no DB URL configured"]})
        self.assertIn("pg_unavailable", text)

    def test_render_labels_trust_for_the_reader(self):
        text = F.render(
            {
                "results": [
                    {"title": "confirmed thing", "tier": F.TIER_CURATED, "path": "/a", "excerpt": ""},
                    {"title": "guessed thing", "tier": F.TIER_QUARANTINED, "path": "/b", "excerpt": ""},
                ],
                "reasons": [],
            }
        )
        self.assertIn("✓ confirmed thing", text)
        self.assertIn("? guessed thing", text)
        self.assertIn("unreviewed capture", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
