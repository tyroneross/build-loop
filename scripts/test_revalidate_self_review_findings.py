#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for revalidate_self_review_findings.py."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import revalidate_self_review_findings as rv  # noqa: E402


def _finding(kind: str, target: str) -> str:
    return (f"---\nsource: self-review\nseverity: MEDIUM\n---\n"
            f"## Finding\n\n**Kind**: `{kind}`\n\n"
            f"### Evidence\n\nno test file for {target}\n")


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile, shutil
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.props = self.root / ".build-loop" / "proposals"
        self.props.mkdir(parents=True)
        (self.root / "scripts").mkdir()

    def write(self, name: str, body: str) -> Path:
        p = self.props / name
        p.write_text(body)
        return p


class TestClassification(Fixture):
    def test_resolved_when_a_test_exists(self):
        (self.root / "scripts" / "widget.py").write_text("x = 1\n")
        (self.root / "scripts" / "test_widget.py").write_text("def test_x(): pass\n")
        self.write("self-review-1.md", _finding("self_missing_test", "widget.py"))
        out = rv.revalidate(self.root)
        self.assertEqual(out["resolved"], 1, out)
        self.assertEqual(out["open"], 0)

    def test_open_when_no_test_exists(self):
        """Mutation check: if this ever reports resolved, the tool closes real work."""
        (self.root / "scripts" / "widget.py").write_text("x = 1\n")
        self.write("self-review-1.md", _finding("self_missing_test", "widget.py"))
        out = rv.revalidate(self.root)
        self.assertEqual(out["open"], 1, out)
        self.assertEqual(out["resolved"], 0)

    def test_bare_filename_resolves_below_the_root(self):
        """The finding names `widget.py`, the file lives at `scripts/widget.py`.
        Resolving from the root alone reported every target as deleted and turned a
        34% stale rate into a false 100% on the first measurement."""
        (self.root / "scripts" / "widget.py").write_text("x = 1\n")
        self.assertIsNotNone(rv.resolve_source(self.root, "widget.py"))

    def test_source_gone_when_the_file_is_absent(self):
        self.write("self-review-1.md", _finding("self_missing_test", "never_existed.py"))
        self.assertEqual(rv.revalidate(self.root)["source_gone"], 1)

    def test_judgment_kinds_are_left_alone(self):
        """Auto-closing a complexity or user-correction finding would trade a stale
        queue for a silently-emptied one."""
        for kind in ("self_complexity_high_complexity", "user_correction_cluster",
                     "self_oversized_file", "bash_ritual_candidate"):
            with self.subTest(kind=kind):
                self.setUp()
                (self.root / "scripts" / "widget.py").write_text("x = 1\n")
                (self.root / "scripts" / "test_widget.py").write_text("pass\n")
                self.write("self-review-1.md", _finding(kind, "widget.py"))
                out = rv.revalidate(self.root)
                self.assertEqual(out["not_checkable"], 1, kind)
                self.assertEqual(out["resolved"], 0, kind)

    def test_copies_and_worktrees_never_satisfy_a_finding(self):
        """A test inside plugin-artifacts/ or a worktree is a copy, not coverage."""
        (self.root / "scripts" / "widget.py").write_text("x = 1\n")
        stale = self.root / "plugin-artifacts" / "codex" / "scripts"
        stale.mkdir(parents=True)
        (stale / "test_widget.py").write_text("pass\n")
        self.write("self-review-1.md", _finding("self_missing_test", "widget.py"))
        self.assertEqual(rv.revalidate(self.root)["open"], 1)


class TestApply(Fixture):
    def test_dry_run_writes_nothing(self):
        (self.root / "scripts" / "widget.py").write_text("x = 1\n")
        (self.root / "scripts" / "test_widget.py").write_text("pass\n")
        f = self.write("self-review-1.md", _finding("self_missing_test", "widget.py"))
        before = f.read_text()
        out = rv.revalidate(self.root)
        self.assertEqual(out["applied"], 0)
        self.assertEqual(f.read_text(), before, "dry run modified a file")

    def test_apply_writes_a_checked_disposition(self):
        (self.root / "scripts" / "widget.py").write_text("x = 1\n")
        (self.root / "scripts" / "test_widget.py").write_text("pass\n")
        f = self.write("self-review-1.md", _finding("self_missing_test", "widget.py"))
        out = rv.revalidate(self.root, apply=True, today="2026-08-21")
        self.assertEqual(out["applied"], 1)
        body = f.read_text()
        self.assertIn("- [x] RESOLVED", body)
        self.assertIn("test_widget.py", body)

    def test_apply_is_idempotent(self):
        """A second pass must not re-stamp: the checked box marks it dispositioned."""
        (self.root / "scripts" / "widget.py").write_text("x = 1\n")
        (self.root / "scripts" / "test_widget.py").write_text("pass\n")
        f = self.write("self-review-1.md", _finding("self_missing_test", "widget.py"))
        rv.revalidate(self.root, apply=True, today="2026-08-21")
        first = f.read_text()
        out = rv.revalidate(self.root, apply=True, today="2026-08-21")
        self.assertEqual(out["applied"], 0)
        self.assertEqual(out["already_dispositioned"], 1)
        self.assertEqual(f.read_text(), first)

    def test_open_findings_are_never_stamped(self):
        (self.root / "scripts" / "widget.py").write_text("x = 1\n")
        f = self.write("self-review-1.md", _finding("self_missing_test", "widget.py"))
        before = f.read_text()
        rv.revalidate(self.root, apply=True, today="2026-08-21")
        self.assertEqual(f.read_text(), before, "an OPEN finding was closed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
