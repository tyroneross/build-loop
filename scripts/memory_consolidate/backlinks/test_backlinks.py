#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for backlinks — propose + idempotent write_backlinks_footer.

Runnable via ``python3 scripts/memory_consolidate/backlinks/test_backlinks.py``.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE.parent))

from memory_consolidate.backlinks import backlinks as bl  # noqa: E402


class ExtractBacklinksTests(unittest.TestCase):
    def test_finds_double_bracket_links(self):
        text = "Some prose with [[link-a]] and [[link-b]] mid-flow."
        self.assertEqual(bl.extract_existing_backlinks(text), {"link-a", "link-b"})

    def test_returns_empty_when_none(self):
        self.assertEqual(bl.extract_existing_backlinks("no links here"), set())


class ProposeBacklinksTests(unittest.TestCase):
    def _entry(self, name="self", body="body content"):
        return (
            "---\n"
            f"name: {name}\n"
            "type: lesson\n"
            "---\n"
            f"{body}\n"
        )

    def test_dedup_skips_existing_and_self(self):
        text = self._entry(body="body with [[already-linked]] mention.")
        related = [
            {"file_hint": "projects/p/lessons/already-linked.md", "subject": "x"},
            {"file_hint": "projects/p/lessons/new-target.md", "subject": "y"},
            {"file_hint": "projects/p/lessons/self.md", "subject": "z"},
        ]
        out = bl.propose_backlinks(
            text, own_name="self", project="p",
            related_fn=lambda body, own, proj: related,
        )
        names = [s.target_name for s in out]
        self.assertIn("new-target", names)
        self.assertNotIn("already-linked", names)
        self.assertNotIn("self", names)

    def test_limit_honoured(self):
        text = self._entry()
        related = [
            {"file_hint": f"projects/p/lessons/t{i}.md", "subject": f"t{i}"}
            for i in range(20)
        ]
        out = bl.propose_backlinks(
            text, own_name="self", project="p", limit=3,
            related_fn=lambda b, o, p: related,
        )
        self.assertEqual(len(out), 3)

    def test_empty_related_returns_empty(self):
        text = self._entry()
        out = bl.propose_backlinks(
            text, own_name="self", project="p",
            related_fn=lambda b, o, p: [],
        )
        self.assertEqual(out, [])


class WriteBacklinksFooterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _write_entry(self, name="self", body="some body content"):
        p = self.tmp / f"{name}.md"
        p.write_text(
            "---\n"
            f"name: {name}\n"
            "type: lesson\n"
            "---\n"
            f"{body}\n",
            encoding="utf-8",
        )
        return p

    def test_writes_new_block_at_end(self):
        p = self._write_entry()
        bl.write_backlinks_footer(p, [
            bl.BacklinkSuggestion(target_name="t1"),
            bl.BacklinkSuggestion(target_name="t2"),
        ])
        text = p.read_text(encoding="utf-8")
        self.assertIn("## Related", text)
        self.assertIn("- [[t1]]", text)
        self.assertIn("- [[t2]]", text)

    def test_idempotent_double_write(self):
        p = self._write_entry()
        suggs = [bl.BacklinkSuggestion(target_name="t1"),
                 bl.BacklinkSuggestion(target_name="t2")]
        bl.write_backlinks_footer(p, suggs)
        once = p.read_text(encoding="utf-8")
        bl.write_backlinks_footer(p, suggs)
        twice = p.read_text(encoding="utf-8")
        self.assertEqual(once, twice)
        # Only ONE "## Related" heading.
        self.assertEqual(twice.count("## Related"), 1)

    def test_union_with_existing_block_dedups(self):
        p = self._write_entry()
        bl.write_backlinks_footer(p, [bl.BacklinkSuggestion(target_name="existing")])
        bl.write_backlinks_footer(p, [
            bl.BacklinkSuggestion(target_name="existing"),
            bl.BacklinkSuggestion(target_name="new-one"),
        ])
        text = p.read_text(encoding="utf-8")
        # Both present, exactly once each.
        self.assertEqual(text.count("[[existing]]"), 1)
        self.assertEqual(text.count("[[new-one]]"), 1)
        self.assertEqual(text.count("## Related"), 1)

    def test_dry_run_does_not_touch_disk(self):
        p = self._write_entry()
        before = p.read_text(encoding="utf-8")
        new_text = bl.write_backlinks_footer(
            p, [bl.BacklinkSuggestion(target_name="t1")], dry_run=True,
        )
        self.assertIn("[[t1]]", new_text)
        self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_no_suggestions_no_change(self):
        p = self._write_entry()
        before = p.read_text(encoding="utf-8")
        out_text = bl.write_backlinks_footer(p, [])
        self.assertEqual(out_text, before)

    def test_body_preserved(self):
        p = self._write_entry(body="original line one\noriginal line two")
        bl.write_backlinks_footer(p, [bl.BacklinkSuggestion(target_name="t1")])
        text = p.read_text(encoding="utf-8")
        self.assertIn("original line one", text)
        self.assertIn("original line two", text)


class DoDBacklinksTest(unittest.TestCase):
    """**DoD**: backlinks generated between related entries (end-to-end)."""

    def test_two_related_entries_link_each_other(self):
        tmp = Path(tempfile.mkdtemp())
        a = tmp / "a.md"
        b = tmp / "b.md"
        a.write_text("---\nname: a\ntype: lesson\n---\nbody A\n", encoding="utf-8")
        b.write_text("---\nname: b\ntype: lesson\n---\nbody B\n", encoding="utf-8")

        # Simulate "they are related" via injected related_fn.
        def related(body, own, project):
            if "A" in body:
                return [{"file_hint": "b.md", "subject": "body B"}]
            return [{"file_hint": "a.md", "subject": "body A"}]

        for path, own in [(a, "a"), (b, "b")]:
            suggs = bl.propose_backlinks(
                path.read_text(), own_name=own, project=None,
                related_fn=related,
            )
            bl.write_backlinks_footer(path, suggs)

        a_text = a.read_text(encoding="utf-8")
        b_text = b.read_text(encoding="utf-8")
        self.assertIn("[[b]]", a_text)
        self.assertIn("[[a]]", b_text)


if __name__ == "__main__":
    unittest.main()
