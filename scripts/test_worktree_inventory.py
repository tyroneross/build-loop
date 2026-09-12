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
        (wt / ".env").write_text("API_KEY=synthetic\n")

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

    def test_credential_inside_a_reproducible_directory_blocks_the_claim(self) -> None:
        """`git status` collapses an ignored directory to one entry, so judging
        it by NAME judges a container by its label. A `build/` holding a
        credential and hand-written work would otherwise be certified
        reproducible - the exact characterization this module refuses to make."""
        wt = self._worktree()
        (wt / ".gitignore").write_text("build/\n.env\n")
        (wt / "build").mkdir()
        (wt / "build" / "DESIGN.md").write_text("hand-written findings\n")
        (wt / "build" / ".env").write_text("API_KEY=synthetic\n")

        result = worktree_inventory.inventory(wt)
        self.assertIn("build/", result["ignored"])
        self.assertIn("build/.env", result["non_reproducible_ignored"],
                      "the credential inside the collapsed directory must be named")
        self.assertFalse(result["caches_only_claim_supported"])

    def test_directory_too_large_to_inspect_reads_as_uninspected(self) -> None:
        """A directory nobody looked inside cannot support a claim about its
        contents. Budget exhaustion must read as unknown, never as clean."""
        wt = self._worktree()
        (wt / ".gitignore").write_text("node_modules/\n")
        (wt / "node_modules").mkdir()
        for i in range(5):
            (wt / "node_modules" / f"m{i}.js").write_text("x\n")

        original = worktree_inventory._SCAN_BUDGET
        try:
            worktree_inventory._SCAN_BUDGET = 2
            result = worktree_inventory.inventory(wt)
        finally:
            worktree_inventory._SCAN_BUDGET = original

        self.assertIn("node_modules/", result["uninspected_ignored_directories"])
        self.assertFalse(result["caches_only_claim_supported"])
        self.assertIn("too large to inspect", result["characterization"])

    def test_git_status_timeout_is_reported_not_raised(self) -> None:
        """A fail-open wrapper catches raises, not hangs. A wedged git must
        surface as an error the caller can read."""
        wt = self._worktree()
        original = subprocess.run

        def _hang(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 30))

        worktree_inventory.subprocess.run = _hang
        try:
            result = worktree_inventory.inventory(wt)
        finally:
            worktree_inventory.subprocess.run = original

        self.assertFalse(result["ok"])
        self.assertIn("timed out", result["error"])

    def test_symlinked_subdirectory_reads_as_uninspected(self) -> None:
        """os.walk does not descend into a symlinked directory, so its contents
        are unseen. A credential one symlink away must not read as clean."""
        wt = self._worktree()
        (wt / ".gitignore").write_text("build/\n")
        (wt / "build").mkdir()
        (wt / "build" / "cache.txt").write_text("x\n")
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / ".env").write_text("API_KEY=synthetic\n")
        (wt / "build" / "linked").symlink_to(outside, target_is_directory=True)

        result = worktree_inventory.inventory(wt)
        self.assertIn("build/", result["uninspected_ignored_directories"])
        self.assertFalse(result["caches_only_claim_supported"])

    def test_valuable_name_wins_over_a_reproducible_pattern(self) -> None:
        """classify checks potentially_valuable first, so a valuable name is not
        laundered by a reproducible-looking suffix or an enclosing cache dir."""
        self.assertEqual(worktree_inventory.classify("build/secrets.log"), "potentially_valuable")
        self.assertEqual(worktree_inventory.classify("node_modules/.env"), "potentially_valuable")
        self.assertEqual(worktree_inventory.classify("dist/local.sqlite"), "potentially_valuable")
        self.assertEqual(worktree_inventory.classify("run.log"), "log")

    def test_budget_is_shared_across_directories(self) -> None:
        """Two directories must not each get a fresh budget."""
        wt = self._worktree()
        (wt / ".gitignore").write_text("build/\nnode_modules/\n")
        for name in ("build", "node_modules"):
            (wt / name).mkdir()
            for i in range(3):
                (wt / name / f"f{i}.txt").write_text("x\n")

        original = worktree_inventory._SCAN_BUDGET
        try:
            worktree_inventory._SCAN_BUDGET = 4
            result = worktree_inventory.inventory(wt)
        finally:
            worktree_inventory._SCAN_BUDGET = original

        self.assertTrue(result["uninspected_ignored_directories"],
                        "a 4-entry budget cannot inspect 6 files across two directories")
        self.assertFalse(result["caches_only_claim_supported"])

    def test_deeply_nested_valuable_file_is_found(self) -> None:
        wt = self._worktree()
        (wt / ".gitignore").write_text("build/\n")
        deep = wt / "build" / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "service.pem").write_text("synthetic\n")

        result = worktree_inventory.inventory(wt)
        self.assertIn("build/a/b/c/service.pem", result["non_reproducible_ignored"])
        self.assertFalse(result["caches_only_claim_supported"])

    def test_unreadable_subdirectory_blocks_the_claim(self) -> None:
        """git exits 0 while warning on stderr that it could not open a
        directory, and os.walk swallows the same error by default. A worktree
        neither tool could fully read supports no claim about its contents."""
        wt = self._worktree()
        (wt / ".gitignore").write_text("build/\n")
        (wt / "build").mkdir()
        (wt / "build" / "cache.txt").write_text("x\n")
        locked = wt / "build" / "locked"
        locked.mkdir()
        (locked / ".env").write_text("API_KEY=synthetic\n")
        locked.chmod(0o000)
        try:
            result = worktree_inventory.inventory(wt)
        finally:
            locked.chmod(0o755)

        self.assertTrue(result["status_warnings"], "git's stderr warning must not be discarded")
        self.assertFalse(result["caches_only_claim_supported"])
        self.assertIn("could not read part of this worktree", result["characterization"])

    def test_directory_git_cannot_enumerate_at_all_blocks_the_claim(self) -> None:
        """When an ignored directory holds nothing git can read, git omits the
        entry entirely — the directory vanishes rather than appearing as a risk.
        The stderr warning is the only evidence it existed."""
        wt = self._worktree()
        (wt / ".gitignore").write_text("build/\n")
        locked = wt / "build" / "locked"
        locked.mkdir(parents=True)
        (locked / ".env").write_text("API_KEY=synthetic\n")
        locked.chmod(0o000)
        try:
            result = worktree_inventory.inventory(wt)
        finally:
            locked.chmod(0o755)

        self.assertEqual(result["ignored"], [], "precondition: git omits the directory entirely")
        self.assertTrue(result["status_warnings"])
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
