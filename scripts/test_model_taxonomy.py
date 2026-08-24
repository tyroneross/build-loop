#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the two-axis model taxonomy loader (scripts/model_taxonomy.py)."""
from __future__ import annotations

import importlib
import re
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
        # T1 roster order is capability rank: opus heads it since 2026-07-28,
        # fable is retained as the second frontier choice.
        self.assertEqual(
            self.mt.preferred("generative_reasoning", "T1"),
            ["opus", "fable", "gpt-5.6-sol"],
        )

    def test_preferred_empty_cell_returns_list(self) -> None:
        # A dormant segment's non-specialist tier is empty, not an error.
        self.assertEqual(self.mt.preferred("realtime_interaction", "T1"), [])

    def test_agentic_code_prefers_high_effort(self) -> None:
        self.assertEqual(
            self.mt.preferred_effort("agentic_execution", "code"), "high"
        )
        self.assertEqual(
            self.mt.preferred_effort("agentic_execution", "T3"), "high"
        )

    def test_effort_policy_is_sparse_and_fail_open(self) -> None:
        self.assertIsNone(
            self.mt.preferred_effort("governance_evaluation", "code")
        )
        self.assertIsNone(
            self.mt.preferred_effort("agentic_execution", "bogus")
        )

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
        # opus's date drives the recency tiebreak in every T1/T2 cell it sits
        # in, so pin it explicitly rather than leaving it implicit.
        self.assertEqual(self.mt.released("opus"), "2026-07-25")
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

    def test_break_ties_by_recency_preserves_rank_order(self) -> None:
        """Rank is the only key; this helper must not reorder anything.

        It used to date-sort the whole candidate list. Because the preferred
        list order IS the capability rank, that discarded the ranking and made
        `resolve_role` disagree with `model_index resolve --tier` on T3 and T4.
        Input is deliberately oldest-first, so the old descending-date sort
        would reverse it and fail here.
        """
        candidates = ["fable", "gpt-5.5", "opus"]
        self.assertEqual(self.mt.break_ties_by_recency(candidates), candidates)

    def test_break_ties_does_not_promote_on_unknown_date(self) -> None:
        # A model with no release date must not be moved either — the old
        # implementation sorted it last; rank order says leave it alone.
        candidates = ["no-date-model", "fable"]
        self.assertEqual(self.mt.break_ties_by_recency(candidates), candidates)

    def test_released_still_available_for_display(self) -> None:
        # The date lookup is retained deliberately: it is no longer a
        # resolution key, but it is still shown in resolution envelopes and
        # would back a future per-cell `tiebreak: rank | recency` field.
        self.assertEqual(self.mt.released("opus"), "2026-07-25")

    # --- Prompting profiles (T-01, T-02) -----------------------------------
    def test_prompting_profile_covers_ladder_and_folds_legacy_tokens(self) -> None:
        # T-01: every generative rung (excluding T-S) carries a complete
        # profile: the five posture fields plus confidence + summary.
        required_fields = {
            "examples", "constraint_posture", "edge_case_handling",
            "rationale", "prompt_budget", "confidence", "summary",
        }
        for rung in self.mt.tier_ladder():
            if rung == "T-S":
                continue
            with self.subTest(rung=rung):
                profile = self.mt.prompting_profile(rung)
                self.assertIsNotNone(profile, f"{rung} missing a prompting profile")
                assert profile is not None  # narrow for type-checkers
                self.assertTrue(
                    required_fields.issubset(profile),
                    f"{rung} profile missing fields: {required_fields - set(profile)}",
                )
        # T-S is specialist infrastructure, off the ladder: no profile.
        self.assertIsNone(self.mt.prompting_profile("T-S"))
        # Unknown/None tokens fail open, never raise.
        self.assertIsNone(self.mt.prompting_profile("bogus"))
        self.assertIsNone(self.mt.prompting_profile(None))
        # Legacy tokens normalize to the same ladder rung's profile.
        self.assertEqual(
            self.mt.prompting_profile("code"),
            self.mt.prompting_profile("T3"),
        )

    def test_unprofiled_tiers_detects_gap_in_synthetic_taxonomy(self) -> None:
        # T-02: a synthetic taxonomy dict (never the real data file) with a
        # rung present in tiers.order but missing from prompting_profiles.by_tier.
        synthetic = {
            "tiers": {"order": ["T0", "T1", "T2", "T-S"]},
            "prompting_profiles": {
                "by_tier": {
                    "T0": {"examples": "omit"},
                    "T1": None,
                    # T2 absent entirely.
                    "T-S": None,
                }
            },
        }
        self.assertEqual(
            set(self.mt.unprofiled_tiers(synthetic)),
            {"T1", "T2"},
        )
        # A fully-profiled synthetic taxonomy reports no gaps.
        complete = {
            "tiers": {"order": ["T0", "T1", "T-S"]},
            "prompting_profiles": {
                "by_tier": {
                    "T0": {"examples": "omit"},
                    "T1": {"examples": "worked"},
                    "T-S": None,
                }
            },
        }
        self.assertEqual(self.mt.unprofiled_tiers(complete), [])
        # Default (no arg) reads the real, currently fully-profiled taxonomy.
        self.assertEqual(self.mt.unprofiled_tiers(), [])

    # --- Fallback disposal drift guard (T-09) -----------------------------
    def test_fallbacks_prompt_section_matches_generated_summaries(self) -> None:
        # T-09: skills/build-loop/fallbacks.md#prompt is a generated
        # projection of prompting_profiles.by_tier, not a hand-maintained
        # copy. Every rung's summary string (except T-S, which carries no
        # profile) must appear verbatim in the fallback text, and the
        # retired stale-tier block must not have crept back in.
        fallbacks_path = HERE.parent / "skills" / "build-loop" / "fallbacks.md"
        text = fallbacks_path.read_text(encoding="utf-8")

        for rung in self.mt.tier_ladder():
            if rung == "T-S":
                continue
            with self.subTest(rung=rung):
                profile = self.mt.prompting_profile(rung)
                self.assertIsNotNone(profile, f"{rung} missing a prompting profile")
                assert profile is not None  # narrow for type-checkers
                summary = profile["summary"]
                self.assertIn(
                    summary, text,
                    f"{rung} summary not found verbatim in fallbacks.md#prompt",
                )

        self.assertIn(
            "Edit the taxonomy first",
            text,
            "fallbacks.md#prompt is missing its provenance marker",
        )

        for retired in ("Opus 4.6", "gpt-4-mini", "T1 — Opus"):
            with self.subTest(retired=retired):
                self.assertNotIn(
                    retired, text,
                    f"retired stale-tier string {retired!r} still present in fallbacks.md",
                )

    # --- Markdown projection drift guard (T-10) ---------------------------
    def test_markdown_tables_match_prompting_profiles(self) -> None:
        # T-10: two markdown tables hand-mirror prompting_profiles.by_tier.
        # Both drift silently the moment someone edits the taxonomy alone --
        # the exact failure the retired fallbacks.md block demonstrated, so
        # gate them the same way T-09 gates the fallback text.
        fields = ("examples", "constraint_posture", "edge_case_handling",
                  "rationale", "prompt_budget")

        def cells(line: str) -> list[str]:
            return [c.strip().strip("`*") for c in line.strip().strip("|").split("|")]

        # (a) skills/model-tiering/SKILL.md -- rung-major: one row per rung,
        #     columns in `fields` order.
        skill = (HERE.parent / "skills" / "model-tiering" / "SKILL.md")
        lines = skill.read_text(encoding="utf-8").splitlines()
        seen_rungs = set()
        for line in lines:
            if not line.lstrip().startswith("|"):
                continue
            row = cells(line)
            if not row or row[0] not in self.mt.tier_ladder() or row[0] == "T-S":
                continue
            profile = self.mt.prompting_profile(row[0])
            self.assertIsNotNone(profile, f"{row[0]} has no profile")
            assert profile is not None
            seen_rungs.add(row[0])
            for field, value in zip(fields, row[1:1 + len(fields)]):
                with self.subTest(file="model-tiering/SKILL.md", rung=row[0], field=field):
                    self.assertEqual(
                        value, profile[field],
                        f"SKILL.md row {row[0]} column {field} says {value!r}; "
                        f"references/model-taxonomy.json says {profile[field]!r}",
                    )
        self.assertEqual(
            seen_rungs,
            {r for r in self.mt.tier_ladder() if r != "T-S"},
            "SKILL.md profile table does not cover every ladder rung",
        )

        # (b) references/implementer-brief-template.md -- field-major, with
        #     rungs grouped into columns. Map each column to a representative
        #     rung and assert per cell.
        template = (HERE.parent / "references" / "implementer-brief-template.md")
        tlines = template.read_text(encoding="utf-8").splitlines()
        header: list[str] | None = None
        checked = 0
        for line in tlines:
            if not line.lstrip().startswith("|"):
                continue
            row = cells(line)
            if row and row[0] in ("Profile field", "field"):
                header = row
                continue
            if header is None or not row or row[0] not in fields:
                continue
            field = row[0]
            for col, value in zip(header[1:], row[1:]):
                rungs = re.findall(r"T\d", col)
                for rung in rungs:
                    profile = self.mt.prompting_profile(rung)
                    if profile is None:
                        continue
                    with self.subTest(file="implementer-brief-template.md",
                                      rung=rung, field=field):
                        self.assertEqual(
                            value, profile[field],
                            f"brief template {field} column {col!r} says "
                            f"{value!r}; taxonomy says {profile[field]!r} for {rung}",
                        )
                    checked += 1
        self.assertGreater(
            checked, 0,
            "brief template profile table not found -- T-10 would pass vacuously",
        )

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

    # --- Family inheritance for unregistered models ----------------------
    # 2026-07-25: model_meta was exact-id/alias only, so a SHIPPING model the
    # user was actively routing work to (claude-opus-5) resolved to None --
    # indistinguishable from an invented one. These lock in the three arms.
    def test_unregistered_model_inherits_from_its_family(self) -> None:
        # claude-opus-5 was the original repro but became a CURATED alias on
        # 2026-07-28, so it no longer exercises inheritance (it now covers the
        # curated arm below). Use the next unshipped version of the same family
        # — the mechanism under test is "a new version of a known family must
        # resolve", not any one id.
        meta = self.mt.model_meta("claude-opus-6")
        self.assertIsNotNone(meta, "a new version of a known family must resolve")
        self.assertEqual(meta["inherited_from"], "opus")
        self.assertEqual(meta["status"], "inherited")
        # Tier/segment come from the family row, so routing stays sane.
        self.assertEqual(meta["tier"], self.mt.model_meta("claude-opus-4-8")["tier"])
        self.assertEqual(meta["segment"], self.mt.model_meta("claude-opus-4-8")["segment"])

    def test_curated_rows_are_not_marked_inherited(self) -> None:
        # claude-opus-5 is curated as of 2026-07-28 — an inherited row here
        # would mean the alias silently dropped out of the registry.
        for mid in ("claude-opus-5", "claude-opus-4-8",
                    "claude-fable-5", "claude-sonnet-5"):
            meta = self.mt.model_meta(mid)
            self.assertIsNotNone(meta, mid)
            self.assertNotEqual(meta.get("status"), "inherited", mid)
            self.assertNotIn("inherited_from", meta, mid)

    def test_inheritance_never_invents(self) -> None:
        # Unknown family: no tier may be guessed.
        self.assertIsNone(self.mt.model_meta("claude-zephyr-9"))
        # Never across vendors -- "opus" is an Anthropic family.
        self.assertIsNone(self.mt.model_meta("gpt-opus-9"))
        # Unrecognized vendor token.
        self.assertIsNone(self.mt.model_meta("llama-opus-9"))
        # Degenerate input.
        self.assertIsNone(self.mt.model_meta("x"))
        self.assertIsNone(self.mt.model_meta(""))


if __name__ == "__main__":
    unittest.main()
