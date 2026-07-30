#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for project_registry (v2 loader + alias-walking resolver).

Pure in-memory + tmp fixture stores; no Postgres, no network, no real store.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import project_registry as pr  # type: ignore  # noqa: E402


def _reg(*projects, default="_unscoped"):
    """Build a normalized registry dict from partial entry dicts."""
    norm = [pr._normalize_entry(p) for p in projects]
    return {"default": default, "projects": [n for n in norm if n]}


class ResolveTests(unittest.TestCase):
    def test_alias_hit(self) -> None:
        reg = _reg(
            {"id": "rosslabs-ai-assistant", "aliases": ["ai-assistant"]},
            {"id": "build-loop"},
        )
        self.assertEqual(pr.resolve("ai-assistant", None, reg), "rosslabs-ai-assistant")

    def test_id_and_canonical_slug_hit(self) -> None:
        reg = _reg({"id": "build-loop"})
        self.assertEqual(pr.resolve("build-loop", None, reg), "build-loop")

    def test_chained_alias_walk(self) -> None:
        # Separate nodes per rename hop: a -> b -> c (each newer lists the prior).
        reg = _reg(
            {"id": "a"},
            {"id": "b", "aliases": ["a"]},
            {"id": "c", "aliases": ["b"]},
        )
        self.assertEqual(pr.resolve("a", None, reg), "c")
        self.assertEqual(pr.resolve("b", None, reg), "c")
        self.assertEqual(pr.resolve("c", None, reg), "c")

    def test_cycle_guard_returns_current_node(self) -> None:
        # a and b each claim the other as an alias → a cycle.
        reg = _reg(
            {"id": "a", "aliases": ["b"]},
            {"id": "b", "aliases": ["a"]},
        )
        # Must terminate (not hang) and return a node in the cycle.
        with self.assertLogs("build_loop.project_registry", level="WARNING"):
            result = pr.resolve("a", None, reg)
        self.assertIn(result, {"a", "b"})

    def test_depth_bound_terminates(self) -> None:
        # A long forward chain beyond MAX_ALIAS_WALK_DEPTH still terminates.
        n = pr.MAX_ALIAS_WALK_DEPTH + 5
        entries = [{"id": "n0"}]
        for i in range(1, n):
            entries.append({"id": f"n{i}", "aliases": [f"n{i-1}"]})
        reg = _reg(*entries)
        with self.assertLogs("build_loop.project_registry", level="WARNING"):
            result = pr.resolve("n0", None, reg)
        # Stops at the depth bound rather than reaching the true terminal.
        self.assertTrue(result.startswith("n"))

    def test_path_match(self) -> None:
        reg = _reg({"id": "build-loop", "paths": ["/repo/build-loop"]})
        self.assertEqual(pr.resolve("_unscoped", "/repo/build-loop/scripts", reg), "build-loop")

    def test_path_match_then_alias_walk(self) -> None:
        reg = _reg(
            {"id": "new-name", "aliases": ["old-name"]},
            {"id": "old-name", "paths": ["/repo/old"]},
        )
        # cwd path-matches the old node, which walks forward to new-name.
        self.assertEqual(pr.resolve("_unscoped", "/repo/old", reg), "new-name")

    def test_key_match_wins_over_path(self) -> None:
        reg = _reg(
            {"id": "by-key"},
            {"id": "by-path", "paths": ["/repo/x"]},
        )
        self.assertEqual(pr.resolve("by-key", "/repo/x", reg), "by-key")

    def test_unknown_returns_none(self) -> None:
        reg = _reg({"id": "build-loop", "paths": ["/repo/build-loop"]})
        self.assertIsNone(pr.resolve("nope", "/somewhere/else", reg))

    def test_empty_registry_returns_none(self) -> None:
        self.assertIsNone(pr.resolve("anything", "/x", {"default": "_unscoped", "projects": []}))

    def test_sibling_path_does_not_collide(self) -> None:
        reg = _reg({"id": "build-loop", "paths": ["/repo/build-loop"]})
        self.assertIsNone(pr.resolve("_unscoped", "/repo/build-loop-other", reg))


class V1CompatTests(unittest.TestCase):
    def test_v1_entry_normalizes(self) -> None:
        n = pr._normalize_entry({"path": "~/dev/build-loop", "project": "build-loop"})
        self.assertEqual(n["id"], "build-loop")
        self.assertEqual(n["canonical_slug"], "build-loop")
        self.assertEqual(n["paths"], ["~/dev/build-loop"])
        self.assertEqual(n["aliases"], [])
        self.assertIsNone(n["derived_from"])
        self.assertEqual(n["depends_on"], [])

    def test_v1_yaml_parses_and_resolves(self) -> None:
        text = (
            "default: _unscoped\n"
            "projects:\n"
            "  - path: /repo/build-loop\n"
            "    project: build-loop\n"
            "  - path: /repo/example\n"
            "    project: example-app\n"
        )
        reg = pr._parse_registry_yaml(text)
        self.assertEqual(len(reg["projects"]), 2)
        self.assertEqual(pr.resolve("build-loop", None, reg), "build-loop")
        self.assertEqual(pr.resolve("_unscoped", "/repo/example/sub", reg), "example-app")

    def test_missing_id_means_project_value(self) -> None:
        # Frozen-schema rule: a missing `id` means id == the `project` value.
        n = pr._normalize_entry({"project": "foo", "aliases": ["bar"]})
        self.assertEqual(n["id"], "foo")


