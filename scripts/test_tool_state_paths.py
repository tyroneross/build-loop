#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/tool_state_paths.py. Real temp git repos via subprocess,
no mocks of git.

Run: ``python3 -m pytest scripts/test_tool_state_paths.py -q``
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "tool_state_paths.py"
HOOK_SH = HERE.parent / "hooks" / "session-start-tool-state.sh"


def _run_git(workdir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(workdir), *args],
                           capture_output=True, text=True, check=True)


def _init_repo(workdir: Path) -> None:
    subprocess.run(["git", "init", str(workdir)], check=True, capture_output=True)
    _run_git(workdir, "config", "user.email", "test@example.com")
    _run_git(workdir, "config", "user.name", "Test")
    (workdir / "README.md").write_text("init\n", encoding="utf-8")
    _run_git(workdir, "add", "README.md")
    _run_git(workdir, "commit", "-m", "init", "--allow-empty")


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                           capture_output=True, text=True, check=False)


def _porcelain(workdir: Path) -> str:
    return _run_git(workdir, "status", "--porcelain").stdout


def _exclude_text(workdir: Path) -> str:
    cp = subprocess.run(["git", "-C", str(workdir), "rev-parse", "--git-path", "info/exclude"],
                         capture_output=True, text=True, check=True)
    p = Path(cp.stdout.strip())
    if not p.is_absolute():
        p = workdir / p
    return p.read_text(encoding="utf-8") if p.is_file() else ""


class FreshRepoCreatedThisSessionTests(unittest.TestCase):
    """(a) dirs created after snapshot are attributed to this session, never
    called pre-existing, excluded from git status, and added to info/exclude."""

    def setUp(self):
        self._td = TemporaryDirectory()
        self.workdir = Path(self._td.name) / "repo"
        _init_repo(self.workdir)

    def tearDown(self):
        self._td.cleanup()

    def test_created_this_session_attribution(self):
        cp = _run_cli("snapshot", "--workdir", str(self.workdir), "--session-id", "s1")
        self.assertEqual(cp.returncode, 0, cp.stderr)

        rally = self.workdir / ".rally"
        rally.mkdir()
        (rally / ".gitignore").write_text("*\n", encoding="utf-8")
        bl = self.workdir / ".build-loop"
        bl.mkdir()
        (bl / "state.json").write_text("{}\n", encoding="utf-8")

        rep = _run_cli("report", "--workdir", str(self.workdir), "--session-id", "s1", "--json")
        self.assertEqual(rep.returncode, 0, rep.stderr)
        payload = json.loads(rep.stdout)
        attributions = {e["path"]: e["attribution"] for e in payload["paths"]}
        self.assertIn(".rally/", attributions)
        self.assertIn(".build-loop/", attributions)
        for attribution in attributions.values():
            self.assertIn("created by build-loop's hooks", attribution)
            self.assertNotIn("pre-existing", attribution)

        status = _porcelain(self.workdir)
        self.assertNotIn(".rally", status)
        self.assertNotIn(".build-loop", status)

        exclude = _exclude_text(self.workdir)
        self.assertIn("/.build-loop/", exclude)
        self.assertIn("/.rally/", exclude)


class PreExistingDirTests(unittest.TestCase):
    """(b) a dir older than the grace window is classified pre-existing and
    left out of info/exclude."""

    def setUp(self):
        self._td = TemporaryDirectory()
        self.workdir = Path(self._td.name) / "repo"
        _init_repo(self.workdir)

    def tearDown(self):
        self._td.cleanup()

    def test_pre_existing_not_excluded(self):
        rally = self.workdir / ".rally"
        rally.mkdir()
        (rally / "note.txt").write_text("old\n", encoding="utf-8")

        # macOS birthtime can't be backdated in-place; drive the classification
        # via --now = real time + 1 hour instead (dir's true birthtime stays
        # "now", well outside the grace window relative to the future --now).
        future = time.time() + 3600
        cp = _run_cli("snapshot", "--workdir", str(self.workdir), "--session-id", "s2",
                      "--now", str(future))
        self.assertEqual(cp.returncode, 0, cp.stderr)

        rep = _run_cli("report", "--workdir", str(self.workdir), "--session-id", "s2")
        self.assertEqual(rep.returncode, 0, rep.stderr)
        self.assertIn("pre-existing", rep.stdout)

        exclude = _exclude_text(self.workdir)
        self.assertNotIn("/.rally/", exclude)


