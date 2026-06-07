#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_consolidate.intake — pending-queue contract."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "scripts"))
sys.path.insert(0, str(HERE.parent))

from memory_consolidate import intake


class SubmitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_submit_creates_pending_file(self):
        c = intake.submit(
            "raw lesson body",
            workdir=self.tmp,
            run_id="run_test",
            host="claude_code",
            hint="smells like a gotcha",
        )
        pdir = self.tmp / ".build-loop/pending-lessons/pending"
        files = list(pdir.glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].stem, c.id)
        loaded = json.loads(files[0].read_text())
        self.assertEqual(loaded["content"], "raw lesson body")
        self.assertEqual(loaded["hint"], "smells like a gotcha")
        self.assertEqual(loaded["source_host"], "claude_code")
        self.assertIn("submitted_at", loaded)

    def test_submit_sequences_ids_per_run(self):
        c1 = intake.submit("first", workdir=self.tmp, run_id="run_x", host="claude_code")
        c2 = intake.submit("second", workdir=self.tmp, run_id="run_x", host="claude_code")
        self.assertTrue(c1.id.startswith("run_x-1-"))
        self.assertTrue(c2.id.startswith("run_x-2-"))

    def test_submit_rejects_empty_content(self):
        with self.assertRaises(ValueError):
            intake.submit("", workdir=self.tmp, run_id="r", host="claude_code")
        with self.assertRaises(ValueError):
            intake.submit("   \n  ", workdir=self.tmp, run_id="r", host="claude_code")

    def test_submit_requires_run_id_and_host(self):
        with self.assertRaises(ValueError):
            intake.submit("body", workdir=self.tmp, run_id="", host="claude_code")
        with self.assertRaises(ValueError):
            intake.submit("body", workdir=self.tmp, run_id="r", host="")


class ListPendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_list_empty(self):
        self.assertEqual(intake.list_pending(self.tmp), [])

    def test_list_returns_submitted_candidates(self):
        intake.submit("a", workdir=self.tmp, run_id="r", host="claude_code", name="one")
        intake.submit("b", workdir=self.tmp, run_id="r", host="claude_code", name="two")
        items = intake.list_pending(self.tmp)
        self.assertEqual(len(items), 2)
        names = {c.content for c in items}
        self.assertEqual(names, {"a", "b"})


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_transition_to_placed(self):
        c = intake.submit("body", workdir=self.tmp, run_id="r", host="claude_code")
        intake.transition(c, intake.PLACED_DIR, placement={"path": "x"}, workdir=self.tmp)
        pending = list((self.tmp / ".build-loop/pending-lessons/pending").glob("*.json"))
        placed = list((self.tmp / ".build-loop/pending-lessons/placed").glob("*.json"))
        self.assertEqual(len(pending), 0)
        self.assertEqual(len(placed), 1)
        loaded = json.loads(placed[0].read_text())
        self.assertEqual(loaded["placement"], {"path": "x"})

    def test_transition_rejects_unknown_state(self):
        c = intake.submit("body", workdir=self.tmp, run_id="r", host="claude_code")
        with self.assertRaises(ValueError):
            intake.transition(c, "pending", workdir=self.tmp)  # cannot transition into pending


class LoadCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_load_from_pending(self):
        c = intake.submit("body", workdir=self.tmp, run_id="r", host="claude_code")
        loaded = intake.load_candidate(c.id, workdir=self.tmp)
        self.assertEqual(loaded.id, c.id)
        self.assertEqual(loaded._state, "pending")

    def test_load_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            intake.load_candidate("no-such-id", workdir=self.tmp)


if __name__ == "__main__":
    unittest.main()
