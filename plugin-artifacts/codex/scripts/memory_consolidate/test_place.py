#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_consolidate.place — guarded write + transition."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "scripts"))
sys.path.insert(0, str(HERE.parent))


class PlaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.memroot = Path(tempfile.mkdtemp())
        self._prev_env = os.environ.get("BUILD_LOOP_MEMORY_STORE_ROOT")
        os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = str(self.memroot)
        # Reimport so _paths picks up the env.
        import _paths
        importlib.reload(_paths)
        import memory_writer
        importlib.reload(memory_writer)
        from memory_consolidate import intake as intake_mod
        from memory_consolidate import classify as classify_mod
        from memory_consolidate import place as place_mod
        importlib.reload(intake_mod)
        importlib.reload(classify_mod)
        importlib.reload(place_mod)
        self.intake = intake_mod
        self.classify = classify_mod
        self.place = place_mod
        self._paths = _paths

    def tearDown(self):
        if self._prev_env is None:
            os.environ.pop("BUILD_LOOP_MEMORY_STORE_ROOT", None)
        else:
            os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = self._prev_env
        import _paths
        importlib.reload(_paths)

    def test_place_with_heuristic_decision_lands_in_lane(self):
        c = self.intake.submit(
            "watch out for the path footgun in lane resolution",
            workdir=self.tmp,
            run_id="run_test", host="claude_code",
            hint="gotcha footgun", project="demoproj",
        )
        packet = self.classify.prepare(c.id, workdir=self.tmp)
        decision = packet.suggested_decision
        # Heuristic should pick lessons/gotcha for project=demoproj.
        self.assertEqual(decision["lane"], "lessons")
        self.assertEqual(decision["type"], "gotcha")

        fm = self.place.place(c.id, decision, workdir=self.tmp)
        # File should exist in the resolved lane (lessons, here).
        landed = list(self.memroot.rglob("*.md"))
        self.assertEqual(len(landed), 1, f"expected 1 landed file, got {landed}")
        rel = landed[0].relative_to(self.memroot)
        self.assertEqual(str(rel.parent), "projects/demoproj/lessons")
        # Candidate must have transitioned to placed/.
        placed_dir = self.tmp / ".build-loop/pending-lessons/placed"
        self.assertEqual(len(list(placed_dir.glob("*.json"))), 1)

    def test_place_with_decision_overrides_lane_to_debugging(self):
        c = self.intake.submit(
            "crash on startup, NPE in foo",
            workdir=self.tmp,
            run_id="run_test", host="claude_code",
            hint="bug crash exception", project="demoproj",
        )
        packet = self.classify.prepare(c.id, workdir=self.tmp)
        decision = packet.suggested_decision
        # Heuristic should pick debugging lane on bug keywords.
        self.assertEqual(decision["lane"], "debugging")
        fm = self.place.place(c.id, decision, workdir=self.tmp)
        landed = list(self.memroot.rglob("*.md"))
        rel = landed[0].relative_to(self.memroot)
        # MUST land in projects/demoproj/debugging/ — NOT in lessons/.
        self.assertEqual(str(rel.parent), "projects/demoproj/debugging")
        # Backlinks footer is omitted when no similar entries exist.
        text = landed[0].read_text()
        self.assertIn("crash on startup", text)

    def test_place_writes_backlinks_when_provided(self):
        c = self.intake.submit(
            "another arbitrary lesson", workdir=self.tmp,
            run_id="r", host="claude_code", project="demoproj",
        )
        decision = {
            "scope": "project",
            "project": "demoproj",
            "lane": "lessons",
            "type": "lesson",
            "name": "another-arb",
            "filename": None,
            "backlinks": ["lessons/old-1.md", "lessons/old-2.md"],
        }
        self.place.place(c.id, decision, workdir=self.tmp)
        landed = list(self.memroot.rglob("*.md"))
        text = landed[0].read_text()
        self.assertIn("## Backlinks", text)
        self.assertIn("- lessons/old-1.md", text)
        self.assertIn("- lessons/old-2.md", text)

    def test_place_rejects_double_placement(self):
        """Placing the same candidate twice raises — it's already in placed/."""
        c = self.intake.submit("body", workdir=self.tmp, run_id="r", host="claude_code", project="demoproj")
        decision = self.classify.heuristic_decision(c, similar=[])
        self.place.place(c.id, decision, workdir=self.tmp)
        with self.assertRaises(ValueError):
            self.place.place(c.id, decision, workdir=self.tmp)

    def test_reject_transitions_to_rejected(self):
        c = self.intake.submit("body", workdir=self.tmp, run_id="r", host="claude_code")
        self.place.reject(c.id, reason="dupe", workdir=self.tmp)
        rejected_dir = self.tmp / ".build-loop/pending-lessons/rejected"
        self.assertEqual(len(list(rejected_dir.glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
