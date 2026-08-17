#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/frontier_gate.py."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure scripts/ is importable when run directly via pytest <file>
sys.path.insert(0, str(Path(__file__).parent))

from frontier_gate import (  # noqa: E402
    AUDIT_OBSERVATION,
    DEFAULT_COUPLING_THRESHOLD,
    DEFAULT_TIGHT_INTEGRATION_REPOS,
    GATED_MODEL,
    TIER_DEFAULT_MODEL,
    UNKNOWN,
    VERIFICATION_ROLES,
    collect_signals,
    coupling_density,
    evaluate,
    format_plain,
    load_config,
    main,
    normalize_role,
    normalize_slug,
    repo_slug,
)


# ---------------------------------------------------------------------------
# Helpers — build a synthetic workdir on disk
# ---------------------------------------------------------------------------

class GateTestCase(unittest.TestCase):
    """Base: a throwaway workdir named after the repo slug under test."""

    slug = "some-repo"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.workdir = self.root / self.slug
        self.workdir.mkdir()
        self.addCleanup(self._tmp.cleanup)

    # -- fixture writers ----------------------------------------------------

    def write_config(self, block, *, raw: str | None = None) -> None:
        d = self.workdir / ".build-loop"
        d.mkdir(exist_ok=True)
        text = raw if raw is not None else json.dumps({"frontierGate": block})
        (d / "config.json").write_text(text, encoding="utf-8")

    def write_state(self, state: dict, *, raw: str | None = None) -> None:
        d = self.workdir / ".build-loop"
        d.mkdir(exist_ok=True)
        text = raw if raw is not None else json.dumps(state)
        (d / "state.json").write_text(text, encoding="utf-8")

    def write_graph(
        self,
        nodes: list[str],
        edges: list[tuple[str, str]],
        *,
        where: str = ".build-loop/architecture",
        file_map: dict[str, str] | None = None,
        raw: str | None = None,
    ) -> None:
        d = self.workdir / where
        d.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            (d / "graph.json").write_text(raw, encoding="utf-8")
        else:
            (d / "graph.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "nodes": [{"id": n, "name": n, "layer": "backend"} for n in nodes],
                        "edges": [{"from": f, "to": t, "type": "imports"} for f, t in edges],
                    }
                ),
                encoding="utf-8",
            )
        if file_map is not None:
            (d / "file_map.json").write_text(
                json.dumps({"schema_version": "1.0.0", "files": file_map}), encoding="utf-8"
            )

    def dense_graph(self, size: int = 6) -> None:
        """A fully-connected component set: mean degree = 2*(n-1) — well over 6.0."""
        nodes = [f"COMP_{i}" for i in range(size)]
        edges = [(a, b) for a in nodes for b in nodes if a != b]
        self.write_graph(nodes, edges)

    def sparse_graph(self, size: int = 10) -> None:
        """A chain: mean degree = 2*(n-1)/n < 2 — comfortably under 6.0."""
        nodes = [f"COMP_{i}" for i in range(size)]
        edges = [(nodes[i], nodes[i + 1]) for i in range(size - 1)]
        self.write_graph(nodes, edges)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

class TestNormalization(unittest.TestCase):
    def test_slug_lowercases_and_hyphenates(self) -> None:
        for raw in ("Atomize AI", "atomize_ai", "Atomize-AI", "  ATOMIZE   AI  "):
            self.assertEqual(normalize_slug(raw), "atomize-ai", raw)

    def test_slug_none_when_empty(self) -> None:
        self.assertIsNone(normalize_slug(""))
        self.assertIsNone(normalize_slug(None))
        self.assertIsNone(normalize_slug("///"))

    def test_role_aliases_fold_to_plan_synthesis(self) -> None:
        for raw in ("plan-synthesis", "plan_synthesis", "Phase2-Plan-Synthesis", "phase 2 plan synthesis"):
            self.assertEqual(normalize_role(raw), "plan-synthesis", raw)

    def test_unknown_role_passes_through_normalized(self) -> None:
        self.assertEqual(normalize_role("Some Custom Role"), "some-custom-role")

    def test_repo_slug_falls_back_to_dirname(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp) / "Easy Terminal"
            wd.mkdir()
            self.assertEqual(repo_slug(wd), "easy-terminal")

    def test_repo_slug_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp) / "whatever"
            wd.mkdir()
            self.assertEqual(repo_slug(wd, "Atomize AI"), "atomize-ai")


