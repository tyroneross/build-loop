#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Capture-side durability and relevance guards for the tier-3 decision sweep.

Each test pins a defect measured on 2026-09-16 in the live store:
  - every capture in a project shared ONE id (1,836 records keyed `2027`),
  - the same decision was queued repeatedly (`merge-pr` x9),
  - a capture was not searchable until an index rebuild happened to run.
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import scan_transcript_for_decisions as SCAN  # type: ignore  # noqa: E402


class IdSequenceTests(unittest.TestCase):
    """A date-prefixed filename must never be read as an id.

    `2026-09-02-source-pruning-policy.md` matched the old `^(\\d{4})-` scan, so
    the sequence became "year + 1" and every subsequent capture reused it.
    """

    def _dir_with(self, names: list[str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        for name in names:
            (d / name).write_text("x", encoding="utf-8")
        return d

    def test_year_prefixed_file_is_not_an_id(self):
        d = self._dir_with(["2026-09-02-source-pruning-policy.md"])
        self.assertEqual(SCAN.next_sequence([d]), 1)

    def test_real_madr_id_is_counted(self):
        d = self._dir_with(["0042-some-decision.md"])
        self.assertEqual(SCAN.next_sequence([d]), 43)

    def test_mixed_directory_sequences_from_real_ids_only(self):
        d = self._dir_with(["2026-09-02-x.md", "0007-y.md", "decision-project-z.md"])
        self.assertEqual(SCAN.next_sequence([d]), 8)

    def test_missing_directory_is_tolerated(self):
        self.assertEqual(SCAN.next_sequence([Path("/nonexistent-dir-xyz")]), 1)


class DedupeTests(unittest.TestCase):
    """The same decision must not be queued twice."""

    def _review_dir(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "decision-project-demo-merge-pr-20260901-001.md").write_text("x", encoding="utf-8")
        return d

    def test_duplicate_slug_is_skipped(self):
        self.assertTrue(SCAN.is_duplicate_capture(self._review_dir(), "demo", "merge-pr"))

    def test_distinct_slug_is_not_skipped(self):
        self.assertFalse(
            SCAN.is_duplicate_capture(self._review_dir(), "demo", "rotate-the-database-password")
        )

    def test_other_project_same_slug_is_not_a_duplicate(self):
        self.assertFalse(SCAN.is_duplicate_capture(self._review_dir(), "other", "merge-pr"))


class IndexOnWriteTests(unittest.TestCase):
    """A capture must be searchable at write time, and indexing must never break capture."""

    def test_index_failure_does_not_raise(self):
        # Capture is the valuable act; indexing is best-effort. If the index is
        # locked or missing, the record must still be written.
        original = sys.modules.get("content_index")
        sys.modules["content_index"] = None  # forces an AttributeError inside the helper
        try:
            SCAN._index_on_write(Path("/nonexistent/file.md"))
        finally:
            if original is not None:
                sys.modules["content_index"] = original
            else:
                sys.modules.pop("content_index", None)

    def test_helper_is_wired_into_the_writer(self):
        source = Path(SCAN.__file__).read_text(encoding="utf-8")
        writer = source[source.index("def write_review("):]
        self.assertIn("_index_on_write(written_path)", writer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
