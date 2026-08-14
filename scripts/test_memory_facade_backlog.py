# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from scripts.memory_facade import recall


ITEM = """---
id: TEST-UI-001
title: Choose dashboard density
status: open
priority: P1
type: decision
area: ui
bucket: decision
workstream: dashboard-b
decision_options: [compact, comfortable]
decision_impacts: [person: readability, app: information density]
created: 2026-08-10
updated: 2026-08-11
---
## Context
Choose only when dashboard B is active.
"""


class TestBacklogRecall(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "project"
        (self.repo / ".build-loop" / "backlog" / "items").mkdir(parents=True)
        self.memory = Path(self.tmp.name) / "memory"
        self.old = os.environ.get("BUILD_LOOP_MEMORY_DIR")
        os.environ["BUILD_LOOP_MEMORY_DIR"] = str(self.memory)

    def tearDown(self) -> None:
        if self.old is None:
            os.environ.pop("BUILD_LOOP_MEMORY_DIR", None)
        else:
            os.environ["BUILD_LOOP_MEMORY_DIR"] = self.old
        self.tmp.cleanup()

    def test_canonical_record_wins_over_mirror_duplicate(self) -> None:
        canonical = self.repo / ".build-loop" / "backlog" / "items" / "TEST-UI-001.md"
        canonical.write_text(ITEM, encoding="utf-8")
        mirror = self.memory / "projects" / "project" / "backlog"
        mirror.mkdir(parents=True)
        (mirror / canonical.name).write_text(ITEM.replace("readability", "stale mirror"), encoding="utf-8")
        env = recall("dashboard density", kind="backlog", project="project",
                     workdir=self.repo, limit=10)
        row = env["results_by_kind"]["backlog"][0]
        self.assertEqual(row["source"], "canonical")
        self.assertFalse(row["needs_reconcile"])
        self.assertIn("readability", str(row["decision_impacts"]))

    def test_mirror_only_record_remains_recallable_and_flagged(self) -> None:
        mirror = self.memory / "projects" / "project" / "backlog"
        mirror.mkdir(parents=True)
        (mirror / "TEST-UI-001.md").write_text(ITEM, encoding="utf-8")
        env = recall("dashboard-b", kind="work", project="project",
                     workdir=self.repo, limit=10)
        row = env["results_by_kind"]["backlog"][0]
        self.assertTrue(row["needs_reconcile"])
        self.assertTrue(any("backlog_mirror_only" in r for r in env["reasons"]))

    def test_archived_canonical_record_suppresses_stale_open_mirror(self) -> None:
        archive = self.repo / ".build-loop" / "backlog" / "archive"
        archive.mkdir()
        archived = ITEM.replace("status: open", "status: done")
        (archive / "TEST-UI-001.md").write_text(archived, encoding="utf-8")
        mirror = self.memory / "projects" / "project" / "backlog"
        mirror.mkdir(parents=True)
        (mirror / "TEST-UI-001.md").write_text(ITEM, encoding="utf-8")

        env = recall("dashboard", kind="backlog", project="project",
                     workdir=self.repo, limit=10)

        self.assertEqual(env["results_by_kind"]["backlog"], [])
        self.assertFalse(any("backlog_mirror_only" in r for r in env["reasons"]))

    def test_unrelated_query_does_not_surface_decision(self) -> None:
        canonical = self.repo / ".build-loop" / "backlog" / "items" / "TEST-UI-001.md"
        canonical.write_text(ITEM, encoding="utf-8")
        env = recall("sqlite migration", kind="backlog", project="project",
                     workdir=self.repo, limit=10)
        self.assertEqual(env["results_by_kind"]["backlog"], [])


if __name__ == "__main__":
    unittest.main()