# ---------------------------------------------------------------------------
# Seeded tight-integration repos
# ---------------------------------------------------------------------------

class TestSeededTightIntegration(GateTestCase):
    def test_seed_list_is_the_two_named_archetypes(self) -> None:
        self.assertEqual(
            set(DEFAULT_TIGHT_INTEGRATION_REPOS), {"atomize-ai", "easy-terminal"}
        )

    def test_atomize_ai_advisor_routes_to_fable(self) -> None:
        result = evaluate(self.workdir, role="advisor", repo="atomize-ai")
        self.assertEqual(result["verdict"], GATED_MODEL)
        self.assertIn("tight_integration", result["fired"])

    def test_easy_terminal_plan_synthesis_routes_to_fable(self) -> None:
        result = evaluate(self.workdir, role="phase2-plan-synthesis", repo="easy-terminal")
        self.assertEqual(result["verdict"], GATED_MODEL)
        self.assertEqual(result["role"], "plan-synthesis")

    def test_slug_inferred_from_directory_name_hits_seed(self) -> None:
        wd = self.root / "Atomize AI"
        wd.mkdir()
        result = evaluate(wd, role="advisor")
        self.assertEqual(result["verdict"], GATED_MODEL)
        self.assertIn("tight_integration", result["fired"])

    def test_unlisted_repo_stays_on_opus(self) -> None:
        result = evaluate(self.workdir, role="advisor", repo="build-loop")
        self.assertEqual(result["verdict"], TIER_DEFAULT_MODEL)
        self.assertEqual(result["fired"], [])

    def test_signal_is_objective_not_self_reported(self) -> None:
        """Source string names the config key + slug it compared, never a confidence."""
        result = evaluate(self.workdir, role="advisor", repo="atomize-ai")
        src = result["signals"]["tight_integration"]["source"]
        self.assertIn("config.frontierGate.tightIntegrationRepos", src)
        self.assertIn("atomize-ai", src)


# ---------------------------------------------------------------------------
# Role allowlist — verification roles are never gated
# ---------------------------------------------------------------------------

class TestRoleAllowlist(GateTestCase):
    def _fire_everything(self) -> None:
        self.dense_graph()
        self.write_state({"runs": [{"synthesisDensity": {"count": 9},
                                    "triggers": {"riskSurfaceChange": True}}]})

    def test_every_verification_role_stays_on_opus_with_all_signals_firing(self) -> None:
        self._fire_everything()
        for role in sorted(VERIFICATION_ROLES):
            with self.subTest(role=role):
                result = evaluate(self.workdir, role=role, repo="atomize-ai")
                self.assertEqual(result["verdict"], TIER_DEFAULT_MODEL)
                # All four signals genuinely fired — the role, not the evidence, held the line.
                self.assertEqual(len(result["fired"]), 4, result["fired"])
                self.assertIn(AUDIT_OBSERVATION, result["reason"])

    def test_named_verification_roles_from_the_spec(self) -> None:
        self._fire_everything()
        for role in ("independent-auditor", "plan-critic", "security-reviewer"):
            with self.subTest(role=role):
                self.assertEqual(
                    evaluate(self.workdir, role=role, repo="atomize-ai")["verdict"],
                    TIER_DEFAULT_MODEL,
                )

    def test_config_cannot_promote_a_verification_role(self) -> None:
        """Hard deny-list outranks config.planningRoles."""
        self.write_config({"planningRoles": ["advisor", "independent-auditor", "security-reviewer"]})
        self._fire_everything()
        cfg = load_config(self.workdir)
        self.assertNotIn("independent-auditor", cfg["planningRoles"])
        self.assertEqual(
            evaluate(self.workdir, role="independent-auditor", repo="atomize-ai")["verdict"],
            TIER_DEFAULT_MODEL,
        )

    def test_unknown_role_is_not_eligible(self) -> None:
        self._fire_everything()
        result = evaluate(self.workdir, role="implementer", repo="atomize-ai")
        self.assertEqual(result["verdict"], TIER_DEFAULT_MODEL)
        self.assertIn("not in the planning allowlist", result["reason"])

    def test_planning_roles_are_eligible(self) -> None:
        for role in ("advisor", "plan-synthesis"):
            with self.subTest(role=role):
                self.assertEqual(
                    evaluate(self.workdir, role=role, repo="atomize-ai")["verdict"], GATED_MODEL
                )


