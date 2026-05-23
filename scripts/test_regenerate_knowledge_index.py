#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for regenerate_knowledge_index.py.

- Decisions INDEX renders one row per decision in id order.
- Default confidence floor (`confirmed`) hides `inferred`/`assumed`.
- Issues INDEX renders rows for any *.md in .episodic/issues/ except INDEX.md.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WRITE = HERE / "write_decision.py"
INDEX = HERE / "regenerate_knowledge_index.py"

from _test_helpers import MemIsolationMixin, write_legacy_madr  # noqa: E402

TAXONOMY = """---
type: taxonomy
---

## 1. Decision tags

- `architecture`
- `process`
- `tooling`
- `testing`

## 6. Source attribution

- `manual`
- `migration`
"""


def run_write(workdir: Path, **kw) -> str:
    args = [
        sys.executable, str(WRITE),
        "--workdir", str(workdir),
        "--title", kw.get("title", "T"),
        "--decision", kw.get("decision", "D"),
        "--tags", kw.get("tags", "process"),
        "--primary-tag", kw.get("primary_tag", "process"),
        "--entity", kw.get("entity", "build-loop"),
        "--confidence", kw.get("confidence", "explicit"),
        "--source", "manual",
        "--no-db",
    ]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


class IndexTests(MemIsolationMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        (self.workdir / ".semantic").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions" / "_history").mkdir(parents=True)
        (self.workdir / ".episodic" / "issues").mkdir(parents=True)
        (self.workdir / ".semantic" / "TAXONOMY.md").write_text(TAXONOMY)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        super().tearDown()

    def test_index_shape_with_three_decisions(self) -> None:
        # regenerate_knowledge_index reads from workdir/.episodic/decisions/ (legacy path).
        # Write files there directly to avoid Phase-C routing via write_decision.py.
        for i, conf in enumerate(["explicit", "confirmed", "inferred"], start=1):
            write_legacy_madr(
                self.workdir,
                f"000{i}",
                "2026-05-05",
                f"Decision {i}",
                f"e{i}",
                "process",
                confidence=conf,
            )
        # Run the index regenerator.
        r = subprocess.run(
            [sys.executable, str(INDEX), "--workdir", str(self.workdir)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        index_text = (self.workdir / ".episodic" / "decisions" / "INDEX.md").read_text()
        # Default floor = confirmed → inferred row hidden
        self.assertIn("Decision 1", index_text)
        self.assertIn("Decision 2", index_text)
        self.assertNotIn("Decision 3", index_text)

    def test_lower_confidence_floor_includes_inferred(self) -> None:
        write_legacy_madr(
            self.workdir, "0001", "2026-05-05", "Low", "e-low", "process", confidence="inferred"
        )
        r = subprocess.run(
            [
                sys.executable,
                str(INDEX),
                "--workdir", str(self.workdir),
                "--confidence-floor", "inferred",
            ],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        index_text = (self.workdir / ".episodic" / "decisions" / "INDEX.md").read_text()
        self.assertIn("Low", index_text)

    def test_issues_index_lists_issue_files(self) -> None:
        issue = self.workdir / ".episodic" / "issues" / "2026-05-04-test-issue.md"
        issue.write_text(
            """---
type: issue
title: Test issue
status: open
date: 2026-05-04
tags: [process]
---

Body of the issue.
"""
        )
        r = subprocess.run(
            [sys.executable, str(INDEX), "--workdir", str(self.workdir)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        index_text = (self.workdir / ".episodic" / "issues" / "INDEX.md").read_text()
        self.assertIn("Test issue", index_text)
        self.assertIn("open", index_text)


if __name__ == "__main__":
    unittest.main()
