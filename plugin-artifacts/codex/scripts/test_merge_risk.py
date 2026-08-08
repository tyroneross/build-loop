#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/merge_risk.py.

Each test builds a real temporary git repo via subprocess so the
`git merge-base`, `git rev-list`, `git diff`, and `git merge-tree` calls
exercise actual git. The house pattern (SPDX header, git-fixture helpers,
unittest.main() footer) matches scripts/test_collapse_run.py.

The load-bearing test, `test_green_evidence_at_stale_base_is_high_risk`,
reproduces the observed incident: a branch's own suite is green at its own
(now-stale) merge-base while main changed the same file twice since. A
scorer that reads "tests pass" as low-risk would auto-merge an unmergeable
branch -- this test convicts that defect.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import merge_risk  # noqa: E402


# ---------------------------------------------------------------------------
# Repo factory helpers
# ---------------------------------------------------------------------------

def _git(workdir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _git_available_with_merge_tree() -> bool:
    """We need git present; merge_risk falls back gracefully on old git,
    so we only skip if git itself is entirely unavailable."""
    try:
        subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _commit_file(repo: Path, name: str, content: str, message: str) -> str:
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _head_sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@unittest.skipUnless(_git_available_with_merge_tree(), "git is not available")
class TestStaleBaseEvidence(unittest.TestCase):
    """The conviction test: green evidence at a stale base must score high."""

    def test_green_evidence_at_stale_base_is_high_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = _make_repo(tmp_path)

            # 1. shared.rs committed on main.
            _commit_file(repo, "shared.rs", "fn shared() -> i32 { 1 }\n", "init shared.rs")

            # 2. Branch feature modifies shared.rs, then reports a green
            #    suite at its own base.
            _git(repo, "checkout", "-b", "feature")
            branch_sha = _commit_file(
                repo, "shared.rs", "fn shared() -> i32 { 2 }\n", "feature: change shared.rs",
            )
            branch_dt = merge_risk._committer_date(repo, branch_sha)
            _git(repo, "checkout", "main")

            # 3. main changes the SAME file TWICE since the base.
            _commit_file(repo, "shared.rs", "fn shared() -> i32 { 3 }\n", "main: change shared.rs once")
            _commit_file(repo, "shared.rs", "fn shared() -> i32 { 4 }\n", "main: change shared.rs twice")

            result = merge_risk.score(
                repo,
                branch="feature",
                target="main",
                evidence_time=branch_dt.isoformat(),
                evidence={"test suite": "pass"},
            )

            self.assertEqual(result["verdict"], "stale_base_evidence_invalid")
            self.assertEqual(result["risk"], "high")
            self.assertIn("shared.rs", result["contested_files"])
            self.assertEqual(result["target_churn_on_contested"]["shared.rs"], 2)
            self.assertFalse(result["evidence_valid_against_target"])
            self.assertEqual(result["evidence_ignored_reason"], "produced_at_stale_base")

            # THE CONVICTION ASSERTION: a scorer that reads "tests pass" as
            # low-risk would auto-merge an unmergeable branch. Passing
            # evidence supplied alongside contested files must make the
            # verdict MORE alarming, never less.
            self.assertNotEqual(result["risk"], "low")
            self.assertNotEqual(result["risk"], "medium")

            # CLI must refuse (exit 1) too.
            self.assertEqual(result["exit_code"], 1)
            cli = subprocess.run(
                [
                    sys.executable, str(_SCRIPTS / "merge_risk.py"),
                    "--repo", str(repo),
                    "--branch", "feature",
                    "--target", "main",
                    "--evidence-time", branch_dt.isoformat(),
                    "--evidence", "test suite=pass",
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)


@unittest.skipUnless(_git_available_with_merge_tree(), "git is not available")
class TestCleanBranch(unittest.TestCase):
    def test_clean_branch_with_current_evidence_is_mergeable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = _make_repo(tmp_path)
            _commit_file(repo, "README.md", "hello\n", "init")

            _git(repo, "checkout", "-b", "feature")
            branch_sha = _commit_file(repo, "feature.rs", "fn f() {}\n", "feature work")
            branch_dt = merge_risk._committer_date(repo, branch_sha)

            # target ("main") is untouched since the base -- behind == 0.
            result = merge_risk.score(
                repo,
                branch="feature",
                target="main",
                evidence_time=branch_dt.isoformat(),
                evidence={"test suite": "pass"},
            )

            self.assertEqual(result["verdict"], "mergeable_evidence_current")
            self.assertEqual(result["risk"], "low")
            self.assertEqual(result["behind"], 0)
            self.assertEqual(result["contested_files"], [])
            self.assertTrue(result["evidence_valid_against_target"])
            self.assertEqual(result["exit_code"], 0)


@unittest.skipUnless(_git_available_with_merge_tree(), "git is not available")
class TestBehindButDisjoint(unittest.TestCase):
    def test_behind_but_disjoint_is_medium_not_high(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = _make_repo(tmp_path)
            _commit_file(repo, "shared.rs", "fn shared() {}\n", "init shared.rs")

            _git(repo, "checkout", "-b", "feature")
            _commit_file(repo, "feature.rs", "fn f() {}\n", "feature work")
            _git(repo, "checkout", "main")

            # main changes a DIFFERENT file -- disjoint from branch_files.
            _commit_file(repo, "other.rs", "fn other() {}\n", "main: unrelated change")

            result = merge_risk.score(repo, branch="feature", target="main")

            self.assertEqual(result["verdict"], "behind_but_disjoint")
            self.assertEqual(result["risk"], "medium")
            self.assertEqual(result["contested_files"], [])
            self.assertGreater(result["behind"], 0)
            self.assertEqual(result["exit_code"], 0)

            strict_result = merge_risk.score(repo, branch="feature", target="main", strict=True)
            self.assertEqual(strict_result["exit_code"], 1)

            cli = subprocess.run(
                [
                    sys.executable, str(_SCRIPTS / "merge_risk.py"),
                    "--repo", str(repo),
                    "--branch", "feature",
                    "--target", "main",
                    "--strict",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)


@unittest.skipUnless(_git_available_with_merge_tree(), "git is not available")
class TestConflictProbeUnavailable(unittest.TestCase):
    def test_conflict_probe_unavailable_is_null_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = _make_repo(tmp_path)
            _commit_file(repo, "shared.rs", "fn shared() {}\n", "init shared.rs")

            _git(repo, "checkout", "-b", "feature")
            _commit_file(repo, "feature.rs", "fn f() {}\n", "feature work")
            _git(repo, "checkout", "main")
            _commit_file(repo, "other.rs", "fn other() {}\n", "main: unrelated change")

            original_probe = merge_risk.probe_conflicts
            merge_risk.probe_conflicts = lambda *a, **kw: (None, "unavailable")
            try:
                result = merge_risk.score(repo, branch="feature", target="main")
            finally:
                merge_risk.probe_conflicts = original_probe

            self.assertIsNone(result["predicted_conflicts"])
            self.assertEqual(result["conflict_probe"], "unavailable")
            # The real signal here (behind > 0, disjoint) must still drive
            # the verdict -- a silent probe must not manufacture a clean bill.
            self.assertNotEqual(result["verdict"], "mergeable_evidence_current")
            self.assertEqual(result["verdict"], "behind_but_disjoint")


@unittest.skipUnless(_git_available_with_merge_tree(), "git is not available")
class TestAllBranchesSweep(unittest.TestCase):
    def test_all_branches_sweep_sorts_risk_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = _make_repo(tmp_path)
            _commit_file(repo, "shared.rs", "fn shared() {}\n", "init shared.rs")

            # Fork medium-risk and high-risk from this early point, BEFORE
            # main advances, so both end up genuinely behind.
            _git(repo, "checkout", "-b", "medium-risk")
            _commit_file(repo, "medium.rs", "fn medium() {}\n", "medium risk work")
            _git(repo, "checkout", "main")

            _git(repo, "checkout", "-b", "high-risk")
            _commit_file(repo, "shared.rs", "fn shared() -> i32 { 9 }\n", "high risk: touch shared.rs")
            _git(repo, "checkout", "main")

            # main advances twice: once disjoint from medium-risk's files,
            # once contesting shared.rs (which high-risk also touched).
            _commit_file(repo, "other.rs", "fn other() {}\n", "main: unrelated change")
            _commit_file(repo, "shared.rs", "fn shared() -> i32 { 10 }\n", "main: also touch shared.rs")

            # low-risk branch forks from the CURRENT main tip, so it is not
            # behind at all -- ahead-only, evidence current.
            _git(repo, "checkout", "-b", "low-risk")
            _commit_file(repo, "low.rs", "fn low() {}\n", "low risk work")
            _git(repo, "checkout", "main")

            results = merge_risk.sweep(repo, target="main")
            branches_in_order = [r["branch"] for r in results]

            self.assertIn("high-risk", branches_in_order)
            self.assertIn("medium-risk", branches_in_order)
            self.assertIn("low-risk", branches_in_order)

            risk_by_branch = {r["branch"]: r["risk"] for r in results}
            self.assertEqual(risk_by_branch["high-risk"], "high")
            self.assertEqual(risk_by_branch["low-risk"], "low")

            risk_rank = {"high": 0, "medium": 1, "low": 2}
            ranks = [risk_rank[r["risk"]] for r in results]
            self.assertEqual(ranks, sorted(ranks), "sweep must be sorted risk-first")

            idx = {b: i for i, b in enumerate(branches_in_order)}
            self.assertLess(idx["high-risk"], idx["low-risk"])


class TestConflictCounting(unittest.TestCase):
    """Unit-level checks on the write-tree output parser, no git required."""

    def test_count_write_tree_conflicts_clean(self) -> None:
        stdout = "abc123treeoid\n"
        self.assertEqual(merge_risk._count_write_tree_conflicts(stdout), 0)

    def test_count_write_tree_conflicts_two_files(self) -> None:
        stdout = (
            "abc123treeoid\n"
            "100644 aaa 1\tf1.txt\n"
            "100644 bbb 2\tf1.txt\n"
            "100644 ccc 3\tf1.txt\n"
            "100644 ddd 1\tf2.txt\n"
            "100644 eee 2\tf2.txt\n"
            "100644 fff 3\tf2.txt\n"
            "\n"
            "Auto-merging f1.txt\n"
            "CONFLICT (content): Merge conflict in f1.txt\n"
            "Auto-merging f2.txt\n"
            "CONFLICT (content): Merge conflict in f2.txt\n"
        )
        self.assertEqual(merge_risk._count_write_tree_conflicts(stdout), 2)


if __name__ == "__main__":
    unittest.main()