# ---------------------------------------------------------------------------
# Coupling density
# ---------------------------------------------------------------------------

class TestCouplingDensity(GateTestCase):
    def test_no_graph_is_unknown(self) -> None:
        density, source = coupling_density(self.workdir)
        self.assertIsNone(density)
        self.assertIn("no architecture graph", source)

    def test_dense_graph_fires(self) -> None:
        self.dense_graph(size=6)  # mean degree = 2*5 = 10 > 6.0
        density, _ = coupling_density(self.workdir)
        self.assertAlmostEqual(density, 10.0)
        result = evaluate(self.workdir, role="advisor", repo="loose-repo")
        self.assertEqual(result["verdict"], GATED_MODEL)
        self.assertIn("coupling_density", result["fired"])

    def test_sparse_graph_does_not_fire(self) -> None:
        self.sparse_graph(size=10)  # mean degree = 18/10 = 1.8
        density, _ = coupling_density(self.workdir)
        self.assertAlmostEqual(density, 1.8)
        self.assertLess(density, DEFAULT_COUPLING_THRESHOLD)
        self.assertEqual(
            evaluate(self.workdir, role="advisor", repo="loose-repo")["verdict"],
            TIER_DEFAULT_MODEL,
        )

    def test_endpoints_absent_from_node_list_excluded_from_degree(self) -> None:
        self.write_graph(
            nodes=["COMP_a", "COMP_b"],
            edges=[("COMP_a", "COMP_b")] + [("COMP_a", f"PKG_{i}") for i in range(50)],
        )
        density, _ = coupling_density(self.workdir)
        self.assertAlmostEqual(density, 1.0)  # 2 internal degree / 2 components

    def test_external_layer_nodes_excluded_even_when_listed(self) -> None:
        """50 dependencies must not read as tight coupling."""
        d = self.workdir / ".build-loop" / "architecture"
        d.mkdir(parents=True)
        nodes = [{"id": "COMP_a", "layer": "backend"}, {"id": "COMP_b", "layer": "backend"}]
        nodes += [{"id": f"COMP_ext_{i}", "layer": "external"} for i in range(50)]
        edges = [{"from": "COMP_a", "to": "COMP_b", "type": "imports"}]
        edges += [{"from": "COMP_a", "to": f"COMP_ext_{i}", "type": "uses-package"} for i in range(50)]
        (d / "graph.json").write_text(json.dumps({"nodes": nodes, "edges": edges}), encoding="utf-8")

        density, source = coupling_density(self.workdir)
        self.assertAlmostEqual(density, 1.0)
        self.assertIn("2 components", source)
        self.assertEqual(
            evaluate(self.workdir, role="advisor", repo="loose-repo")["verdict"], TIER_DEFAULT_MODEL
        )

    def test_navgator_source_target_edges_are_counted(self) -> None:
        """NavGator emits source/target where build-loop emits from/to.

        Reading only from/to dropped every edge in a .navgator graph, so
        agent-rally-point reported mean degree 0.00 across 671 components while
        its graph held 490 edges — and the signal then read `false` (loosely
        coupled) rather than `unknown`, making a dead sensor look like a real
        measurement.
        """
        d = self.workdir / ".navgator" / "architecture"
        d.mkdir(parents=True)
        nodes = [{"id": "COMP_component_a", "layer": "backend"},
                 {"id": "COMP_component_b", "layer": "backend"}]
        edges = [{"source": "COMP_component_a", "target": "COMP_component_b", "type": "imports"}]
        (d / "graph.json").write_text(json.dumps({"nodes": nodes, "edges": edges}), encoding="utf-8")

        density, _ = coupling_density(self.workdir)
        self.assertAlmostEqual(density, 1.0, msg="source/target edges must count")

    def test_both_edge_vocabularies_counted_in_one_graph(self) -> None:
        d = self.workdir / ".build-loop" / "architecture"
        d.mkdir(parents=True)
        nodes = [{"id": f"COMP_component_{n}", "layer": "backend"} for n in "abc"]
        edges = [{"from": "COMP_component_a", "to": "COMP_component_b"},
                 {"source": "COMP_component_b", "target": "COMP_component_c"}]
        (d / "graph.json").write_text(json.dumps({"nodes": nodes, "edges": edges}), encoding="utf-8")

        density, _ = coupling_density(self.workdir)
        self.assertAlmostEqual(density, 4 / 3, msg="both vocabularies count in one pass")

    def test_external_id_markers_excluded_when_layer_is_missing(self) -> None:
        """Graph producers that omit `layer` still get package nodes filtered."""
        d = self.workdir / ".build-loop" / "architecture"
        d.mkdir(parents=True)
        nodes = [{"id": "COMP_component_a"}, {"id": "COMP_component_b"},
                 {"id": "COMP_pip_package_pytest_400a"}, {"id": "COMP_llm_ollama_8de8"}]
        edges = [{"from": "COMP_component_a", "to": "COMP_component_b"},
                 {"from": "COMP_component_a", "to": "COMP_pip_package_pytest_400a"},
                 {"from": "COMP_component_b", "to": "COMP_llm_ollama_8de8"}]
        (d / "graph.json").write_text(json.dumps({"nodes": nodes, "edges": edges}), encoding="utf-8")

        density, source = coupling_density(self.workdir)
        self.assertAlmostEqual(density, 1.0)  # only the a<->b edge counts
        self.assertIn("2 components", source)

    def test_graph_of_only_external_nodes_is_unknown(self) -> None:
        d = self.workdir / ".build-loop" / "architecture"
        d.mkdir(parents=True)
        (d / "graph.json").write_text(
            json.dumps({"nodes": [{"id": "COMP_x", "layer": "external"}], "edges": []}),
            encoding="utf-8",
        )
        density, source = coupling_density(self.workdir)
        self.assertIsNone(density)
        self.assertIn("no components", source)

    def test_isolated_components_stay_in_denominator(self) -> None:
        self.write_graph(
            nodes=["COMP_a", "COMP_b"] + [f"COMP_iso{i}" for i in range(8)],
            edges=[("COMP_a", "COMP_b")],
        )
        density, _ = coupling_density(self.workdir)
        self.assertAlmostEqual(density, 0.2)  # 2 / 10

    def test_navgator_graph_is_found_as_fallback(self) -> None:
        self.write_graph(
            ["COMP_a", "COMP_b", "COMP_c"],
            [("COMP_a", "COMP_b"), ("COMP_b", "COMP_c"), ("COMP_c", "COMP_a")],
            where=".navgator/architecture",
        )
        density, source = coupling_density(self.workdir)
        self.assertAlmostEqual(density, 2.0)
        self.assertIn(".navgator/architecture", source)

    def test_build_loop_dir_wins_over_navgator(self) -> None:
        self.write_graph(["COMP_a"], [], where=".navgator/architecture")
        self.dense_graph(size=4)
        _, source = coupling_density(self.workdir)
        self.assertIn(".build-loop/architecture", source)

    def test_unparseable_graph_is_unknown(self) -> None:
        self.write_graph([], [], raw="NOT JSON {{{")
        density, source = coupling_density(self.workdir)
        self.assertIsNone(density)
        self.assertIn("unreadable", source)

    def test_empty_graph_is_unknown(self) -> None:
        self.write_graph([], [])
        density, source = coupling_density(self.workdir)
        self.assertIsNone(density)
        self.assertIn("no components", source)

    def test_touched_scoping_narrows_the_mean(self) -> None:
        """A hot component inside an otherwise loose repo."""
        nodes = [f"COMP_{i}" for i in range(12)]
        hub = "COMP_0"
        edges = [(hub, n) for n in nodes[1:]]  # hub degree 11, spokes degree 1
        self.write_graph(nodes, edges, file_map={"src/hub.py": hub, "src/spoke.py": "COMP_1"})

        whole, _ = coupling_density(self.workdir)
        self.assertLess(whole, DEFAULT_COUPLING_THRESHOLD)

        hot, source = coupling_density(self.workdir, touched=["src/hub.py"])
        self.assertAlmostEqual(hot, 11.0)
        self.assertIn("1 touched component", source)

        cold, _ = coupling_density(self.workdir, touched=["src/spoke.py"])
        self.assertAlmostEqual(cold, 1.0)

    def test_unresolvable_touched_path_falls_back_to_whole_repo(self) -> None:
        self.sparse_graph(size=10)
        density, source = coupling_density(self.workdir, touched=["does/not/exist.py"])
        self.assertAlmostEqual(density, 1.8)
        self.assertIn("no touched path resolved", source)