class TrackedDirNeverExcludedTests(unittest.TestCase):
    """(c) a dir with tracked files is never added to info/exclude, even if
    classified absent/new."""

    def setUp(self):
        self._td = TemporaryDirectory()
        self.workdir = Path(self._td.name) / "repo"
        _init_repo(self.workdir)
        bl = self.workdir / ".build-loop"
        bl.mkdir()
        (bl / "committed.md").write_text("tracked\n", encoding="utf-8")
        _run_git(self.workdir, "add", ".build-loop/committed.md")
        _run_git(self.workdir, "commit", "-m", "track build-loop file")

    def tearDown(self):
        self._td.cleanup()

    def test_tracked_dir_not_excluded(self):
        cp = _run_cli("snapshot", "--workdir", str(self.workdir), "--session-id", "s3")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        exclude = _exclude_text(self.workdir)
        self.assertNotIn(".build-loop", exclude)


class IdempotentExcludeTests(unittest.TestCase):
    """(d) running snapshot twice does not duplicate exclude lines."""

    def setUp(self):
        self._td = TemporaryDirectory()
        self.workdir = Path(self._td.name) / "repo"
        _init_repo(self.workdir)

    def tearDown(self):
        self._td.cleanup()

    def test_no_duplicate_lines(self):
        for _ in range(2):
            (self.workdir / ".rally").mkdir(exist_ok=True)
            cp = _run_cli("snapshot", "--workdir", str(self.workdir), "--session-id", "s4")
            self.assertEqual(cp.returncode, 0, cp.stderr)
        exclude = _exclude_text(self.workdir)
        self.assertEqual(exclude.count("/.rally/"), 1)


class EmitContextTests(unittest.TestCase):
    """(e) --emit-context prints valid SessionStart JSON when dirs are
    absent/new, and nothing when everything is pre-existing."""

    def setUp(self):
        self._td = TemporaryDirectory()
        self.workdir = Path(self._td.name) / "repo"
        _init_repo(self.workdir)

    def tearDown(self):
        self._td.cleanup()

    def test_emits_context_when_absent(self):
        cp = _run_cli("snapshot", "--workdir", str(self.workdir), "--session-id", "s5",
                      "--emit-context")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertTrue(cp.stdout.strip(), "expected emitted context JSON")
        payload = json.loads(cp.stdout.strip().splitlines()[-1])
        self.assertEqual(
            payload["hookSpecificOutput"]["hookEventName"], "SessionStart"
        )
        self.assertIn("additionalContext", payload["hookSpecificOutput"])

    def test_no_context_when_all_pre_existing(self):
        (self.workdir / ".rally").mkdir()
        (self.workdir / ".build-loop").mkdir()
        future = time.time() + 3600
        cp = _run_cli("snapshot", "--workdir", str(self.workdir), "--session-id", "s6",
                      "--now", str(future), "--emit-context")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(cp.stdout.strip(), "")


