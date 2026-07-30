#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
Tests for stale_context_check.py.

Uses temporary git repos created via subprocess (no mocking) so results are
deterministic.  "now" is always the repo HEAD commit time, so the days_since
calculation doesn't depend on wall-clock.

Run: uv run pytest scripts/test_stale_context_check.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import stale_context_check as scc  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        check=True,
        capture_output=True,
    )


def _init_repo(tmpdir: Path) -> Path:
    """Init a bare git repo with a consistent author identity."""
    env_patch = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    import os
    env = {**os.environ, **env_patch}

    subprocess.run(["git", "init", str(tmpdir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(tmpdir), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmpdir), check=True, capture_output=True,
    )
    return tmpdir


def _commit(repo: Path, message: str = "commit", files: dict[str, str] | None = None) -> None:
    """Write files (or touch a dummy file) and make a commit."""
    import os
    env_patch = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    env = {**os.environ, **env_patch}

    if files:
        for fname, content in files.items():
            fpath = repo / fname
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content)
            subprocess.run(["git", "add", fname], cwd=str(repo), check=True, capture_output=True)
    else:
        # Commit a dummy file to advance HEAD.
        dummy = repo / "_dummy.txt"
        dummy.write_text(message)
        subprocess.run(["git", "add", "_dummy.txt"], cwd=str(repo), check=True, capture_output=True)

    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStaleByCommits(unittest.TestCase):
    """HANDOFF.md committed then 25 later commits → stale (commits threshold)."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="stale-ctx-test-")
        self.repo = Path(self.tmpdir)
        _init_repo(self.repo)
        # Initial commit with HANDOFF.md.
        _commit(self.repo, "add handoff", {"HANDOFF.md": "# Handoff\nContent here.\n"})
        # 25 subsequent commits to other files.
        for i in range(25):
            _commit(self.repo, f"unrelated commit {i}")

    def test_flagged_stale(self) -> None:
        result = scc.check(
            workdir=self.repo,
            globs=["HANDOFF*.md"],
            commits_threshold=20,
            days_threshold=9999,  # Disable days threshold — only commits matter here.
        )
        self.assertEqual(result["stale_count"], 1)
        doc = result["docs"][0]
        self.assertEqual(doc["path"], "HANDOFF.md")
        self.assertTrue(doc["stale"])
        self.assertGreaterEqual(doc["commits_since"], 25)
        self.assertIn("commits_since", doc["reason"])


class TestNotStaleWhenRecentlyTouched(unittest.TestCase):
    """HANDOFF.md touched in the latest commit → not stale."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="stale-ctx-test-")
        self.repo = Path(self.tmpdir)
        _init_repo(self.repo)
        # Make some unrelated commits first.
        for i in range(5):
            _commit(self.repo, f"other commit {i}")
        # HANDOFF.md touched in the final commit.
        _commit(self.repo, "update handoff", {"HANDOFF.md": "# Updated\n"})

    def test_not_stale(self) -> None:
        result = scc.check(
            workdir=self.repo,
            globs=["HANDOFF*.md"],
            commits_threshold=20,
            days_threshold=9999,
        )
        self.assertEqual(result["stale_count"], 0)
        doc = result["docs"][0]
        self.assertFalse(doc["stale"])
        self.assertEqual(doc["commits_since"], 0)