class TestRealRepoCalibration(unittest.TestCase):
    """The threshold must classify this repo — a loosely-coupled script repo — as false."""

    REPO_ROOT = Path(__file__).resolve().parent.parent

    def setUp(self) -> None:
        if not (self.REPO_ROOT / ".build-loop" / "architecture" / "graph.json").is_file():
            self.skipTest("no architecture graph in this checkout")

    def test_build_loop_measures_well_under_threshold(self) -> None:
        density, source = coupling_density(self.REPO_ROOT)
        self.assertIsNotNone(density)
        self.assertLess(density, DEFAULT_COUPLING_THRESHOLD, source)
        self.assertLess(density, 3.0, f"calibration drift: {source}")

    def test_advisor_on_build_loop_stays_on_opus(self) -> None:
        result = evaluate(self.REPO_ROOT, role="advisor")
        self.assertEqual(result["verdict"], TIER_DEFAULT_MODEL)
        self.assertIs(result["signals"]["coupling_density"]["value"], False)


# ---------------------------------------------------------------------------
# State-derived signals
# ---------------------------------------------------------------------------

class TestStateSignals(GateTestCase):
    def _signals(self):
        return collect_signals(self.workdir, load_config(self.workdir), "loose-repo")

    def test_synthesis_density_dict_shape_over_threshold(self) -> None:
        self.write_state({"runs": [{"synthesisDensity": {"count": 7, "escalated": True}}]})
        self.assertIs(self._signals()["synthesis_density"]["value"], True)

    def test_synthesis_density_bare_int_shape(self) -> None:
        self.write_state({"synthesisDensity": 6})
        self.assertIs(self._signals()["synthesis_density"]["value"], True)

    def test_synthesis_density_at_threshold_does_not_fire(self) -> None:
        self.write_state({"synthesisDensity": 5})  # rule is strictly > 5
        self.assertIs(self._signals()["synthesis_density"]["value"], False)

    def test_synthesis_density_absent_is_unknown(self) -> None:
        self.write_state({"runs": []})
        self.assertEqual(self._signals()["synthesis_density"]["value"], UNKNOWN)

    def test_risk_surface_change_true(self) -> None:
        self.write_state({"runs": [{"triggers": {"riskSurfaceChange": True}}]})
        self.assertIs(self._signals()["risk_surface_change"]["value"], True)

    def test_risk_surface_change_false(self) -> None:
        self.write_state({"triggers": {"riskSurfaceChange": False}})
        self.assertIs(self._signals()["risk_surface_change"]["value"], False)

    def test_risk_surface_change_absent_is_unknown(self) -> None:
        self.write_state({})
        self.assertEqual(self._signals()["risk_surface_change"]["value"], UNKNOWN)

    def test_latest_run_wins_over_top_level(self) -> None:
        self.write_state({"synthesisDensity": 0, "runs": [{"synthesisDensity": 1},
                                                          {"synthesisDensity": 9}]})
        self.assertIs(self._signals()["synthesis_density"]["value"], True)


