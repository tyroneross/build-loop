#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/staged_content_gate.py.

The load-bearing test — ``test_worktree_green_but_index_red_is_convicted`` —
builds a real throwaway git repo and reproduces the observed failure exactly
(RossLabs-AI-Assistant commit 5066d1f): the INDEX holds a broken fix while
the WORKING TREE holds the fixed version. It proves both halves of the
regression in one place:

  1. Running the test suite against the working tree is a false green
     (this is the bug the old pre-commit hook exhibited).
  2. ``run_against_index`` — which grades the INDEX, i.e. what ``git commit``
     would actually record — is red.

If either assertion fails, the gate is not landed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Make scripts/ importable (siblings live flat in scripts/).
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import staged_content_gate as scg  # noqa: E402

_SCRIPT_PATH = _SCRIPTS / "staged_content_gate.py"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BROKEN_IMPL = """def add(a, b):
    return a - b  # BUG: should be a + b
"""

FIXED_IMPL = """def add(a, b):
    return a + b
"""

TEST_IMPL = """import unittest
from impl import add


class TestAdd(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
"""


def _git_available() -> bool:
    return shutil.which("git") is not None


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _make_repo(tmp_path: Path) -> Path:
    """Create a bare-minimum git repo with one commit on main."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    return repo


class StagedContentGateTestCase(unittest.TestCase):
    """Base fixture: real throwaway git repos, skipped cleanly without git."""

    def setUp(self) -> None:
        if not _git_available():
            raise unittest.SkipTest("git is not available on PATH")
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()


# ---------------------------------------------------------------------------
# The convicting mutant
# ---------------------------------------------------------------------------


class TestWorktreeGreenIndexRed(StagedContentGateTestCase):
    def test_worktree_green_but_index_red_is_convicted(self) -> None:
        repo = _make_repo(self.tmp_path)

        # 2. Stage the BROKEN impl + a test that fails against it.
        (repo / "impl.py").write_text(BROKEN_IMPL)
        (repo / "test_impl.py").write_text(TEST_IMPL)
        _git(repo, "add", "impl.py", "test_impl.py")

        # 3. Overwrite the WORKING-TREE impl.py with the FIXED version,
        #    leaving the INDEX holding the broken one.
        (repo / "impl.py").write_text(FIXED_IMPL)

        cmd = f"{sys.executable} test_impl.py"

        # 4. Running the test command in the working tree PASSES — the
        #    false green the old worktree-grading hook produced.
        worktree_result = subprocess.run(
            cmd, shell=True, cwd=repo, capture_output=True, text=True,
        )
        self.assertEqual(
            worktree_result.returncode, 0,
            f"expected the working tree to be green (the bug this gate fixes): "
            f"stdout={worktree_result.stdout!r} stderr={worktree_result.stderr!r}",
        )

        # 5. run_against_index grades the INDEX (what would actually be
        #    committed) — it MUST convict the broken content.
        index_result = scg.run_against_index(repo, cmd, timeout=30)
        self.assertNotEqual(
            index_result["returncode"], 0,
            f"gate failed to convict staged-broken/worktree-fixed content: "
            f"stdout={index_result['stdout']!r} stderr={index_result['stderr']!r}",
        )
        self.assertEqual(index_result["graded"], "staged_index")
        self.assertFalse(index_result["timed_out"])

        # 6. check_divergence must flag impl.py.
        divergence = scg.check_divergence(repo)
        self.assertEqual(divergence["verdict"], "diverged")
        divergent_paths = {d["path"] for d in divergence["divergent"]}
        self.assertIn("impl.py", divergent_paths)
        entry = next(d for d in divergence["divergent"] if d["path"] == "impl.py")
        self.assertEqual(entry["reason"], "index_differs_from_worktree")


# ---------------------------------------------------------------------------
# Supporting behavior
# ---------------------------------------------------------------------------


class TestAlignedRepo(StagedContentGateTestCase):
    def test_aligned_index_and_worktree_report_aligned(self) -> None:
        repo = _make_repo(self.tmp_path)
        (repo / "impl.py").write_text(FIXED_IMPL)
        (repo / "test_impl.py").write_text(TEST_IMPL)
        _git(repo, "add", "impl.py", "test_impl.py")
        # No post-add edit — index and worktree agree.

        divergence = scg.check_divergence(repo)
        self.assertEqual(divergence["verdict"], "aligned")
        self.assertEqual(divergence["divergent_count"], 0)
        self.assertEqual(divergence["staged_count"], 2)

        cmd = f"{sys.executable} test_impl.py"
        result = scg.run_against_index(repo, cmd, timeout=30)
        self.assertEqual(result["returncode"], 0, result["stdout"] + result["stderr"])


class TestStagedDeletion(StagedContentGateTestCase):
    def test_staged_file_deleted_from_worktree_is_flagged(self) -> None:
        repo = _make_repo(self.tmp_path)
        (repo / "impl.py").write_text(FIXED_IMPL)
        _git(repo, "add", "impl.py")
        os.remove(repo / "impl.py")

        divergence = scg.check_divergence(repo)
        self.assertEqual(divergence["verdict"], "diverged")
        entry = next(d for d in divergence["divergent"] if d["path"] == "impl.py")
        self.assertEqual(entry["reason"], "staged_but_deleted_in_worktree")


class TestCLIStrictExitCode(StagedContentGateTestCase):
    def test_strict_flag_toggles_exit_code_on_divergence(self) -> None:
        repo = _make_repo(self.tmp_path)
        (repo / "impl.py").write_text(FIXED_IMPL)
        _git(repo, "add", "impl.py")
        (repo / "impl.py").write_text(FIXED_IMPL + "\n# post-add edit\n")

        non_strict = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), "--repo", str(repo), "--check", "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(non_strict.returncode, 0, non_strict.stderr)
        payload = json.loads(non_strict.stdout)
        self.assertEqual(payload["verdict"], "diverged")

        strict = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), "--repo", str(repo), "--check", "--strict", "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)

    def test_aligned_repo_strict_still_exits_zero(self) -> None:
        repo = _make_repo(self.tmp_path)
        (repo / "impl.py").write_text(FIXED_IMPL)
        _git(repo, "add", "impl.py")

        strict = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), "--repo", str(repo), "--check", "--strict", "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(strict.returncode, 0, strict.stdout + strict.stderr)


class TestRunAgainstIndexTimeout(StagedContentGateTestCase):
    def test_timeout_reports_timed_out_and_exit_3(self) -> None:
        repo = _make_repo(self.tmp_path)
        (repo / "impl.py").write_text(FIXED_IMPL)
        _git(repo, "add", "impl.py")

        sleep_cmd = f'{sys.executable} -c "import time; time.sleep(5)"'
        result = scg.run_against_index(repo, sleep_cmd, timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["returncode"], 3)

    def test_cli_run_timeout_exit_code(self) -> None:
        repo = _make_repo(self.tmp_path)
        (repo / "impl.py").write_text(FIXED_IMPL)
        _git(repo, "add", "impl.py")

        sleep_cmd = f'{sys.executable} -c "import time; time.sleep(5)"'
        r = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), "--repo", str(repo),
             "--run", sleep_cmd, "--timeout", "1", "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)


class TestTmpdirCleanup(StagedContentGateTestCase):
    def test_tmpdir_removed_by_default(self) -> None:
        repo = _make_repo(self.tmp_path)
        (repo / "impl.py").write_text(FIXED_IMPL)
        _git(repo, "add", "impl.py")

        cmd = f'{sys.executable} -c "print(1)"'
        result = scg.run_against_index(repo, cmd, timeout=30)
        self.assertFalse(Path(result["tmpdir"]).exists())

    def test_keep_tmpdir_preserves_directory(self) -> None:
        repo = _make_repo(self.tmp_path)
        (repo / "impl.py").write_text(FIXED_IMPL)
        _git(repo, "add", "impl.py")

        cmd = f'{sys.executable} -c "print(1)"'
        result = scg.run_against_index(repo, cmd, timeout=30, keep_tmpdir=True)
        try:
            self.assertTrue(Path(result["tmpdir"]).exists())
        finally:
            shutil.rmtree(result["tmpdir"], ignore_errors=True)

    def test_tmpdir_removed_even_on_setup_failure(self) -> None:
        repo = _make_repo(self.tmp_path)
        not_a_repo = self.tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        cmd = f'{sys.executable} -c "print(1)"'
        result = scg.run_against_index(not_a_repo, cmd, timeout=30)
        self.assertEqual(result["returncode"], 2)
        self.assertTrue(result.get("setup_error"))
        self.assertFalse(Path(result["tmpdir"]).exists())


class TestCopyUntracked(StagedContentGateTestCase):
    def test_copy_untracked_opt_in_makes_untracked_file_visible(self) -> None:
        repo = _make_repo(self.tmp_path)
        (repo / "impl.py").write_text(FIXED_IMPL)
        _git(repo, "add", "impl.py")
        # Untracked helper file, never staged.
        (repo / "helper.txt").write_text("untracked-marker\n")

        check_cmd = "test -f helper.txt && echo present || echo absent"

        default_result = scg.run_against_index(repo, check_cmd, timeout=30)
        self.assertIn("absent", default_result["stdout"])

        copy_result = scg.run_against_index(repo, check_cmd, timeout=30, copy_untracked=True)
        self.assertIn("present", copy_result["stdout"])


if __name__ == "__main__":
    unittest.main()
