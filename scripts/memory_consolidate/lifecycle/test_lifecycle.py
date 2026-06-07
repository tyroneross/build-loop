#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for lifecycle — Karpathy states + transitions.

Runnable via ``python3 scripts/memory_consolidate/lifecycle/test_lifecycle.py``.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE.parent))

from memory_consolidate.lifecycle import lifecycle as lc  # noqa: E402


def _write(path: Path, fm: dict, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    text = "\n".join(lines) + "\n" + body
    path.write_text(text, encoding="utf-8")
    return path


class ClassifyStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_active_for_clean_non_empty_entry(self):
        p = _write(self.tmp / "lesson.md",
                   {"name": "x", "type": "lesson"},
                   "This is a perfectly fine lesson body that is long enough to be active.")
        c = lc.classify_state(p)
        self.assertEqual(c.state, "active")
        self.assertEqual(c.reason, "clean")

    def test_draft_for_empty_body(self):
        p = _write(self.tmp / "draft.md",
                   {"name": "x", "type": "lesson"},
                   "tiny")
        c = lc.classify_state(p)
        self.assertEqual(c.state, "draft")

    def test_archived_for_history_path(self):
        p = _write(self.tmp / "_history" / "0001-revoked.md",
                   {"name": "x", "type": "decision", "revoked": True},
                   "body")
        c = lc.classify_state(p)
        self.assertEqual(c.state, "archived")

    def test_archived_for_revoked_status(self):
        p = _write(self.tmp / "x.md",
                   {"name": "x", "type": "decision", "revoked": True},
                   "body has plenty of content here to clear the draft threshold limit by far")
        c = lc.classify_state(p)
        self.assertEqual(c.state, "archived")

    def test_archived_for_rejected_status(self):
        p = _write(self.tmp / "y.md",
                   {"name": "y", "type": "decision", "status": "rejected"},
                   "body has plenty of content here to clear the draft threshold limit by far")
        c = lc.classify_state(p)
        self.assertEqual(c.state, "archived")

    def test_contradicted_for_superseded_status(self):
        p = _write(self.tmp / "z.md",
                   {"name": "z", "type": "decision", "status": "superseded"},
                   "body has plenty of content here to clear the draft threshold limit by far")
        c = lc.classify_state(p)
        self.assertEqual(c.state, "contradicted")

    def test_stale_for_source_hash_mismatch(self):
        # Compute a hash, write a frontmatter that claims a DIFFERENT hash.
        body = "a lesson with stable content for source-hash tracking purposes"
        wrong_hash = "0" * 64
        p = _write(self.tmp / "stale.md",
                   {"name": "stale", "type": "lesson", "source_hash": wrong_hash},
                   body)
        c = lc.classify_state(p)
        self.assertEqual(c.state, "stale")
        self.assertEqual(c.reason, "source-hash-mismatch")
        # The classification carries the *new* hash.
        self.assertEqual(c.source_hash, lc.compute_source_hash(body))

    def test_decision_rot_marks_stale_when_old(self):
        # Decision dated 200 days ago, threshold 90.
        old = (datetime.now(timezone.utc) - timedelta(days=200)).date().isoformat()
        p = _write(self.tmp / "old.md",
                   {"name": "old", "type": "decision", "date": old},
                   "decision body with content that is more than the draft threshold value here")
        c = lc.classify_state(p, threshold_days=90)
        self.assertEqual(c.state, "stale")
        self.assertTrue(c.reason.startswith("decision-rot:"))


class TransitionDoDTests(unittest.TestCase):
    """**DoD**: clean→active; stale-source→stale (both fire)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_clean_to_active_and_stale_source_to_stale(self):
        clean = _write(self.tmp / "clean.md",
                       {"name": "clean", "type": "lesson"},
                       "I'm a perfectly clean lesson body that is plenty long for active state classification.")
        stale_body = "this lesson body's text content for source hashing purposes."
        bad_hash = "deadbeef" * 8
        stale = _write(self.tmp / "stale.md",
                       {"name": "stale", "type": "lesson", "source_hash": bad_hash},
                       stale_body)

        c_clean = lc.classify_state(clean)
        c_stale = lc.classify_state(stale)
        self.assertEqual(c_clean.state, "active")
        self.assertEqual(c_clean.reason, "clean")
        self.assertEqual(c_stale.state, "stale")
        self.assertEqual(c_stale.reason, "source-hash-mismatch")


class ListLifecycleTransitionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # Synthetic memroot.
        self.memroot = Path(tempfile.mkdtemp())
        proj = self.memroot / "projects" / "p1" / "lessons"
        proj.mkdir(parents=True)
        _write(proj / "a.md",
               {"name": "a", "type": "lesson"},
               "a clean lesson with plenty of body content for the active state classification")
        _write(proj / "b.md",
               {"name": "b", "type": "lesson", "source_hash": "ff" * 32},
               "another lesson body but with a wrong recorded source_hash to trigger stale")
        top = self.memroot / "lessons"
        top.mkdir()
        _write(top / "c.md",
               {"name": "c", "type": "lesson"},
               "tiny")

    def test_walks_project_and_top_level(self):
        out = lc.list_lifecycle_transitions(workdir=".", memory_root=self.memroot)
        states = {Path(t.path).name: t.classification.state for t in out}
        self.assertEqual(states.get("a.md"), "active")
        self.assertEqual(states.get("b.md"), "stale")
        self.assertEqual(states.get("c.md"), "draft")

    def test_only_changed_filter(self):
        # Mark 'a.md' as previously-active; it should be filtered when only_changed=True.
        for p in (self.memroot / "projects" / "p1" / "lessons").glob("a.md"):
            text = p.read_text()
            text = text.replace("type: lesson", "type: lesson\nlifecycle_state: active")
            p.write_text(text)
        out = lc.list_lifecycle_transitions(workdir=".", memory_root=self.memroot, only_changed=True)
        names = {Path(t.path).name for t in out}
        self.assertNotIn("a.md", names)
        # b.md (no previous state recorded) still appears.
        self.assertIn("b.md", names)


class ApplyStateToFrontmatterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_writes_state_and_reason_into_frontmatter(self):
        p = _write(self.tmp / "x.md",
                   {"name": "x", "type": "lesson"},
                   "body is long enough to be active in normal classification.")
        fm = lc.apply_state_to_frontmatter(p, "stale", reason="manual-test")
        self.assertEqual(fm["lifecycle_state"], "stale")
        self.assertEqual(fm["lifecycle_reason"], "manual-test")
        # Disk reflects the change.
        text = p.read_text(encoding="utf-8")
        self.assertIn("lifecycle_state: stale", text)
        self.assertIn("lifecycle_reason: manual-test", text)
        # Body preserved.
        self.assertIn("body is long enough", text)

    def test_dry_run_does_not_touch_disk(self):
        p = _write(self.tmp / "x.md",
                   {"name": "x", "type": "lesson"},
                   "body is long enough to be active in normal classification.")
        before = p.read_text(encoding="utf-8")
        fm = lc.apply_state_to_frontmatter(p, "stale", reason="test", dry_run=True)
        self.assertEqual(fm["lifecycle_state"], "stale")
        self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_unknown_state_raises(self):
        p = _write(self.tmp / "x.md", {"name": "x", "type": "lesson"}, "body" * 20)
        with self.assertRaises(ValueError):
            lc.apply_state_to_frontmatter(p, "bogus", reason="x")


if __name__ == "__main__":
    unittest.main()