# ---------------------------------------------------------------------------
# Unknown handling — fail toward the more appropriate model
# ---------------------------------------------------------------------------

class TestUnknownHandling(GateTestCase):
    def test_all_unknown_alone_stays_on_opus(self) -> None:
        """Bare repo: no config, no state, no graph. Default must not invert."""
        result = evaluate(self.workdir, role="advisor", repo="loose-repo")
        self.assertEqual(result["verdict"], TIER_DEFAULT_MODEL)
        self.assertEqual(result["signals"]["coupling_density"]["value"], UNKNOWN)
        self.assertEqual(result["signals"]["synthesis_density"]["value"], UNKNOWN)

    def test_corroborating_plus_unknown_coupling_routes_to_fable(self) -> None:
        self.write_state({"runs": [{"triggers": {"riskSurfaceChange": True}}]})
        result = evaluate(self.workdir, role="advisor", repo="loose-repo")
        self.assertEqual(result["verdict"], GATED_MODEL)
        self.assertIn("coupling_density is unknown", result["reason"])

    def test_synthesis_density_plus_unknown_coupling_routes_to_fable(self) -> None:
        self.write_state({"synthesisDensity": {"count": 8}})
        self.assertEqual(
            evaluate(self.workdir, role="advisor", repo="loose-repo")["verdict"], GATED_MODEL
        )

    def test_corroborating_with_MEASURED_low_coupling_stays_on_opus(self) -> None:
        """Once coupling is measurable and low, stakes alone do not gate."""
        self.sparse_graph(size=10)
        self.write_state({"runs": [{"synthesisDensity": 9,
                                    "triggers": {"riskSurfaceChange": True}}]})
        result = evaluate(self.workdir, role="advisor", repo="loose-repo")
        self.assertEqual(result["verdict"], TIER_DEFAULT_MODEL)
        self.assertIs(result["signals"]["coupling_density"]["value"], False)
        self.assertEqual(sorted(result["fired"]), ["risk_surface_change", "synthesis_density"])

    def test_unknown_handling_never_applies_to_verification_roles(self) -> None:
        self.write_state({"runs": [{"triggers": {"riskSurfaceChange": True}}]})
        self.assertEqual(
            evaluate(self.workdir, role="plan-critic", repo="loose-repo")["verdict"],
            TIER_DEFAULT_MODEL,
        )

    def test_unknown_is_the_literal_string_in_the_envelope(self) -> None:
        sig = evaluate(self.workdir, role="advisor", repo="loose-repo")["signals"]
        self.assertEqual(sig["coupling_density"]["value"], "unknown")
        self.assertTrue(json.dumps(sig))  # JSON-serializable


