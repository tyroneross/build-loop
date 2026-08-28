#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Seeded-defect tests for check_runtime_memory_tracking grandfathering.

The guard must (a) keep blocking NEWLY-ADDED runtime paths on the staged
pre-commit path, (b) permit updates to runtime paths already tracked in HEAD
(a private consumer repo's deliberate pre-policy state — observed 2026-08-28:
the guard stranded backlog dispositions in a repo that tracks 84 such files),
and (c) stay strict under --all so the public plugin repo's CI sweep is
unchanged.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_runtime_memory_tracking.py"


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd, capture_output=True, text=True,
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


class GrandfatheringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@example.invalid")
        _git(self.repo, "config", "user.name", "t")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _commit_tracked_runtime_file(self) -> Path:
        path = self.repo / ".build-loop" / "backlog" / "item.md"
        path.parent.mkdir(parents=True)
        path.write_text("v1\n")
        _git(self.repo, "add", "-f", str(path))
        _git(self.repo, "commit", "-q", "-m", "grandfathered runtime file")
        return path

    def test_new_runtime_path_still_blocked(self) -> None:
        (self.repo / "README.md").write_text("x\n")
        _git(self.repo, "add", "README.md")
        _git(self.repo, "commit", "-q", "-m", "init")
        planted = self.repo / ".build-loop" / "new.md"
        planted.parent.mkdir(parents=True)
        planted.write_text("planted\n")
        _git(self.repo, "add", "-f", str(planted))
        self.assertEqual(_run(self.repo).returncode, 1)

    def test_update_to_head_tracked_runtime_path_passes(self) -> None:
        path = self._commit_tracked_runtime_file()
        path.write_text("v2\n")
        _git(self.repo, "add", "-f", str(path))
        self.assertEqual(_run(self.repo).returncode, 0)

    def test_all_audit_stays_strict(self) -> None:
        self._commit_tracked_runtime_file()
        self.assertEqual(_run(self.repo, "--all").returncode, 1)

    def test_unborn_branch_grandfathers_nothing(self) -> None:
        planted = self.repo / ".build-loop" / "new.md"
        planted.parent.mkdir(parents=True)
        planted.write_text("planted\n")
        _git(self.repo, "add", "-f", str(planted))
        self.assertEqual(_run(self.repo).returncode, 1)


if __name__ == "__main__":
    unittest.main()
