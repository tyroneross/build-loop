# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for run_loc_delta.py — net-LOC delta for run reports (observability, no gate)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "run_loc_delta.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_loc_delta  # noqa: E402


def _git(*args: str, cwd: str) -> None:
    subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, text=True)


def _init_repo(tmpdir: str) -> Path:
    root = Path(tmpdir)
    _git("init", cwd=tmpdir)
    _git("config", "user.email", "test@example.com", cwd=tmpdir)
    _git("config", "user.name", "Test", cwd=tmpdir)
    (root / "README").write_text("init\n")
    _git("add", "README", cwd=tmpdir)
    _git("commit", "-m", "init", cwd=tmpdir)
    return root


def _rev(cwd: str, ref: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "-C", cwd, "rev-parse", ref], capture_output=True, text=True, check=True
    ).stdout.strip()


class TestRangeMode(unittest.TestCase):
    def test_added_deleted_net_and_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(tmp)
            base = _rev(tmp)
            # New file (3 lines) + modify README (+1).
            (root / "new.py").write_text("a\nb\nc\n")
            (root / "README").write_text("init\nmore\n")
            _git("add", "-A", cwd=tmp)
            _git("commit", "-m", "change", cwd=tmp)
            head = _rev(tmp)

            env = run_loc_delta.compute(root, range_spec=f"{base}..{head}", working=False)
            self.assertIsNone(env["error"])
            self.assertEqual(env["added"], 4)  # 3 new + 1 readme
            self.assertEqual(env["deleted"], 0)
            self.assertEqual(env["net"], 4)
            self.assertEqual(env["files_changed"], 2)
            self.assertEqual(env["files_created"], 1)
            self.assertEqual(env["files_deleted"], 0)

    def test_deletion_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(tmp)
            (root / "gone.py").write_text("x\ny\n")
            _git("add", "-A", cwd=tmp)
            _git("commit", "-m", "add gone", cwd=tmp)
            base = _rev(tmp)
            (root / "gone.py").unlink()
            _git("add", "-A", cwd=tmp)
            _git("commit", "-m", "remove gone", cwd=tmp)
            head = _rev(tmp)

            env = run_loc_delta.compute(root, range_spec=f"{base}..{head}", working=False)
            self.assertEqual(env["deleted"], 2)
            self.assertEqual(env["net"], -2)
            self.assertEqual(env["files_deleted"], 1)


class TestWorkingMode(unittest.TestCase):
    def test_uncommitted_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(tmp)
            (root / "wip.py").write_text("one\ntwo\n")  # untracked → needs add to show in diff HEAD
            _git("add", "wip.py", cwd=tmp)
            env = run_loc_delta.compute(root, range_spec=None, working=True)
            self.assertIsNone(env["error"])
            self.assertEqual(env["added"], 2)
            self.assertEqual(env["files_created"], 1)


class TestFailOpen(unittest.TestCase):
    def test_bad_range_returns_error_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(tmp)
            env = run_loc_delta.compute(root, range_spec="nonexistent..refs", working=False)
            self.assertIsNotNone(env["error"])
            self.assertEqual(env["net"], 0)

    def test_non_repo_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = run_loc_delta.compute(Path(tmp), range_spec="a..b", working=False)
            self.assertIsNotNone(env["error"])

    def test_cli_always_exit_zero_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init_repo(tmp)
            cp = subprocess.run(
                [sys.executable, str(SCRIPT), "--workdir", tmp, "--range", "bad..range", "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(cp.returncode, 0)  # fail-open
            data = json.loads(cp.stdout)
            self.assertIsNotNone(data["error"])


class TestMarkdown(unittest.TestCase):
    def test_markdown_line_shape(self) -> None:
        env = {
            "added": 120, "deleted": 18, "net": 102,
            "files_changed": 5, "files_created": 2, "files_deleted": 0,
            "range": "abc..def", "error": None,
        }
        line = run_loc_delta.to_markdown(env)
        self.assertEqual(
            line, "+120 / -18 (net +102) across 5 files (2 created, 0 deleted) — range abc..def"
        )

    def test_markdown_negative_net(self) -> None:
        env = {
            "added": 1, "deleted": 10, "net": -9,
            "files_changed": 1, "files_created": 0, "files_deleted": 1,
            "range": "x..y", "error": None,
        }
        self.assertIn("net -9", run_loc_delta.to_markdown(env))

    def test_markdown_error_line(self) -> None:
        env = {"error": "boom", "net": 0}
        self.assertEqual(run_loc_delta.to_markdown(env), "_(loc delta unavailable: boom)_")


if __name__ == "__main__":
    unittest.main()