# ---------------------------------------------------------------------------
# Config override + fail-soft
# ---------------------------------------------------------------------------

class TestConfigOverride(GateTestCase):
    def test_custom_tight_integration_list_replaces_seed(self) -> None:
        self.write_config({"tightIntegrationRepos": ["My Repo"]})
        cfg = load_config(self.workdir)
        self.assertEqual(cfg["tightIntegrationRepos"], ["my-repo"])
        self.assertEqual(evaluate(self.workdir, role="advisor", repo="my-repo")["verdict"], GATED_MODEL)
        self.assertEqual(
            evaluate(self.workdir, role="advisor", repo="atomize-ai")["verdict"], TIER_DEFAULT_MODEL
        )

    def test_custom_coupling_threshold(self) -> None:
        self.sparse_graph(size=10)  # density 1.8
        self.write_config({"couplingDensityThreshold": 1.0})
        self.assertEqual(
            evaluate(self.workdir, role="advisor", repo="loose-repo")["verdict"], GATED_MODEL
        )

    def test_custom_synthesis_threshold(self) -> None:
        self.write_config({"synthesisDensityThreshold": 1})
        self.write_state({"synthesisDensity": 2})
        self.assertEqual(
            evaluate(self.workdir, role="advisor", repo="loose-repo")["verdict"], GATED_MODEL
        )

    def test_config_can_add_a_planning_role(self) -> None:
        self.write_config({"planningRoles": ["spec-author"]})
        cfg = load_config(self.workdir)
        self.assertIn("spec-author", cfg["planningRoles"])
        self.assertIn("advisor", cfg["planningRoles"])  # defaults preserved
        self.assertEqual(
            evaluate(self.workdir, role="spec-author", repo="atomize-ai")["verdict"], GATED_MODEL
        )

    def test_enabled_false_disables_the_gate(self) -> None:
        self.write_config({"enabled": False})
        result = evaluate(self.workdir, role="advisor", repo="atomize-ai")
        self.assertEqual(result["verdict"], TIER_DEFAULT_MODEL)
        self.assertIn("enabled is false", result["reason"])
        self.assertIn("tight_integration", result["fired"])  # signal still reported


