#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the two-axis model taxonomy loader (scripts/model_taxonomy.py)."""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


class TaxonomyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mt = importlib.import_module("model_taxonomy")

    # --- Ladder structure -------------------------------------------------
    def test_seven_rung_ladder(self) -> None:
        self.assertEqual(
            self.mt.tier_ladder(),
            ("T0", "T1", "T2", "T3", "T4", "T5", "T-S"),
        )

    def test_tier_rank_orders_generative_ladder(self) -> None:
        rank = self.mt.tier_rank()
        # T0 highest capability (rank 0) .. T5 lowest generative rung.
        self.assertEqual(rank["T0"], 0)
        self.assertEqual(rank["T1"], 1)
        self.assertEqual(rank["T2"], 2)
        self.assertEqual(rank["T3"], 3)
        self.assertEqual(rank["T4"], 4)
        self.assertEqual(rank["T5"], 5)
        # T-S is off the capability ladder (sentinel-high).
        self.assertGreater(rank["T-S"], rank["T5"])

    def test_ladder_fallback_is_one_edge_down(self) -> None:
        fb = self.mt.ladder_fallback()
        self.assertEqual(fb["T1"], "T2")
        self.assertEqual(fb["T2"], "T3")
        self.assertEqual(fb["T3"], "T4")
        self.assertEqual(fb["T4"], "T5")
        self.assertIsNone(fb["T5"])
        # Specialist tier never walks the generative fallback.
        self.assertIsNone(fb["T-S"])

    # --- Legacy alias back-compat ----------------------------------------
    def test_legacy_aliases_map_to_ladder(self) -> None:
        self.assertEqual(
            self.mt.legacy_aliases(),
            {"frontier": "T1", "thinking": "T2", "code": "T3", "pattern": "T4"},
        )

    def test_normalize_tier_folds_both_vocabularies(self) -> None:
        # Legacy tokens fold to ladder rungs.
        self.assertEqual(self.mt.normalize_tier("frontier"), "T1")
        self.assertEqual(self.mt.normalize_tier("thinking"), "T2")
        self.assertEqual(self.mt.normalize_tier("code"), "T3")
        self.assertEqual(self.mt.normalize_tier("pattern"), "T4")
        # Ladder rungs pass through unchanged.
        self.assertEqual(self.mt.normalize_tier("T1"), "T1")
        self.assertEqual(self.mt.normalize_tier("T-S"), "T-S")

    def test_normalize_tier_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            self.mt.normalize_tier("bogus")
        with self.assertRaises(ValueError):
            self.mt.normalize_tier("")

    def test_is_legacy_and_is_ladder(self) -> None:
        self.assertTrue(self.mt.is_legacy_tier("frontier"))
        self.assertFalse(self.mt.is_legacy_tier("T1"))
        self.assertTrue(self.mt.is_ladder_tier("T-S"))
        self.assertFalse(self.mt.is_ladder_tier("frontier"))

    # --- Segments ---------------------------------------------------------
    def test_all_seven_segments_present(self) -> None:
        expected = {
            "generative_reasoning", "agentic_execution",
            "representation_retrieval", "realtime_interaction",
            "perception_input", "generative_media", "governance_evaluation",
        }
        self.assertEqual(set(self.mt.segments()), expected)

    def test_segment_status_active_partial_dormant(self) -> None:
        self.assertEqual(self.mt.segment_status("generative_reasoning"), "active")
        self.assertEqual(self.mt.segment_status("agentic_execution"), "active")
        self.assertEqual(self.mt.segment_status("governance_evaluation"), "active")
        self.assertEqual(self.mt.segment_status("representation_retrieval"), "partial")
        self.assertEqual(self.mt.segment_status("realtime_interaction"), "dormant")
        self.assertEqual(self.mt.segment_status("perception_input"), "dormant")
        self.assertEqual(self.mt.segment_status("generative_media"), "dormant")

    def test_active_segments(self) -> None:
        self.assertEqual(
            self.mt.active_segments(),
            ["agentic_execution", "generative_reasoning", "governance_evaluation"],
        )

    def test_every_segment_has_at_least_one_preferred_cell(self) -> None:
        # Uniform data shape (plan-critic finding 1): no segment is missing
        # entirely from the preferred map.
        pref = self.mt.taxonomy()["preferred"]
        for seg in self.mt.segments():
            self.assertIn(seg, pref, f"segment {seg} missing from preferred map")
            # at least one tier cell with at least one model
            cells = {k: v for k, v in pref[seg].items() if not k.startswith("_")}
            self.assertTrue(
                any(isinstance(v, list) and v for v in cells.values()),
                f"segment {seg} has no non-empty preferred cell",
            )

    # --- Preferred lists --------------------------------------------------
    def test_preferred_accepts_both_tier_vocabularies(self) -> None:
        # frontier == T1 for generative_reasoning; provider filtering chooses
        # the host-reachable entry at dispatch.
        self.assertEqual(
            self.mt.preferred("generative_reasoning", "frontier"),
            self.mt.preferred("generative_reasoning", "T1"),
        )
        self.assertEqual(
            self.mt.preferred("generative_reasoning", "T1"),
            ["fable", "gpt-5.6-sol"],
        )

    def test_preferred_empty_cell_returns_list(self) -> None:
        # A dormant segment's non-specialist tier is empty, not an error.
        self.assertEqual(self.mt.preferred("realtime_interaction", "T1"), [])

    # --- Model metadata + recency ----------------------------------------
    def test_model_meta_by_id_and_alias(self) -> None:
        m = self.mt.model_meta("fable")
        self.assertIsNotNone(m)
        self.assertEqual(m["tier"], "T1")
        self.assertEqual(m["segment"], "generative_reasoning")
        # Alias resolves to the same entry.
        via_alias = self.mt.model_meta("claude-fable-5")
        self.assertIsNotNone(via_alias)
        self.assertEqual(via_alias["tier"], "T1")

    def test_model_meta_unknown_returns_none(self) -> None:
        self.assertIsNone(self.mt.model_meta("no-such-model"))
        self.assertIsNone(self.mt.model_meta(None))

    def test_released_dates_present_for_seeds(self) -> None:
        self.assertEqual(self.mt.released("fable"), "2025-11-01")
        self.assertEqual(self.mt.released("gpt-5.5"), "2026-02-01")
        self.assertEqual(self.mt.released("gpt-5.6-sol"), "2026-07-09")

    def test_gpt_5_6_family_is_classified_by_work_role(self) -> None:
        expected = {
            "gpt-5.6-sol": "T1",
            "gpt-5.6-terra": "T2",
            "gpt-5.6-luna": "T4",
        }
        for model, tier in expected.items():
            with self.subTest(model=model):
                meta = self.mt.model_meta(model)
                self.assertIsNotNone(meta)
                self.assertEqual(meta["provider"], "openai")
                self.assertEqual(meta["tier"], tier)

        self.assertIn("gpt-5.6-sol", self.mt.preferred("governance_evaluation", "frontier"))
        self.assertEqual(
            self.mt.preferred("governance_evaluation", "thinking"),
            ["opus", "gpt-5.6-sol"],
        )
        self.assertIn("gpt-5.6-terra", self.mt.preferred("agentic_execution", "code"))
        self.assertIn("gpt-5.6-luna", self.mt.preferred("governance_evaluation", "pattern"))

    def test_break_ties_by_recency_newer_first(self) -> None:
        # gpt-5.5 (2026-02) is newer than opus (2025-11): recency puts it first
        # when comparing the two as equal-rank candidates.
        ordered = self.mt.break_ties_by_recency(["opus", "gpt-5.5"])
        self.assertEqual(ordered[0], "gpt-5.5")

    def test_break_ties_unknown_date_sorts_last(self) -> None:
        ordered = self.mt.break_ties_by_recency(["no-date-model", "fable"])
        self.assertEqual(ordered[0], "fable")

    # --- Classification rubric -------------------------------------------
    def test_classification_rubric_has_segment_hints(self) -> None:
        rubric = self.mt.classification_rubric()
        # Specialist segments grade on their own metrics, not SWE-bench.
        self.assertIn("MTEB", rubric["representation_retrieval"])
        self.assertIn("WER", rubric["realtime_interaction"])
        # Generative segments grade on reasoning/coding benchmarks.
        self.assertIn("SWE-bench", rubric["generative_reasoning"])
        # The primary-role rule is encoded.
        self.assertIn("primary_role_rule", rubric)
        self.assertIn("multimodal-input", rubric["primary_role_rule"])


if __name__ == "__main__":
    unittest.main()