class GitHookAttributionTests(unittest.TestCase):
    """(f) a git hook installed after snapshot is reported as installed by
    build-loop's hooks this session."""

    def setUp(self):
        self._td = TemporaryDirectory()
        self.workdir = Path(self._td.name) / "repo"
        _init_repo(self.workdir)

    def tearDown(self):
        self._td.cleanup()

    def test_hook_installed_after_snapshot(self):
        cp = _run_cli("snapshot", "--workdir", str(self.workdir), "--session-id", "s7")
        self.assertEqual(cp.returncode, 0, cp.stderr)

        hooks_dir = self.workdir / ".git" / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        pre_commit = hooks_dir / "pre-commit"
        pre_commit.write_text("#!/bin/sh\n# build-loop pre-commit\nexit 0\n", encoding="utf-8")
        pre_commit.chmod(0o755)

        rep = _run_cli("report", "--workdir", str(self.workdir), "--session-id", "s7", "--json")
        self.assertEqual(rep.returncode, 0, rep.stderr)
        payload = json.loads(rep.stdout)
        hook_entries = [e for e in payload["paths"] if e["kind"] == "git-hook"]
        self.assertTrue(any(e["path"] == "pre-commit" for e in hook_entries))
        for e in hook_entries:
            if e["path"] == "pre-commit":
                self.assertIn("installed by build-loop's hooks", e["attribution"])

    def test_hook_without_marker_not_listed(self):
        """A hook installed after snapshot that never names build-loop/rally
        (e.g. a plain lint-staged hook a human or another tool wrote) must
        NOT be credited to build-loop — timing alone is not evidence."""
        cp = _run_cli("snapshot", "--workdir", str(self.workdir), "--session-id", "s7b")
        self.assertEqual(cp.returncode, 0, cp.stderr)

        hooks_dir = self.workdir / ".git" / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        pre_commit = hooks_dir / "pre-commit"
        pre_commit.write_text("#!/bin/sh\nnpx lint-staged\n", encoding="utf-8")
        pre_commit.chmod(0o755)

        rep = _run_cli("report", "--workdir", str(self.workdir), "--session-id", "s7b", "--json")
        self.assertEqual(rep.returncode, 0, rep.stderr)
        payload = json.loads(rep.stdout)
        hook_entries = [e for e in payload["paths"] if e["kind"] == "git-hook"]
        self.assertFalse(any(e["path"] == "pre-commit" for e in hook_entries))


class NonGitDirTests(unittest.TestCase):
    """(g) non-git dir → exit 0, no output."""

    def test_non_git_dir_noop(self):
        with TemporaryDirectory() as td:
            cp = _run_cli("snapshot", "--workdir", td, "--emit-context")
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertEqual(cp.stdout.strip(), "")


class HookShellEndToEndTests(unittest.TestCase):
    """(h) hooks/session-start-tool-state.sh end-to-end in a fresh repo."""

    def setUp(self):
        self._td = TemporaryDirectory()
        self.workdir = Path(self._td.name) / "repo"
        _init_repo(self.workdir)

    def tearDown(self):
        self._td.cleanup()

    def test_hook_end_to_end(self):
        import os
        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(self.workdir)
        cp = subprocess.run(
            ["bash", str(HOOK_SH)],
            input='{"session_id":"abc"}',
            capture_output=True, text=True, env=env, timeout=15,
        )
        self.assertEqual(cp.returncode, 0, cp.stderr)
        state_path_cp = subprocess.run(
            ["git", "-C", str(self.workdir), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=True,
        )
        common = Path(state_path_cp.stdout.strip())
        if not common.is_absolute():
            common = self.workdir / common
        state_file = common.resolve() / "build-loop" / "tool-state" / "abc.json"
        self.assertTrue(state_file.is_file(), f"expected {state_file} to exist")

    def test_hook_non_repo_exits_zero(self):
        with TemporaryDirectory() as td:
            cp = subprocess.run(
                ["bash", str(HOOK_SH)],
                input="", capture_output=True, text=True,
                env={"CLAUDE_PROJECT_DIR": td, "PATH": "/usr/bin:/bin:/usr/local/bin"},
                timeout=15,
            )
            self.assertEqual(cp.returncode, 0, cp.stderr)


if __name__ == "__main__":
    unittest.main()