class TestConfigFailSoft(GateTestCase):
    def _assert_defaults(self) -> None:
        cfg = load_config(self.workdir)
        self.assertEqual(cfg["tightIntegrationRepos"], sorted(DEFAULT_TIGHT_INTEGRATION_REPOS))
        self.assertEqual(cfg["couplingDensityThreshold"], DEFAULT_COUPLING_THRESHOLD)
        self.assertTrue(cfg["enabled"])
        self.assertEqual(
            evaluate(self.workdir, role="advisor", repo="atomize-ai")["verdict"], GATED_MODEL
        )

    def test_missing_config_file(self) -> None:
        self._assert_defaults()

    def test_unparseable_json(self) -> None:
        self.write_config(None, raw="NOT JSON {{{")
        self._assert_defaults()

    def test_missing_frontier_gate_key(self) -> None:
        self.write_config(None, raw=json.dumps({"parallelism": {"maxImplementers": 8}}))
        self._assert_defaults()

    def test_frontier_gate_wrong_type(self) -> None:
        self.write_config(None, raw=json.dumps({"frontierGate": "yes please"}))
        self._assert_defaults()

    def test_wrong_typed_keys_degrade_individually(self) -> None:
        """One bad key must not discard the good keys beside it."""
        self.write_config(
            {
                "tightIntegrationRepos": "atomize-ai",   # str, not list
                "couplingDensityThreshold": "high",      # str, not number
                "synthesisDensityThreshold": -3,         # negative
                "planningRoles": {"a": 1},               # dict, not list
                "enabled": "true",                       # str, not bool
            }
        )
        cfg = load_config(self.workdir)
        self.assertEqual(cfg["tightIntegrationRepos"], sorted(DEFAULT_TIGHT_INTEGRATION_REPOS))
        self.assertEqual(cfg["couplingDensityThreshold"], DEFAULT_COUPLING_THRESHOLD)
        self.assertEqual(cfg["synthesisDensityThreshold"], 5)
        self.assertEqual(cfg["planningRoles"], sorted(("advisor", "plan-synthesis")))
        self.assertTrue(cfg["enabled"])

    def test_empty_repo_list_falls_back_to_seed(self) -> None:
        self.write_config({"tightIntegrationRepos": []})
        self.assertEqual(
            load_config(self.workdir)["tightIntegrationRepos"],
            sorted(DEFAULT_TIGHT_INTEGRATION_REPOS),
        )

    def test_malformed_state_json_does_not_crash(self) -> None:
        self.write_state({}, raw="{{{ broken")
        result = evaluate(self.workdir, role="advisor", repo="loose-repo")
        self.assertEqual(result["verdict"], TIER_DEFAULT_MODEL)
        self.assertEqual(result["signals"]["synthesis_density"]["value"], UNKNOWN)


# ---------------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------------

