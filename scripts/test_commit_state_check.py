#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for commit_state_check.py. Zero external deps. Run: pytest scripts/test_commit_state_check.py -q"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "commit_state_check.py"


def _git(*args: str, cwd: str) -> None:
    """Run a git command; raise on failure."""
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(tmpdir: str) -> Path:
    """Init a bare repo with an initial commit so HEAD exists."""
    root = Path(tmpdir)
    _git("init", cwd=tmpdir)
    _git("config", "user.email", "test@example.com", cwd=tmpdir)
    _git("config", "user.name", "Test", cwd=tmpdir)
    # Initial commit so git status works reliably.
    (root / "README").write_text("init\n")
    _git("add", "README", cwd=tmpdir)
    _git("commit", "-m", "init", cwd=tmpdir)
    return root


def _run(*extra_args: str, workdir: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", workdir, *extra_args],
        capture_output=True,
        text=True,
    )


def _json_result(workdir: str) -> dict:
    cp = _run("--json", workdir=workdir)
    return json.loads(cp.stdout)


class TestCleanRepo(unittest.TestCase):
    def test_clean_has_uncommitted_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init_repo(tmp)
            data = _json_result(tmp)
            self.assertFalse(data["has_uncommitted_tracked"])
            self.assertEqual(data["tracked_changed"], [])
            self.assertEqual(data["summary"], "clean")

    def test_hook_mode_prints_nothing_when_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init_repo(tmp)
            cp = _run("--hook", workdir=tmp)
            self.assertEqual(cp.returncode, 0)
            self.assertEqual(cp.stdout, "")
            self.assertEqual(cp.stderr, "")


class TestModifiedTrackedFile(unittest.TestCase):
    def test_modified_file_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(tmp)
            # Modify the tracked README (unstaged).
            (root / "README").write_text("changed\n")
            data = _json_result(tmp)
            self.assertTrue(data["has_uncommitted_tracked"])
            self.assertIn("README", data["tracked_changed"])

    def test_hook_prints_reminder_for_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(tmp)
            (root / "README").write_text("changed\n")
            cp = _run("--hook", workdir=tmp)
            self.assertEqual(cp.returncode, 0)
            self.assertNotEqual(cp.stdout.strip(), "")
            self.assertIn("commit", cp.stdout.lower())


class TestUntrackedOnlyFile(unittest.TestCase):
    def test_untracked_only_is_not_uncommitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(tmp)
            # Drop a new file but never `git add` it.
            (root / "scratch.txt").write_text("scratch\n")
            data = _json_result(tmp)
            self.assertFalse(data["has_uncommitted_tracked"])
            self.assertEqual(data["tracked_changed"], [])
            self.assertGreaterEqual(data["untracked_count"], 1)

    def test_hook_silent_for_untracked_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(tmp)
            (root / "scratch.txt").write_text("scratch\n")
            cp = _run("--hook", workdir=tmp)
            self.assertEqual(cp.returncode, 0)
            self.assertEqual(cp.stdout, "")


class TestStagedFile(unittest.TestCase):
    def test_staged_tracked_file_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(tmp)
            # Create and stage a NEW tracked file.
            (root / "new_file.py").write_text("x = 1\n")
            _git("add", "new_file.py", cwd=tmp)
            data = _json_result(tmp)
            self.assertTrue(data["has_uncommitted_tracked"])
            self.assertIn("new_file.py", data["staged"])

    def test_staged_existing_file_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(tmp)
            (root / "README").write_text("updated\n")
            _git("add", "README", cwd=tmp)
            data = _json_result(tmp)
            self.assertTrue(data["has_uncommitted_tracked"])
            self.assertIn("README", data["staged"])

    def test_hook_prints_reminder_for_staged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(tmp)
            (root / "README").write_text("updated\n")
            _git("add", "README", cwd=tmp)
            cp = _run("--hook", workdir=tmp)
            self.assertEqual(cp.returncode, 0)
            self.assertNotEqual(cp.stdout.strip(), "")


class TestNonGitDir(unittest.TestCase):
    def test_non_git_dir_fail_soft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Bare temp dir — no git init.
            data = _json_result(tmp)
            self.assertFalse(data["has_uncommitted_tracked"])
            self.assertIn("git", data["summary"].lower())

    def test_non_git_dir_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cp = _run("--json", workdir=tmp)
            self.assertEqual(cp.returncode, 0)

    def test_non_git_dir_hook_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cp = _run("--hook", workdir=tmp)
            self.assertEqual(cp.returncode, 0)
            self.assertEqual(cp.stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
