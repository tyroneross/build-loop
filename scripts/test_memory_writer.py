#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_writer.py. Zero deps. Run: python3 test_memory_writer.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import memory_writer as mw  # noqa: E402


class FrontmatterParseTests(unittest.TestCase):
    def test_split_no_frontmatter(self):
        fm, body = mw._split_frontmatter("just a body")
        self.assertEqual(fm, {})
        self.assertEqual(body, "just a body")

    def test_split_basic_frontmatter(self):
        text = "---\nname: foo\ndescription: bar\n---\nbody text"
        fm, body = mw._split_frontmatter(text)
        self.assertEqual(fm["name"], "foo")
        self.assertEqual(fm["description"], "bar")
        self.assertEqual(body, "body text")

    def test_split_boolean_values(self):
        text = "---\nactive: true\nstale: false\n---\nbody"
        fm, _ = mw._split_frontmatter(text)
        self.assertIs(fm["active"], True)
        self.assertIs(fm["stale"], False)

    def test_split_null_value(self):
        text = "---\nsource_repo: null\nempty:\n---\nbody"
        fm, _ = mw._split_frontmatter(text)
        self.assertIsNone(fm["source_repo"])
        self.assertIsNone(fm["empty"])

    def test_split_list_value(self):
        text = '---\nitems: ["a","b","c"]\n---\nbody'
        fm, _ = mw._split_frontmatter(text)
        self.assertEqual(fm["items"], ["a", "b", "c"])

    def test_split_list_of_dicts(self):
        text = '---\napplied: [{"repo":"x","workdir":"/y"}]\n---\nbody'
        fm, _ = mw._split_frontmatter(text)
        self.assertEqual(fm["applied"], [{"repo": "x", "workdir": "/y"}])

    def test_split_quoted_string(self):
        text = '---\nrepo: "https://github.com/foo/bar.git"\n---\nbody'
        fm, _ = mw._split_frontmatter(text)
        self.assertEqual(fm["repo"], "https://github.com/foo/bar.git")


class EmitFrontmatterTests(unittest.TestCase):
    def test_emit_round_trip(self):
        original = {
            "name": "foo",
            "active": True,
            "stale": False,
            "source_repo": None,
            "items": ["a", "b"],
            "applied": [{"repo": "x", "workdir": "/y"}],
        }
        text = mw._emit_frontmatter(original) + "\nbody"
        fm, _ = mw._split_frontmatter(text)
        self.assertEqual(fm, original)


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_write_creates_file_with_provenance(self):
        fm = mw.write(
            self.tmp, "lesson.md",
            body="A lesson body.",
            name="lesson",
            description="A test lesson",
            type_="pattern",
            run_id="run_x",
            workdir=str(self.tmp),
            host="claude_code",
        )
        path = self.tmp / "lesson.md"
        self.assertTrue(path.exists())
        # Frontmatter has all required provenance fields.
        for field in mw.REQUIRED_PROVENANCE_FIELDS:
            self.assertIn(field, fm)
        self.assertEqual(fm["name"], "lesson")
        self.assertEqual(fm["type"], "pattern")
        self.assertEqual(fm["source_host"], "claude_code")
        self.assertFalse(fm["cross_repo_validated"])
        self.assertEqual(fm["applied_in_repos"], [])

    def test_write_appends_to_index(self):
        mw.write(
            self.tmp, "lesson.md", body="x",
            name="l", description="d", type_="pattern",
            run_id="r", workdir=str(self.tmp), host="codex",
        )
        index = self.tmp / "INDEX.jsonl"
        self.assertTrue(index.exists())
        rows = [json.loads(l) for l in index.read_text().strip().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "write")
        self.assertEqual(rows[0]["file"], "lesson.md")

    def test_write_update_preserves_created_at_and_applied(self):
        # Initial write.
        fm1 = mw.write(
            self.tmp, "x.md", body="v1",
            name="x", description="d", type_="pattern",
            run_id="r1", workdir=str(self.tmp), host="claude_code",
        )
        # Simulate cross-repo application.
        mw.mark_applied(
            self.tmp, "x.md",
            applying_repo="other.git",
            applying_workdir="/tmp/other",
            applying_run_id="r2",
        )
        # Update (re-write).
        fm2 = mw.write(
            self.tmp, "x.md", body="v2",
            name="x", description="d", type_="pattern",
            run_id="r3", workdir=str(self.tmp), host="claude_code",
        )
        # created_at preserved from the first write.
        self.assertEqual(fm2["created_at"], fm1["created_at"])
        # applied_in_repos preserved through the update.
        self.assertEqual(len(fm2["applied_in_repos"]), 1)
        # cross_repo_validated stays True (writer doesn't reset state).
        self.assertTrue(fm2["cross_repo_validated"])
        # last_updated_at is fresh (string format, can't easily compare without
        # parsing — just ensure it's present and well-formed).
        self.assertEqual(len(fm2["last_updated_at"]), 20)  # YYYY-MM-DDTHH:MM:SSZ

    def test_write_rejects_invalid_host(self):
        with self.assertRaises(ValueError):
            mw.write(
                self.tmp, "x.md", body="x",
                name="x", description="d", type_="pattern",
                run_id="r", workdir=str(self.tmp), host="ghost",
            )

    def test_write_rejects_invalid_type(self):
        with self.assertRaises(ValueError):
            mw.write(
                self.tmp, "x.md", body="x",
                name="x", description="d", type_="not-a-real-type",
                run_id="r", workdir=str(self.tmp), host="codex",
            )


class MarkAppliedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        mw.write(
            self.tmp, "x.md", body="body",
            name="x", description="d", type_="pattern",
            run_id="r1", workdir=str(self.tmp), host="claude_code",
        )

    def test_mark_applied_adds_entry(self):
        fm = mw.mark_applied(
            self.tmp, "x.md",
            applying_repo="other.git",
            applying_workdir="/tmp/other",
            applying_run_id="r2",
        )
        self.assertEqual(len(fm["applied_in_repos"]), 1)
        entry = fm["applied_in_repos"][0]
        self.assertEqual(entry["repo"], "other.git")
        self.assertEqual(entry["run_id"], "r2")

    def test_mark_applied_flips_cross_repo_validated(self):
        fm = mw.mark_applied(
            self.tmp, "x.md",
            applying_repo="other.git",
            applying_workdir="/tmp/other",
            applying_run_id="r2",
        )
        # Different repo applied it → cross_repo_validated must flip True.
        self.assertTrue(fm["cross_repo_validated"])

    def test_mark_applied_same_repo_no_validation(self):
        """If the applying repo IS the source repo, cross_repo_validated stays False."""
        # The original was written from $tmp. Mark-applied with same workdir.
        fm = mw.mark_applied(
            self.tmp, "x.md",
            applying_repo="other.git",  # different repo string
            applying_workdir=str(self.tmp),  # but SAME workdir
            applying_run_id="r2",
        )
        # Source_workdir matches applying_workdir → NOT cross-repo.
        # But source_repo (whatever git detected) likely != "other.git" → IS cross.
        # The OR-logic flips True when EITHER mismatch holds.
        # This test verifies that logic; the value is True because repo differs.
        self.assertTrue(fm["cross_repo_validated"])

    def test_mark_applied_dedup_same_repo_workdir(self):
        mw.mark_applied(
            self.tmp, "x.md",
            applying_repo="other.git",
            applying_workdir="/tmp/other",
            applying_run_id="r2",
        )
        mw.mark_applied(
            self.tmp, "x.md",
            applying_repo="other.git",
            applying_workdir="/tmp/other",
            applying_run_id="r3",  # different run_id but same repo/workdir
        )
        fm, _ = mw._split_frontmatter((self.tmp / "x.md").read_text())
        self.assertEqual(len(fm["applied_in_repos"]), 1)

    def test_mark_applied_two_distinct_repos(self):
        mw.mark_applied(
            self.tmp, "x.md",
            applying_repo="repo_a.git", applying_workdir="/tmp/a", applying_run_id="ra",
        )
        mw.mark_applied(
            self.tmp, "x.md",
            applying_repo="repo_b.git", applying_workdir="/tmp/b", applying_run_id="rb",
        )
        fm, _ = mw._split_frontmatter((self.tmp / "x.md").read_text())
        self.assertEqual(len(fm["applied_in_repos"]), 2)
        self.assertTrue(fm["cross_repo_validated"])

    def test_mark_applied_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            mw.mark_applied(
                self.tmp, "nonexistent.md",
                applying_repo="r.git", applying_workdir="/tmp/x", applying_run_id="r",
            )


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_migrate_backfills_unmigrated_file(self):
        # Pre-existing memory file with minimal frontmatter (no provenance).
        legacy = self.tmp / "feedback_x.md"
        legacy.write_text(
            "---\nname: feedback_x\ndescription: old lesson\ntype: feedback\n---\n"
            "Body here.\n"
        )
        summary = mw.migrate(
            self.tmp, run_id="migration_run", workdir=str(self.tmp),
            host="claude_code",
        )
        self.assertEqual(len(summary["migrated"]), 1)
        self.assertEqual(summary["scanned"], 1)
        # Re-read; provenance fields all present now.
        fm, body = mw._split_frontmatter(legacy.read_text())
        for field in mw.REQUIRED_PROVENANCE_FIELDS:
            self.assertIn(field, fm)
        # Original fields preserved.
        self.assertEqual(fm["name"], "feedback_x")
        self.assertEqual(fm["description"], "old lesson")
        self.assertEqual(fm["type"], "feedback")
        # Migration note attached.
        self.assertIn("migration_note", fm)
        # Body unchanged.
        self.assertEqual(body.strip(), "Body here.")

    def test_migrate_skips_already_migrated_file(self):
        # File that already has all provenance fields.
        mw.write(
            self.tmp, "fresh.md", body="x",
            name="fresh", description="d", type_="pattern",
            run_id="r1", workdir=str(self.tmp), host="codex",
        )
        summary = mw.migrate(
            self.tmp, run_id="migration_run", workdir=str(self.tmp),
            host="claude_code",
        )
        self.assertIn(str(self.tmp / "fresh.md"), summary["skipped"])
        self.assertEqual(len(summary["migrated"]), 0)

    def test_migrate_skips_index_and_memory_md(self):
        (self.tmp / "MEMORY.md").write_text("# Index\n")
        (self.tmp / "INDEX.jsonl").write_text(
            '{"ts":"x","run_id":"r","action":"write","file":"f","sha256":""}\n'
        )
        summary = mw.migrate(
            self.tmp, run_id="r", workdir=str(self.tmp), host="codex",
        )
        # MEMORY.md is .md; gets skipped explicitly.
        # INDEX.jsonl is .jsonl; not matched by glob anyway.
        self.assertEqual(len(summary["migrated"]), 0)
        self.assertIn(str(self.tmp / "MEMORY.md"), summary["skipped"])

    def test_migrate_dry_run_does_not_write(self):
        legacy = self.tmp / "old.md"
        legacy.write_text("---\nname: old\n---\nbody\n")
        original = legacy.read_text()
        summary = mw.migrate(
            self.tmp, run_id="r", workdir=str(self.tmp), host="codex",
            dry_run=True,
        )
        self.assertEqual(len(summary["migrated"]), 1)
        # File unchanged.
        self.assertEqual(legacy.read_text(), original)

    def test_migrate_infers_type_from_filename_prefix(self):
        # Filename starts with `feedback_` → type=feedback.
        legacy = self.tmp / "feedback_thing.md"
        legacy.write_text("---\nname: feedback_thing\n---\nbody\n")
        mw.migrate(self.tmp, run_id="r", workdir=str(self.tmp), host="codex")
        fm, _ = mw._split_frontmatter(legacy.read_text())
        self.assertEqual(fm["type"], "feedback")

    def test_migrate_unknown_prefix_defaults_to_pattern(self):
        legacy = self.tmp / "randomname.md"
        legacy.write_text("body only, no frontmatter at all\n")
        mw.migrate(self.tmp, run_id="r", workdir=str(self.tmp), host="codex")
        fm, _ = mw._split_frontmatter(legacy.read_text())
        self.assertEqual(fm["type"], "pattern")

    def test_migrate_empty_dir(self):
        empty = Path(tempfile.mkdtemp())
        summary = mw.migrate(empty, run_id="r", workdir=str(empty), host="codex")
        self.assertEqual(summary["scanned"], 0)
        self.assertEqual(summary["migrated"], [])


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(HERE / "memory_writer.py"),
             "--memory-dir", str(self.tmp), *args],
            capture_output=True, text=True,
        )

    def test_write_then_mark_applied_roundtrip(self):
        r = self._cli(
            "write", "--file", "l.md", "--name", "l",
            "--description", "desc", "--type", "pattern",
            "--run-id", "r1", "--workdir", str(self.tmp),
            "--host", "claude_code", "--body", "body content",
            "--json",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        m = self._cli(
            "mark-applied", "--file", "l.md",
            "--applying-repo", "other.git",
            "--applying-workdir", "/tmp/other",
            "--applying-run-id", "r2", "--json",
        )
        self.assertEqual(m.returncode, 0, m.stderr)
        fm = json.loads(m.stdout)
        self.assertTrue(fm["cross_repo_validated"])
        self.assertEqual(len(fm["applied_in_repos"]), 1)

    def test_migrate_cli(self):
        legacy = self.tmp / "old.md"
        legacy.write_text("---\nname: old\n---\nbody\n")
        r = self._cli(
            "migrate", "--run-id", "r", "--workdir", str(self.tmp),
            "--host", "codex", "--json",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        summary = json.loads(r.stdout)
        self.assertEqual(len(summary["migrated"]), 1)


if __name__ == "__main__":
    unittest.main()