class ParserTests(unittest.TestCase):
    def test_v2_flow_lists(self) -> None:
        text = (
            "default: _unscoped\n"
            "projects:\n"
            "  - id: rosslabs-ai-assistant\n"
            "    canonical_slug: rosslabs-ai-assistant\n"
            "    paths: [~/dev/git-folder/RossLabs-AI-Assistant]\n"
            "    aliases: [ai-assistant]\n"
            "    derived_from: null\n"
            "    depends_on: []\n"
        )
        reg = pr._parse_registry_yaml(text)
        self.assertEqual(len(reg["projects"]), 1)
        e = reg["projects"][0]
        self.assertEqual(e["id"], "rosslabs-ai-assistant")
        self.assertEqual(e["aliases"], ["ai-assistant"])
        self.assertEqual(e["paths"], ["~/dev/git-folder/RossLabs-AI-Assistant"])
        self.assertIsNone(e["derived_from"])

    def test_v2_block_lists(self) -> None:
        text = (
            "default: _unscoped\n"
            "projects:\n"
            "  - id: proj\n"
            "    paths:\n"
            "      - /a\n"
            "      - /b\n"
            "    aliases:\n"
            "      - old1\n"
            "      - old2\n"
        )
        reg = pr._parse_registry_yaml(text)
        e = reg["projects"][0]
        self.assertEqual(e["paths"], ["/a", "/b"])
        self.assertEqual(e["aliases"], ["old1", "old2"])

    def test_derived_from_and_depends_on_roundtrip(self) -> None:
        reg = _reg({
            "id": "child", "derived_from": "parent", "depends_on": ["a", "b"],
        })
        text = pr.dump_registry(reg)
        reparsed = pr._parse_registry_yaml(text)
        e = reparsed["projects"][0]
        self.assertEqual(e["derived_from"], "parent")
        self.assertEqual(e["depends_on"], ["a", "b"])

    def test_dump_is_reparseable(self) -> None:
        reg = _reg(
            {"id": "b", "aliases": ["b-old"], "paths": ["/b"]},
            {"id": "a"},
        )
        text = pr.dump_registry(reg)
        reparsed = pr._parse_registry_yaml(text)
        # Sorted by id on dump.
        self.assertEqual([p["id"] for p in reparsed["projects"]], ["a", "b"])
        self.assertEqual(pr.resolve("b-old", None, reparsed), "b")


class RegisterProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.reg_path = Path(self.tmp) / "config" / "projects.yaml"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_register_new_then_idempotent(self) -> None:
        changed = pr.register_project("brand-new", path="/repo/brand-new",
                                      registry_path=self.reg_path)
        self.assertTrue(changed)
        reg = pr.load_registry(self.reg_path)
        ids = [p["id"] for p in reg["projects"]]
        self.assertIn("brand-new", ids)
        # Re-register same id + same path → no change.
        changed2 = pr.register_project("brand-new", path="/repo/brand-new",
                                       registry_path=self.reg_path)
        self.assertFalse(changed2)

    def test_register_adds_new_path_to_existing(self) -> None:
        pr.register_project("proj", path="/repo/one", registry_path=self.reg_path)
        changed = pr.register_project("proj", path="/repo/two", registry_path=self.reg_path)
        self.assertTrue(changed)
        reg = pr.load_registry(self.reg_path)
        entry = next(p for p in reg["projects"] if p["id"] == "proj")
        norm = {pr._normalize_path(x) for x in entry["paths"]}
        self.assertIn(pr._normalize_path("/repo/one"), norm)
        self.assertIn(pr._normalize_path("/repo/two"), norm)


class LoadRegistryTests(unittest.TestCase):
    def test_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                pr.load_registry(Path(tmp) / "nope.yaml"),
                {"default": "_unscoped", "projects": []},
            )

    def test_config_preferred_over_dotconfig(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "projects.yaml").write_text(
                "default: _unscoped\nprojects:\n  - id: canonical\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {
                "BUILD_LOOP_MEMORY_STORE_ROOT": str(root),
                "BUILD_LOOP_MEMORY_ROOT": "",
                "AGENT_MEMORY_ROOT": "",
            }, clear=False):
                self.assertEqual(pr.registry_yaml_path(), root / "config" / "projects.yaml")


if __name__ == "__main__":
    unittest.main()
