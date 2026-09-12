#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for worktree_inventory.py. Zero deps. Run: python3 test_worktree_inventory.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "worktree_inventory.py"
sys.path.insert(0, str(HERE))

import worktree_inventory  # noqa: E402


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


class WorktreeInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir(parents=True)
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test")
        (self.repo / ".gitignore").write_text(
            "*.log\n__pycache__/\nnode_modules/\n.env\nlocal.sqlite\nscratch/\n"
        )
        (self.repo / "README.md").write_text("seed\n")
        git(self.repo, "add", ".gitignore", "README.md")
        git(self.repo, "commit", "-m", "seed")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _worktree(self, name: str = "wt") -> Path:
        path = Path(self.tmp.name) / name
        git(self.repo, "worktree", "add", "-b", name, str(path))
        return path

    def test_inventory_names_a_planted_ignored_file(self) -> None:
        """The defect: `git status --short` omits ignored files, so an operator
        approves a removal on evidence the inventory never showed."""
        wt = self._worktree()
        (wt / "debug.log").write_text("noise\n")

        short = subprocess.run(
            ["git", "-C", str(wt), "status", "--short"], capture_output=True, text=True, check=True
        ).stdout
        self.assertNotIn("debug.log", short, "precondition: --short hides the ignored file")

        result = worktree_inventory.inventory(wt)
        self.assertTrue(result["ok"], msg=result["error"])
        self.assertIn("debug.log", result["ignored"])
        self.assertIn("debug.log", result["ignored_by_class"]["log"])
        self.assertEqual(result["counts"]["ignored"], 1)

    def test_ignored_secret_blocks_the_caches_only_claim(self) -> None:
        wt = self._worktree()
        (wt / "__pycache__").mkdir()
        (wt / "__pycache__" / "mod.pyc").write_bytes(b"\x00")
        (wt / ".env").write_text("API_KEY=real\n")

        result = worktree_inventory.inventory(wt)
        self.assertIn(".env", result["ignored"])
        self.assertIn(".env", result["non_reproducible_ignored"])
        self.assertFalse(result["caches_only_claim_supported"])
        self.assertIn("caches-only characterization is unsupported", result["characterization"])

    def test_pure_cache_worktree_supports_the_caches_only_claim(self) -> None:
        wt = self._worktree()
        (wt / "__pycache__").mkdir()
        (wt / "__pycache__" / "mod.pyc").write_bytes(b"\x00")
        (wt / "node_modules").mkdir()
        (wt / "node_modules" / "left-pad.js").write_text("module.exports=1\n")

        result = worktree_inventory.inventory(wt)
        self.assertEqual(result["non_reproducible_ignored"], [])
        self.assertTrue(result["caches_only_claim_supported"])
        self.assertEqual(
            sorted(result["ignored"]), ["__pycache__/", "node_modules/"],
            "ignored directories collapse by default",
        )

    def test_unclassified_ignored_directory_is_named_and_blocks(self) -> None:
        wt = self._worktree()
        (wt / "scratch").mkdir()
        (wt / "scratch" / "analysis.md").write_text("hand-written findings\n")

        result = worktree_inventory.inventory(wt)
        self.assertIn("scratch/", result["ignored"])
        self.assertIn("scratch/", result["ignored_by_class"]["unclassified"])
        self.assertFalse(result["caches_only_claim_supported"])

    def test_matching_expands_ignored_directories(self) -> None:
        wt = self._worktree()
        (wt / "scratch").mkdir()
        (wt / "scratch" / "analysis.md").write_text("findings\n")

        result = worktree_inventory.inventory(wt, matching=True)
        self.assertIn("scratch/analysis.md", result["ignored"])
        self.assertTrue(result["expanded_ignored_directories"])

    def test_tracked_and_untracked_are_still_reported(self) -> None:
        wt = self._worktree()
        (wt / "README.md").write_text("edited\n")
        (wt / "new.txt").write_text("new\n")

        result = worktree_inventory.inventory(wt)
        self.assertEqual(result["counts"]["tracked_changes"], 1)
        self.assertIn("new.txt", result["untracked"])
        self.assertTrue(any("README.md" in row for row in result["tracked_changes"]))

    def test_clean_worktree_reports_nothing_to_delete(self) -> None:
        wt = self._worktree()
        result = worktree_inventory.inventory(wt)
        self.assertEqual(result["counts"], {"tracked_changes": 0, "untracked": 0, "ignored": 0})
        self.assertTrue(result["caches_only_claim_supported"])
        self.assertIn("no tracked changes", result["characterization"])

    def test_missing_path_errors_without_raising(self) -> None:
        result = worktree_inventory.inventory(Path(self.tmp.name) / "absent")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "path does not exist")

    def test_non_repo_path_errors(self) -> None:
        plain = Path(self.tmp.name) / "plain"
        plain.mkdir()
        result = worktree_inventory.inventory(plain)
        self.assertFalse(result["ok"])
        self.assertIsNotNone(result["error"])

    def test_classify_matches_nested_segments(self) -> None:
        self.assertEqual(worktree_inventory.classify("a/b/__pycache__/m.pyc"), "tool_cache")
        self.assertEqual(worktree_inventory.classify("srv/.env.production"), "potentially_valuable")
        self.assertEqual(worktree_inventory.classify("weird-thing"), "unclassified")

    def test_cli_emits_json_and_exits_zero(self) -> None:
        wt = self._worktree()
        (wt / "debug.log").write_text("noise\n")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--path", str(wt), "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("debug.log", json.loads(proc.stdout)["ignored"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