class TestEnvelope(GateTestCase):
    def test_shape_and_keys(self) -> None:
        result = evaluate(self.workdir, role="advisor", repo="atomize-ai")
        self.assertEqual(set(result), {"verdict", "role", "fired", "signals", "reason"})
        self.assertIn(result["verdict"], {TIER_DEFAULT_MODEL, GATED_MODEL})
        self.assertIsInstance(result["fired"], list)
        self.assertEqual(
            set(result["signals"]),
            {"tight_integration", "coupling_density", "synthesis_density", "risk_surface_change"},
        )
        for name, sig in result["signals"].items():
            self.assertEqual(set(sig), {"value", "source"}, name)
            self.assertIn(sig["value"], {True, False, UNKNOWN}, name)
            self.assertTrue(sig["source"], name)

    def test_fired_lists_only_true_signals(self) -> None:
        self.dense_graph()
        result = evaluate(self.workdir, role="advisor", repo="atomize-ai")
        self.assertEqual(sorted(result["fired"]), ["coupling_density", "tight_integration"])

    def test_reason_is_a_nonempty_one_liner(self) -> None:
        reason = evaluate(self.workdir, role="advisor", repo="atomize-ai")["reason"]
        self.assertTrue(reason)
        self.assertNotIn("\n", reason)


# ---------------------------------------------------------------------------
# CLI — plain vs json, exit code
# ---------------------------------------------------------------------------

class TestCli(GateTestCase):
    def _run(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_json_is_the_default_output(self) -> None:
        code, out = self._run(["--role", "advisor", "--workdir", str(self.workdir), "--repo", "atomize-ai"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["verdict"], GATED_MODEL)
        self.assertEqual(payload["role"], "advisor")

    def test_explicit_json_flag(self) -> None:
        _, out = self._run(["--role", "advisor", "--workdir", str(self.workdir),
                            "--repo", "atomize-ai", "--json"])
        self.assertEqual(json.loads(out)["verdict"], GATED_MODEL)

    def test_plain_is_one_line_and_not_json(self) -> None:
        code, out = self._run(["--role", "advisor", "--workdir", str(self.workdir),
                               "--repo", "atomize-ai", "--plain"])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(out)
        self.assertTrue(out.startswith(GATED_MODEL))
        self.assertIn("tight_integration", out)

    def test_plain_reports_fired_none(self) -> None:
        _, out = self._run(["--role", "advisor", "--workdir", str(self.workdir),
                            "--repo", "loose-repo", "--plain"])
        self.assertIn("fired=none", out)
        self.assertTrue(out.startswith(TIER_DEFAULT_MODEL))

    def test_format_plain_matches_envelope(self) -> None:
        result = evaluate(self.workdir, role="advisor", repo="atomize-ai")
        line = format_plain(result)
        self.assertIn(result["verdict"], line)
        self.assertIn(result["reason"], line)

    def test_touched_flag_is_repeatable(self) -> None:
        nodes = [f"COMP_{i}" for i in range(12)]
        edges = [("COMP_0", n) for n in nodes[1:]]
        self.write_graph(nodes, edges, file_map={"src/hub.py": "COMP_0", "src/spoke.py": "COMP_1"})
        _, out = self._run(["--role", "advisor", "--workdir", str(self.workdir),
                            "--repo", "loose-repo", "--touched", "src/hub.py",
                            "--touched", "src/spoke.py"])
        payload = json.loads(out)
        # hub(11) + spoke(1) over 2 components = 6.0, which is NOT > 6.0
        self.assertIs(payload["signals"]["coupling_density"]["value"], False)
        self.assertIn("2 touched component", payload["signals"]["coupling_density"]["source"])

    def test_exit_zero_even_on_a_broken_workdir(self) -> None:
        self.write_config(None, raw="{{{")
        self.write_state({}, raw="}}}")
        self.write_graph([], [], raw="not json")
        for role in ("advisor", "independent-auditor", "implementer"):
            with self.subTest(role=role):
                code, _ = self._run(["--role", role, "--workdir", str(self.workdir)])
                self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
