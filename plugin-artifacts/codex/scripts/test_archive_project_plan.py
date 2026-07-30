#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for archive_project_plan.py."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "archive_project_plan.py"


def run_archive(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


class ArchiveProjectPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workdir = self.root / "sample-repo"
        self.memory = self.root / "memory"
        self.plans = self.workdir / ".build-loop" / "plans"
        self.plans.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.workdir)], check=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_archive_plan_copies_without_removing_source_by_default(self) -> None:
        plan = self.plans / "plan.md"
        plan.write_text("# Plan\n\nDo the thing.\n", encoding="utf-8")

        result = run_archive(
            str(plan),
            "--workdir", str(self.workdir),
            "--memory-root", str(self.memory),
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        archive = Path(payload["archive"])
        self.assertTrue(plan.exists())
        self.assertTrue(archive.exists())
        self.assertEqual(archive.read_text(encoding="utf-8"), plan.read_text(encoding="utf-8"))
        self.assertIn("projects/sample-repo/archive/plans", archive.as_posix())
        self.assertFalse(payload["removed_source"])

    def test_archive_plan_can_remove_source_after_success(self) -> None:
        plan = self.plans / "cleanup.md"
        plan.write_text("# Cleanup\n", encoding="utf-8")

        result = run_archive(
            str(plan),
            "--workdir", str(self.workdir),
            "--memory-root", str(self.memory),
            "--remove-source",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(plan.exists())
        self.assertTrue(Path(payload["archive"]).exists())
        self.assertTrue(payload["removed_source"])

    def test_archive_plan_preserves_collisions(self) -> None:
        plan = self.plans / "same.md"
        plan.write_text("first\n", encoding="utf-8")
        first = run_archive(
            str(plan),
            "--workdir", str(self.workdir),
            "--memory-root", str(self.memory),
            "--json",
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        plan.write_text("second\n", encoding="utf-8")
        second = run_archive(
            str(plan),
            "--workdir", str(self.workdir),
            "--memory-root", str(self.memory),
            "--json",
        )
        self.assertEqual(second.returncode, 0, second.stderr)

        first_archive = Path(json.loads(first.stdout)["archive"])
        second_archive = Path(json.loads(second.stdout)["archive"])
        self.assertNotEqual(first_archive, second_archive)
        self.assertEqual(first_archive.read_text(encoding="utf-8"), "first\n")
        self.assertEqual(second_archive.read_text(encoding="utf-8"), "second\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