class TestCustomGlobsOverride(unittest.TestCase):
    """--globs override replaces default patterns."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="stale-ctx-test-")
        self.repo = Path(self.tmpdir)
        _init_repo(self.repo)
        _commit(self.repo, "add files", {
            "HANDOFF.md": "# Handoff\n",
            "my-notes.txt": "notes",
        })
        # Advance past threshold with unrelated commits.
        for i in range(25):
            _commit(self.repo, f"bump {i}")

    def test_custom_glob_matches_only_specified_pattern(self) -> None:
        # Use custom glob that matches only .txt files.
        result = scc.check(
            workdir=self.repo,
            globs=["*.txt"],
            commits_threshold=20,
            days_threshold=9999,
        )
        paths = [d["path"] for d in result["docs"]]
        self.assertIn("my-notes.txt", paths)
        self.assertNotIn("HANDOFF.md", paths)

    def test_default_glob_would_match_handoff(self) -> None:
        result = scc.check(
            workdir=self.repo,
            globs=scc.DEFAULT_GLOBS,
            commits_threshold=20,
            days_threshold=9999,
        )
        paths = [d["path"] for d in result["docs"]]
        self.assertIn("HANDOFF.md", paths)


class TestNoMatchingDocs(unittest.TestCase):
    """Repo with no context docs → stale_count 0, no error, exit 0."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="stale-ctx-test-")
        self.repo = Path(self.tmpdir)
        _init_repo(self.repo)
        _commit(self.repo, "just code", {"main.py": "print('hi')\n"})

    def test_empty_result(self) -> None:
        result = scc.check(
            workdir=self.repo,
            globs=scc.DEFAULT_GLOBS,
            commits_threshold=20,
            days_threshold=14,
        )
        self.assertEqual(result["stale_count"], 0)
        self.assertEqual(result["docs"], [])

    def test_cli_exits_zero(self) -> None:
        r = subprocess.run(
            [sys.executable, str(HERE / "stale_context_check.py"),
             "--workdir", str(self.repo), "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["stale_count"], 0)


class TestUntrackedDocSkipped(unittest.TestCase):
    """Untracked file matching a glob is not in output (no history)."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="stale-ctx-test-")
        self.repo = Path(self.tmpdir)
        _init_repo(self.repo)
        # One commit with a non-context file.
        _commit(self.repo, "init", {"README.md": "# Repo\n"})
        # HANDOFF.md exists on disk but is NOT staged/committed.
        (self.repo / "HANDOFF.md").write_text("# Untracked handoff\n")

    def test_untracked_not_in_output(self) -> None:
        result = scc.check(
            workdir=self.repo,
            globs=["HANDOFF*.md"],
            commits_threshold=20,
            days_threshold=14,
        )
        paths = [d["path"] for d in result["docs"]]
        self.assertNotIn("HANDOFF.md", paths)
        self.assertEqual(result["stale_count"], 0)


class TestDaysThreshold(unittest.TestCase):
    """days_since is always 0 when doc is at HEAD — never triggers days threshold."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="stale-ctx-test-")
        self.repo = Path(self.tmpdir)
        _init_repo(self.repo)
        _commit(self.repo, "add handoff", {"HANDOFF.md": "# Handoff\n"})

    def test_days_zero_when_doc_at_head(self) -> None:
        # With doc at HEAD, now == last_ct so days_since == 0.
        result = scc.check(
            workdir=self.repo,
            globs=["HANDOFF*.md"],
            commits_threshold=9999,
            days_threshold=0,  # Would trigger if days_since > 0.
        )
        doc = result["docs"][0]
        # days_since should be 0 (same commit as HEAD).
        self.assertAlmostEqual(doc["days_since"], 0.0, places=2)
        # Threshold is 0, so >= 0 fires — but that's expected behaviour.
        # The point: when doc IS HEAD, days_since is deterministically 0.
        self.assertEqual(doc["commits_since"], 0)


class TestJsonOutputShape(unittest.TestCase):
    """--json output has the required keys."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="stale-ctx-test-")
        self.repo = Path(self.tmpdir)
        _init_repo(self.repo)
        _commit(self.repo, "add handoff", {"HANDOFF.md": "# Handoff\n"})
        for i in range(25):
            _commit(self.repo, f"bump {i}")

    def test_output_keys(self) -> None:
        r = subprocess.run(
            [sys.executable, str(HERE / "stale_context_check.py"),
             "--workdir", str(self.repo),
             "--globs", "HANDOFF*.md",
             "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0)
        payload = json.loads(r.stdout)
        self.assertIn("docs", payload)
        self.assertIn("stale_count", payload)
        self.assertIn("errors", payload)
        doc = payload["docs"][0]
        for key in ("path", "last_commit", "commits_since", "days_since", "stale", "reason"):
            self.assertIn(key, doc)

    def test_stderr_contains_stale_label(self) -> None:
        r = subprocess.run(
            [sys.executable, str(HERE / "stale_context_check.py"),
             "--workdir", str(self.repo),
             "--globs", "HANDOFF*.md",
             "--json"],
            capture_output=True, text=True,
        )
        self.assertIn("[STALE CONTEXT]", r.stderr)
        self.assertIn("HANDOFF.md", r.stderr)


if __name__ == "__main__":
    unittest.main()
