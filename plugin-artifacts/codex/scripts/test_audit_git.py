#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/audit_git.py.

The convicting test (`test_checkout_dashdash_cannot_destroy_uncommitted_work`)
reproduces the observed incident: `independent-auditor` ran
`git checkout -- website/public/styles.css` mid-audit in a sibling repo and
destroyed an implementer's uncommitted work. Every test here builds a real
temporary git repo via subprocess so the allow/refuse paths exercise actual
git, not a mock.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Make scripts/ importable
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import audit_git  # noqa: E402

# Isolate from the developer machine's global/system git config (e.g. a
# `tag.gpgsign=true` in ~/.gitconfig would otherwise make even a bare
# `git tag x` demand a signing key). Applies to our own _git() helper AND
# to the git subprocess audit_git.py spawns, since both inherit os.environ.
os.environ.setdefault("GIT_CONFIG_GLOBAL", "/dev/null")
os.environ.setdefault("GIT_CONFIG_NOSYSTEM", "1")

_AUDIT_GIT_PY = str(_SCRIPTS / "audit_git.py")
_AGENT_DEF = _SCRIPTS.parent / "agents" / "independent-auditor.md"

_GIT_AVAILABLE = shutil.which("git") is not None


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _run_audit_git(repo: Path, *git_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _AUDIT_GIT_PY, "--repo", str(repo), "--", *git_args],
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(_GIT_AVAILABLE, "git binary not available")
class TestCheckoutIncidentConviction(unittest.TestCase):
    """The literal incident: `git checkout -- <file>` must be refused, and
    the front door must be the reason the uncommitted work survives."""

    def test_checkout_dashdash_cannot_destroy_uncommitted_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = _make_repo(tmp_path)

            styles = repo / "styles.css"
            styles.write_text("body { color: black; }\n")
            _git(repo, "add", "styles.css")
            _git(repo, "commit", "-m", "initial styles")

            # The implementer's uncommitted work-in-progress.
            uncommitted = "body { color: black; }\n/* WIP: dark mode */\n"
            styles.write_text(uncommitted)

            # --- The conviction: route through the front door. ---
            result = _run_audit_git(repo, "checkout", "--", "styles.css")

            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("checkout", result.stderr.lower())
            self.assertEqual(
                styles.read_text(),
                uncommitted,
                "audit_git.py must refuse `checkout --` and leave uncommitted work intact",
            )

            # --- The contrast: prove the front door is what saved it. ---
            bare = subprocess.run(
                ["git", "-C", str(repo), "checkout", "--", "styles.css"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(bare.returncode, 0)
            self.assertNotEqual(
                styles.read_text(),
                uncommitted,
                "bare `git checkout --` must actually destroy the modification "
                "(this is the contrast that proves the test is real, not vacuous)",
            )

            # Re-apply the modification; it's throwaway (tmpdir gets wiped).
            styles.write_text(uncommitted)


@unittest.skipUnless(_GIT_AVAILABLE, "git binary not available")
class TestReadonlySubcommandsPassThrough(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        (self.repo / "a.txt").write_text("first\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "first commit")
        (self.repo / "a.txt").write_text("second\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "second commit")

    def test_log(self) -> None:
        result = _run_audit_git(self.repo, "log", "--oneline", "-5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("second commit", result.stdout)

    def test_diff(self) -> None:
        result = _run_audit_git(self.repo, "diff", "HEAD~1..HEAD")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a.txt", result.stdout)

    def test_show(self) -> None:
        result = _run_audit_git(self.repo, "show", "HEAD:a.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "second\n")

    def test_status(self) -> None:
        result = _run_audit_git(self.repo, "status", "--short")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rev_parse(self) -> None:
        result = _run_audit_git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.strip()), 40)


@unittest.skipUnless(_GIT_AVAILABLE, "git binary not available")
class TestWriteFormsOfAllowlistedSubcommandsRefused(unittest.TestCase):
    """Some allowlisted subcommands (branch, tag, config, stash, remote,
    worktree, symbolic-ref) have write modes; those must still be refused."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        (self.repo / "a.txt").write_text("hello\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "initial")
        _git(self.repo, "branch", "x")
        _git(self.repo, "tag", "x")

    def test_branch_delete_refused(self) -> None:
        result = _run_audit_git(self.repo, "branch", "-D", "x")
        self.assertEqual(result.returncode, 2)
        branches = _git(self.repo, "branch", "--list", "x").stdout.strip()
        self.assertNotEqual(branches, "", "branch must survive the refused delete")

    def test_tag_delete_refused(self) -> None:
        result = _run_audit_git(self.repo, "tag", "-d", "x")
        self.assertEqual(result.returncode, 2)
        tags = _git(self.repo, "tag", "--list", "x").stdout.strip()
        self.assertNotEqual(tags, "", "tag must survive the refused delete")

    def test_config_write_refused(self) -> None:
        result = _run_audit_git(self.repo, "config", "user.name", "evil")
        self.assertEqual(result.returncode, 2)
        name = _git(self.repo, "config", "--get", "user.name").stdout.strip()
        self.assertEqual(name, "Test")

    def test_stash_push_refused(self) -> None:
        result = _run_audit_git(self.repo, "stash", "push")
        self.assertEqual(result.returncode, 2)

    def test_remote_add_refused(self) -> None:
        result = _run_audit_git(self.repo, "remote", "add", "o", "u")
        self.assertEqual(result.returncode, 2)

    def test_worktree_add_refused(self) -> None:
        result = _run_audit_git(self.repo, "worktree", "add", str(Path(self._tmp.name) / "wt"))
        self.assertEqual(result.returncode, 2)

    def test_symbolic_ref_write_refused(self) -> None:
        result = _run_audit_git(self.repo, "symbolic-ref", "HEAD", "refs/heads/x")
        self.assertEqual(result.returncode, 2)


@unittest.skipUnless(_GIT_AVAILABLE, "git binary not available")
class TestUnknownSubcommandRefusedByDefault(unittest.TestCase):
    """Allowlist, not deny-list: an unrecognized subcommand is refused, and
    so is a real mutating subcommand nobody explicitly denied."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        (self.repo / "a.txt").write_text("hello\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "initial")

    def test_made_up_subcommand_refused(self) -> None:
        result = _run_audit_git(self.repo, "frobnicate")
        self.assertEqual(result.returncode, 2)

    def test_restore_refused(self) -> None:
        # `restore` is the case that proves this is an allowlist, not a
        # deny-list that only thought of `checkout`.
        result = _run_audit_git(self.repo, "restore", "--staged", "a.txt")
        self.assertEqual(result.returncode, 2)


class TestShellMetacharacterRefused(unittest.TestCase):
    def test_shell_metacharacter_in_single_argv_element_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)  # doesn't need to be a git repo; classify() is pure
            result = subprocess.run(
                [sys.executable, _AUDIT_GIT_PY, "--repo", str(repo), "--",
                 "log; rm -rf /tmp/x"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("shell", result.stderr.lower())

    def test_classify_direct(self) -> None:
        verdict = audit_git.classify(["log; rm -rf /tmp/x"])
        self.assertFalse(verdict["allowed"])
        self.assertEqual(verdict["reason_code"], "shell_metacharacter_in_argv")


class TestAgentDefinitionNamesTheFrontDoor(unittest.TestCase):
    """Co-located regression guard: the agent body must instruct routing
    through audit_git.py. EXPECTED TO FAIL until the orchestrator wires
    agents/independent-auditor.md — do not skip or weaken this test."""

    def test_agent_definition_mentions_audit_git(self) -> None:
        self.assertTrue(_AGENT_DEF.exists(), f"{_AGENT_DEF} not found")
        body = _AGENT_DEF.read_text()
        self.assertIn(
            "audit_git.py",
            body,
            "agents/independent-auditor.md must instruct the agent to route git "
            "calls through scripts/audit_git.py instead of bare `git`",
        )


class TestClassifyIsPure(unittest.TestCase):
    """classify() never touches the filesystem or subprocess — sanity check
    that the allow path doesn't accidentally execute anything."""

    def test_allow_does_not_execute(self) -> None:
        verdict = audit_git.classify(["log", "--oneline", "-5"])
        self.assertTrue(verdict["allowed"])
        self.assertEqual(verdict["subcommand"], "log")

    def test_no_subcommand_refused(self) -> None:
        verdict = audit_git.classify([])
        self.assertFalse(verdict["allowed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
