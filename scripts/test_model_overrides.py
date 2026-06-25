#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for dispatch task ids and model override resolution."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISPATCH_ID = HERE / "dispatch_identity.py"
MODEL_OVERRIDES = HERE / "model_overrides.py"
ROUTE_DECISION = HERE / "route_decision.py"
TASK_ID_RE = re.compile(r"^t-[0-9a-f]{8}$")


def run_script(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(path), *args],
        check=False,
        capture_output=True,
        text=True,
    )


class DispatchIdentityTests(unittest.TestCase):
    def test_plain_task_id_matches_contract(self) -> None:
        result = run_script(DISPATCH_ID, "--plain")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.strip(), TASK_ID_RE)

    def test_validate_rejects_bad_task_id(self) -> None:
        result = run_script(DISPATCH_ID, "--validate", "bad-id", "--json")
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["valid"])


class ModelOverrideTests(unittest.TestCase):
    def test_config_override_wins_over_state_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            build_loop = workdir / ".build-loop"
            build_loop.mkdir()
            (build_loop / "config.json").write_text(
                json.dumps({"modelOverrides": {"code": "gpt-5-codex"}}),
                encoding="utf-8",
            )
            (build_loop / "state.json").write_text(
                json.dumps({"config": {"modelOverrides": {"code": "sonnet"}}}),
                encoding="utf-8",
            )

            result = run_script(
                MODEL_OVERRIDES,
                "--workdir", str(workdir),
                "--tier", "code",
                "--fallback", "sonnet",
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-5-codex")
            self.assertEqual(payload["source"], "config")
            self.assertTrue(payload["configured"])

    def test_fallback_when_no_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_script(
                MODEL_OVERRIDES,
                "--workdir", td,
                "--tier", "pattern",
                "--fallback", "haiku",
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "haiku")
            self.assertEqual(payload["source"], "fallback")
            self.assertFalse(payload["configured"])

    def test_tier_default_used_when_no_override_or_fallback(self) -> None:
        # Without an explicit fallback, the tier's built-in default resolves.
        # frontier -> fable, thinking -> opus, code -> sonnet, pattern -> haiku.
        with tempfile.TemporaryDirectory() as td:
            for tier, expected in (
                ("frontier", "fable"),
                ("thinking", "opus"),
                ("code", "sonnet"),
                ("pattern", "haiku"),
            ):
                with self.subTest(tier=tier):
                    result = run_script(
                        MODEL_OVERRIDES,
                        "--workdir", td,
                        "--tier", tier,
                        "--json",
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["model"], expected)
                    self.assertEqual(payload["source"], "tier-default")
                    self.assertFalse(payload["configured"])

    def test_frontier_tier_accepts_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            build_loop = workdir / ".build-loop"
            build_loop.mkdir()
            (build_loop / "config.json").write_text(
                json.dumps({"modelOverrides": {"frontier": "gpt-5-thinking-pro"}}),
                encoding="utf-8",
            )
            result = run_script(
                MODEL_OVERRIDES,
                "--workdir", str(workdir),
                "--tier", "frontier",
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-5-thinking-pro")
            self.assertEqual(payload["source"], "config")
            self.assertTrue(payload["configured"])

    def test_explicit_fallback_beats_tier_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_script(
                MODEL_OVERRIDES,
                "--workdir", td,
                "--tier", "code",
                "--fallback", "gpt-5-codex",
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-5-codex")
            self.assertEqual(payload["source"], "fallback")


class TierFallbackTests(unittest.TestCase):
    """Standing tier->tier fallback policy (TIER_FALLBACK) and its invariant."""

    def _resolve(self, td: str, tier: str, unavailable: str) -> dict:
        result = run_script(
            MODEL_OVERRIDES,
            "--workdir", td,
            "--tier", tier,
            "--unavailable", unavailable,
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_no_fallback_when_tier_model_available(self) -> None:
        # unavailable set doesn't touch this tier's default -> base resolution.
        with tempfile.TemporaryDirectory() as td:
            payload = self._resolve(td, "code", "fable,opus")
            self.assertEqual(payload["model"], "sonnet")
            self.assertEqual(payload["source"], "tier-default")

    def test_thinking_walks_to_code(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            payload = self._resolve(td, "thinking", "opus")
            self.assertEqual(payload["model"], "sonnet")  # code default
            self.assertEqual(payload["source"], "tier-fallback")
            self.assertEqual(payload["fallback_tier"], "code")

    def test_code_walks_to_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            payload = self._resolve(td, "code", "sonnet")
            self.assertEqual(payload["model"], "haiku")  # pattern default
            self.assertEqual(payload["source"], "tier-fallback")
            self.assertEqual(payload["fallback_tier"], "pattern")

    def test_thinking_keeps_walking_to_pattern_when_code_also_unavailable(self) -> None:
        # Non-frontier tiers may walk multiple edges down the graph.
        with tempfile.TemporaryDirectory() as td:
            payload = self._resolve(td, "thinking", "opus,sonnet")
            self.assertEqual(payload["model"], "haiku")  # pattern default
            self.assertEqual(payload["source"], "tier-fallback")
            self.assertEqual(payload["fallback_tier"], "pattern")

    def test_frontier_falls_back_to_thinking(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            payload = self._resolve(td, "frontier", "fable")
            self.assertEqual(payload["model"], "opus")  # thinking default
            self.assertEqual(payload["source"], "tier-fallback")
            self.assertEqual(payload["fallback_tier"], "thinking")

    def test_invariant_frontier_never_resolves_below_thinking(self) -> None:
        # HARD INVARIANT: even when BOTH the frontier default (fable) AND the
        # thinking default (opus) are unavailable, a frontier (judgment) role
        # must NOT silently resolve to the code (sonnet) or pattern (haiku) tier.
        with tempfile.TemporaryDirectory() as td:
            payload = self._resolve(td, "frontier", "fable,opus")
            self.assertEqual(payload["fallback_tier"], "thinking")
            self.assertNotEqual(payload["model"], "sonnet")  # never code tier
            self.assertNotEqual(payload["model"], "haiku")  # never pattern tier
            self.assertNotIn(payload.get("fallback_tier"), {"code", "pattern"})
            # The walk stopped at thinking — code/pattern were never visited.
            self.assertNotIn("code", payload.get("fallback_path", []))
            self.assertNotIn("pattern", payload.get("fallback_path", []))

    def test_explicit_fallback_overrides_standing_policy(self) -> None:
        # An explicit caller --fallback wins; the standing walk is skipped even
        # when the tier default is unavailable.
        with tempfile.TemporaryDirectory() as td:
            result = run_script(
                MODEL_OVERRIDES,
                "--workdir", td,
                "--tier", "frontier",
                "--unavailable", "fable",
                "--fallback", "gpt-5.5",
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-5.5")
            self.assertEqual(payload["source"], "fallback")

    def test_config_override_below_floor_is_clamped_at_source(self) -> None:
        # The f1 production reproduction: the model_overrides.py CLI (the path the
        # orchestrator dispatch docs point at) must NOT return a sub-floor config
        # override. modelOverrides.frontier=haiku + all frontier models down ->
        # opus (thinking floor), never haiku/sonnet. Enforced at the source so
        # every caller inherits it, not just the model_resolver wrapper.
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            bl = workdir / ".build-loop"
            bl.mkdir()
            for sub_floor in ("haiku", "sonnet"):
                (bl / "config.json").write_text(
                    json.dumps({"modelOverrides": {"frontier": sub_floor}}),
                    encoding="utf-8",
                )
                result = run_script(
                    MODEL_OVERRIDES,
                    "--workdir", str(workdir),
                    "--tier", "frontier",
                    "--unavailable", "fable,gpt-5.5,gpt-5.4",
                    "--json",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["model"], "opus", f"{sub_floor}: {payload}")
                self.assertNotEqual(payload["model"], sub_floor)

    def test_config_override_at_floor_is_allowed(self) -> None:
        # opus is the frontier floor (thinking) — a frontier override to opus is
        # legitimate and must NOT be clamped.
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            bl = workdir / ".build-loop"
            bl.mkdir()
            (bl / "config.json").write_text(
                json.dumps({"modelOverrides": {"frontier": "opus"}}), encoding="utf-8"
            )
            result = run_script(
                MODEL_OVERRIDES, "--workdir", str(workdir),
                "--tier", "frontier", "--unavailable", "fable", "--json",
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "opus")

    def test_unknown_config_override_not_clamped(self) -> None:
        # A brand-new unregistered override can't be proven below floor — kept.
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            bl = workdir / ".build-loop"
            bl.mkdir()
            (bl / "config.json").write_text(
                json.dumps({"modelOverrides": {"frontier": "brand-new-x"}}),
                encoding="utf-8",
            )
            result = run_script(
                MODEL_OVERRIDES, "--workdir", str(workdir),
                "--tier", "frontier", "--unavailable", "fable", "--json",
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "brand-new-x")

    def test_explicit_fallback_below_floor_is_caller_intent(self) -> None:
        # An explicit per-call --fallback is deliberate caller intent — exempt
        # from the floor clamp (same as today; the caller owns that choice).
        with tempfile.TemporaryDirectory() as td:
            result = run_script(
                MODEL_OVERRIDES, "--workdir", td,
                "--tier", "frontier", "--fallback", "haiku",
                "--unavailable", "fable", "--json",
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "haiku")

    def test_pattern_has_no_lower_fallback(self) -> None:
        # pattern is the bottom of the graph; an unavailable pattern default
        # returns the base resolution unchanged (no further walk).
        with tempfile.TemporaryDirectory() as td:
            payload = self._resolve(td, "pattern", "haiku")
            self.assertEqual(payload["model"], "haiku")
            self.assertEqual(payload["source"], "tier-default")


class ModelRegistryTests(unittest.TestCase):
    def test_list_models_registers_codex_frontier_and_nano_pattern(self) -> None:
        result = run_script(MODEL_OVERRIDES, "--list-models", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        registry = json.loads(result.stdout)
        frontier_ids = [e["id"] for e in registry["frontier"]]
        pattern_ids = [e["id"] for e in registry["pattern"]]
        # Codex (GPT-5.5) selectable as frontier, alongside the Anthropic default.
        self.assertIn("gpt-5.5", frontier_ids)
        self.assertIn("fable", frontier_ids)
        # nano selectable as pattern; other providers registered too.
        self.assertIn("gpt-5-nano", pattern_ids)

    def test_list_models_tier_filter(self) -> None:
        result = run_script(MODEL_OVERRIDES, "--list-models", "--tier", "frontier", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        registry = json.loads(result.stdout)
        self.assertEqual(set(registry), {"frontier"})

    def test_registered_flag_true_for_registered_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_script(
                MODEL_OVERRIDES,
                "--workdir", td,
                "--tier", "frontier",
                "--fallback", "gpt-5.5",
                "--json",
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-5.5")
            self.assertTrue(payload["registered"])

    def test_unregistered_override_still_resolves(self) -> None:
        # The registry is advisory — an unknown model id still resolves (any
        # string is accepted), it is just flagged registered=false.
        with tempfile.TemporaryDirectory() as td:
            result = run_script(
                MODEL_OVERRIDES,
                "--workdir", td,
                "--tier", "frontier",
                "--fallback", "some-future-model-9000",
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "some-future-model-9000")
            self.assertFalse(payload["registered"])

    def test_tier_default_is_registered(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_script(MODEL_OVERRIDES, "--workdir", td, "--tier", "pattern", "--json")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "haiku")
            self.assertTrue(payload["registered"])

    def test_tier_required_without_list(self) -> None:
        result = run_script(MODEL_OVERRIDES, "--workdir", ".")
        self.assertNotEqual(result.returncode, 0)


class CanonicalAliasTests(unittest.TestCase):
    """GAP 1 regression: an outage by canonical id must match the registry alias."""

    def setUp(self) -> None:
        import importlib

        self.mo = importlib.import_module("model_overrides")

    def test_normalize_canonical_to_alias(self) -> None:
        self.assertEqual(self.mo.normalize_model_id("claude-fable-5"), "fable")
        self.assertEqual(self.mo.normalize_model_id("claude-opus-4-8"), "opus")
        self.assertEqual(self.mo.normalize_model_id("claude-opus-4-8[1m]"), "opus")
        self.assertEqual(self.mo.normalize_model_id("claude-sonnet-4-6"), "sonnet")
        self.assertEqual(self.mo.normalize_model_id("claude-haiku-4-5"), "haiku")

    def test_normalize_alias_is_identity(self) -> None:
        self.assertEqual(self.mo.normalize_model_id("fable"), "fable")

    def test_normalize_unknown_passes_through(self) -> None:
        self.assertEqual(self.mo.normalize_model_id("brand-new-x"), "brand-new-x")
        self.assertIsNone(self.mo.normalize_model_id(None))

    def test_expand_unavailable_covers_both_forms(self) -> None:
        # A model down by canonical id marks the alias unavailable too, and vice versa.
        exp = self.mo.expand_unavailable({"claude-fable-5"})
        self.assertIn("fable", exp)
        self.assertIn("claude-fable-5", exp)
        exp2 = self.mo.expand_unavailable({"fable"})
        self.assertIn("fable", exp2)
        self.assertIn("claude-fable-5", exp2)

    def test_canonical_id_outage_fires_fallback_at_source(self) -> None:
        # The GAP-1 production repro at the model_overrides.py CLI: a frontier
        # outage named by the CANONICAL id must fall back, not return the dead
        # model. claude-fable-5 + cross-vendor frontier down -> opus.
        with tempfile.TemporaryDirectory() as td:
            result = run_script(
                MODEL_OVERRIDES, "--workdir", td, "--tier", "frontier",
                "--unavailable", "claude-fable-5,gpt-5.5,gpt-5.4", "--json",
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "opus", payload)
            self.assertNotEqual(payload["model"], "fable")


class TierRankHelperTests(unittest.TestCase):
    """The shared tier-rank helpers used by the model_resolver floor clamp."""

    def setUp(self) -> None:
        import importlib

        self.mo = importlib.import_module("model_overrides")

    def test_tier_of_model_resolves_registry_ids(self) -> None:
        self.assertEqual(self.mo.tier_of_model("fable"), "frontier")
        self.assertEqual(self.mo.tier_of_model("opus"), "thinking")
        self.assertEqual(self.mo.tier_of_model("sonnet"), "code")
        self.assertEqual(self.mo.tier_of_model("haiku"), "pattern")

    def test_tier_of_unknown_model_is_none(self) -> None:
        self.assertIsNone(self.mo.tier_of_model("brand-new-9000"))
        self.assertIsNone(self.mo.tier_of_model(None))

    def test_is_below_floor_true_for_lower_tier(self) -> None:
        # haiku (pattern) and sonnet (code) are below the thinking floor.
        self.assertTrue(self.mo.is_below_floor("haiku", "thinking"))
        self.assertTrue(self.mo.is_below_floor("sonnet", "thinking"))

    def test_is_below_floor_false_at_or_above_floor(self) -> None:
        self.assertFalse(self.mo.is_below_floor("opus", "thinking"))  # at floor
        self.assertFalse(self.mo.is_below_floor("fable", "thinking"))  # above

    def test_is_below_floor_keeps_unknown_models(self) -> None:
        # Unknown models can't be proven below floor — never clamped.
        self.assertFalse(self.mo.is_below_floor("brand-new-9000", "thinking"))


class RouteDecisionOverrideTests(unittest.TestCase):
    def test_route_decision_reads_config_model_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = root / "plan.md"
            config = root / "config.json"
            state = root / "state.json"
            plan.write_text(
                textwrap.dedent(
                    """\
                    # Low density

                    synthesis_dimensions:
                      one: x

                    ## Body
                    """
                ),
                encoding="utf-8",
            )
            config.write_text(
                json.dumps({"modelOverrides": {"thinking": "gpt-5-thinking"}}),
                encoding="utf-8",
            )

            result = run_script(
                ROUTE_DECISION,
                "--plan", str(plan),
                "--config", str(config),
                "--state", str(state),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["tier"], "thinking")
            self.assertEqual(payload["reason"], "explicit-override")
            self.assertEqual(payload["synthesis_dimensions_count"], 1)


class TwoAxisRoleTests(unittest.TestCase):
    """The two-axis taxonomy layer: alias back-compat through the ladder,
    resolve_role, recency tiebreak, and the floor invariant under the 7-rung
    ladder. These are ADDITIVE — the legacy tests above must stay green."""

    def setUp(self) -> None:
        import importlib
        self.mo = importlib.import_module("model_overrides")

    # --- Alias back-compat: legacy tokens resolve the SAME models ----------
    def test_tier_defaults_derived_from_taxonomy_unchanged(self) -> None:
        self.assertEqual(self.mo.TIER_DEFAULTS, {
            "frontier": "fable", "thinking": "opus",
            "code": "sonnet", "pattern": "haiku",
        })

    def test_tier_fallback_chain_unchanged(self) -> None:
        self.assertEqual(self.mo.TIER_FALLBACK, {
            "frontier": "thinking", "thinking": "code",
            "code": "pattern", "pattern": None,
        })

    def test_legacy_tier_normalizes_to_ladder_rung(self) -> None:
        import importlib
        mt = importlib.import_module("model_taxonomy")
        self.assertEqual(mt.normalize_tier("frontier"), "T1")
        self.assertEqual(mt.normalize_tier("pattern"), "T4")

    # --- resolve_role two-axis entrypoint ---------------------------------
    def test_resolve_role_generative_reasoning_frontier(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = self.mo.resolve_role(
                segment="generative_reasoning", tier="frontier",
                workdir=Path(td),
            )
            self.assertEqual(r["model"], "fable")
            self.assertEqual(r["tier"], "T1")
            self.assertEqual(r["source"], "role-preferred")

    def test_resolve_role_accepts_ladder_tier_directly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = self.mo.resolve_role(
                segment="generative_reasoning", tier="T4", workdir=Path(td),
            )
            self.assertEqual(r["model"], "haiku")

    def test_resolve_role_unavailable_falls_to_next_preferred(self) -> None:
        # GR/T2 preferred = [opus, gpt-5.5]; opus down -> gpt-5.5.
        with tempfile.TemporaryDirectory() as td:
            r = self.mo.resolve_role(
                segment="generative_reasoning", tier="thinking",
                workdir=Path(td), unavailable={"opus"},
            )
            self.assertEqual(r["model"], "gpt-5.5")

    def test_resolve_role_floor_inherited_for_generative(self) -> None:
        # GR/T1 = [fable]; fable down + the whole T1 cell exhausted -> the
        # legacy ladder floor walk takes over and stops at thinking (opus),
        # never below. Floor invariant preserved under the new ladder.
        with tempfile.TemporaryDirectory() as td:
            r = self.mo.resolve_role(
                segment="generative_reasoning", tier="frontier",
                workdir=Path(td), unavailable={"fable"},
            )
            self.assertEqual(r["model"], "opus")
            self.assertNotEqual(r["model"], "sonnet")
            self.assertNotEqual(r["model"], "haiku")

    def test_resolve_role_specialist_segment_unresolved_when_unavailable(self) -> None:
        # representation_retrieval is off-ladder (T-S only); if its sole model
        # is down there is no generative ladder to walk -> unresolved.
        with tempfile.TemporaryDirectory() as td:
            r = self.mo.resolve_role(
                segment="representation_retrieval", tier="T-S",
                workdir=Path(td),
                unavailable={"openai-text-embedding-3-large",
                             "openai-text-embedding-3-small"},
            )
            self.assertEqual(r["source"], "unresolved")
            self.assertIsNone(r["model"])

    def test_resolve_role_unknown_segment_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                self.mo.resolve_role(
                    segment="no-such-segment", tier="T1", workdir=Path(td),
                )

    # --- Recency tiebreak --------------------------------------------------
    def test_recency_breaks_tie_among_equal_rank(self) -> None:
        # Within a single (segment,tier) cell every entry shares the rung, so
        # recency is a pure in-cell tiebreak. GR/T2 = [opus(2025-11),
        # gpt-5.5(2026-02)]; with recency_tiebreak the newer gpt-5.5 is tried
        # FIRST. With opus available, recency still prefers the newer model.
        with tempfile.TemporaryDirectory() as td:
            r = self.mo.resolve_role(
                segment="generative_reasoning", tier="thinking",
                workdir=Path(td), recency_tiebreak=True,
            )
            self.assertEqual(r["model"], "gpt-5.5")  # newer than opus

    def test_no_recency_keeps_capability_order(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = self.mo.resolve_role(
                segment="generative_reasoning", tier="thinking",
                workdir=Path(td), recency_tiebreak=False,
            )
            self.assertEqual(r["model"], "opus")  # list order preserved


if __name__ == "__main__":
    unittest.main(verbosity=2)
