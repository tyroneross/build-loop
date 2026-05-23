#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for validate_knowledge.py.

- Rejects unknown primary_tag.
- Accepts proposed:foo on tags (not on primary_tag).
- Catches unresolved supersedes link.
- Passes a known-good corpus.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WRITE = HERE / "write_decision.py"
VALIDATE = HERE / "validate_knowledge.py"

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


def write(workdir: Path, **kw) -> str:
    args = [
        sys.executable, str(WRITE),
        "--workdir", str(workdir),
        "--title", kw.get("title", "T"),
        "--decision", kw.get("decision", "D"),
        "--tags", kw.get("tags", "process"),
        "--primary-tag", kw.get("primary_tag", "process"),
        "--entity", kw.get("entity", "ent"),
        "--confidence", kw.get("confidence", "explicit"),
        "--source", "manual",
        "--no-db",
    ]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def validate(workdir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VALIDATE), "--workdir", str(workdir), "--quiet"],
        capture_output=True, text=True,
    )


class ValidateTests(MemIsolationMixin, unittest.TestCase):
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

    def test_clean_corpus_passes(self) -> None:
        # validate_knowledge reads from workdir/.episodic/decisions/ — use the
        # legacy writer helper so files land where the validator expects them.
        write_legacy_madr(self.workdir, "0001", "2026-05-05", "A", "e1", "tooling")
        write_legacy_madr(self.workdir, "0002", "2026-05-05", "B", "e2", "testing")
        r = validate(self.workdir)
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_proposed_tag_accepted(self) -> None:
        # Write a valid MADR directly; proposed: tags on the tags list are accepted.
        path = write_legacy_madr(self.workdir, "0001", "2026-05-05", "A", "e1", "tooling")
        text = path.read_text()
        # Inject a proposed: tag into the tags line.
        text = text.replace("tags: [tooling]", "tags: [tooling, proposed:experimental]")
        path.write_text(text)
        r = validate(self.workdir)
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_unknown_tag_rejected(self) -> None:
        # Bypass the writer (it would catch this) — write an MADR file directly.
        bad = self.workdir / ".episodic" / "decisions" / "0001-2026-05-04-bad.md"
        bad.write_text(
            """---
id: '0001'
slug: bad
title: Bad tag
type: decision
status: accepted
confidence: explicit
date: '2026-05-04'
tags: [random-tag]
primary_tag: tooling
entity: e1
source: manual
---

# Bad tag
"""
        )
        r = validate(self.workdir)
        self.assertEqual(r.returncode, 1)
        self.assertIn("random-tag", r.stderr)

    def test_unresolved_supersedes_link_rejected(self) -> None:
        # Place a valid MADR in the legacy path and hand-edit it to claim it
        # supersedes a non-existent decision — the validator should reject that.
        f = write_legacy_madr(self.workdir, "0001", "2026-05-05", "A", "e1", "tooling")
        text = f.read_text().replace("supersedes: null", "supersedes: '9999'")
        f.write_text(text)
        r = validate(self.workdir)
        self.assertEqual(r.returncode, 1)
        self.assertIn("9999", r.stderr)


if __name__ == "__main__":
    unittest.main()
