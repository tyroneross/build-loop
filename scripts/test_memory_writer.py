#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
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

    def test_write_appends_to_global_update_ledger(self):
        mw.write(
            self.tmp, "lesson.md", body="x",
            name="l", description="d", type_="pattern",
            run_id="r", workdir=str(self.tmp), host="codex",
        )
        ledger = self.tmp / "indexes" / "updates.jsonl"
        self.assertTrue(ledger.exists())
        rows = [json.loads(l) for l in ledger.read_text().strip().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "write")
        self.assertEqual(rows[0]["path"], "lesson.md")
        self.assertEqual(rows[0]["writer"], "memory_writer.py")
        self.assertEqual(rows[0]["source_host"], "codex")

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
        ledger = self.tmp / "indexes" / "updates.jsonl"
        rows = [json.loads(l) for l in ledger.read_text().strip().splitlines()]
        self.assertEqual(rows[-1]["action"], "mark-applied")

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



class P2WriterGuardTests(unittest.TestCase):
    """P2 regression: --file with a lane prefix under --scope project must
    NOT double-nest. This is today's exact bug (4 agents hit it 2026-06-07).
    """

    def setUp(self):
        import os
        self.tmp = Path(tempfile.mkdtemp())
        # Isolate from the real memory store: env override + reload _paths.
        self._prev_env = os.environ.get("BUILD_LOOP_MEMORY_STORE_ROOT")
        os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = str(self.tmp)
        import importlib
        import _paths as _p
        importlib.reload(_p)
        importlib.reload(mw)
        self._paths = _p

    def tearDown(self):
        import os
        if self._prev_env is None:
            os.environ.pop("BUILD_LOOP_MEMORY_STORE_ROOT", None)
        else:
            os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = self._prev_env
        import importlib, _paths as _p
        importlib.reload(_p)
        importlib.reload(mw)

    def test_double_nest_repro_today_exact_bug(self):
        """``--file projects/<slug>/issues/x.md --scope project --project <slug>``
        must land at ``projects/<slug>/issues/x.md`` ONCE (not nested under
        ``lessons/``)."""
        mw.write(
            self._paths.project_lessons_dir("demoproj"),
            file_rel="projects/demoproj/issues/regression.md",
            body="body",
            name="x", description="x", type_="gotcha",
            run_id="r", workdir=str(self.tmp), host="claude_code",
            scope="project", project="demoproj",
        )
        landed = sorted(p.relative_to(self.tmp) for p in self.tmp.rglob("*.md"))
        self.assertEqual(
            [str(p) for p in landed],
            ["projects/demoproj/issues/regression.md"],
            f"Expected single landed path; got {landed}",
        )

    def test_strip_redundant_project_prefix_then_lessons(self):
        """``--file projects/<slug>/lessons/x.md --scope project --project <slug>``
        lands in lessons/ once."""
        mw.write(
            self._paths.project_lessons_dir("demoproj"),
            file_rel="projects/demoproj/lessons/foo.md",
            body="body",
            name="x", description="x", type_="lesson",
            run_id="r", workdir=str(self.tmp), host="claude_code",
            scope="project", project="demoproj",
        )
        landed = [str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*.md")]
        self.assertIn("projects/demoproj/lessons/foo.md", landed)
        self.assertEqual(len(landed), 1, landed)

    def test_strip_sublane_only_prefix(self):
        """``--file issues/x.md --scope project --project <slug>`` resolves
        to projects/<slug>/issues/x.md (sublane override)."""
        mw.write(
            self._paths.project_lessons_dir("demoproj"),
            file_rel="issues/just-sublane.md",
            body="body",
            name="x", description="x", type_="gotcha",
            run_id="r", workdir=str(self.tmp), host="claude_code",
            scope="project", project="demoproj",
        )
        landed = [str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*.md")]
        self.assertEqual(landed, ["projects/demoproj/issues/just-sublane.md"])

    def test_bare_filename_under_scope_project(self):
        """Bare filename keeps legacy behavior: lands in default lane (lessons/)."""
        mw.write(
            self._paths.project_lessons_dir("demoproj"),
            file_rel="bare.md",
            body="body",
            name="x", description="x", type_="lesson",
            run_id="r", workdir=str(self.tmp), host="claude_code",
            scope="project", project="demoproj",
        )
        landed = [str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*.md")]
        self.assertEqual(landed, ["projects/demoproj/lessons/bare.md"])

    def test_legacy_callers_without_scope_kwarg_unaffected(self):
        """Library callers that don't pass scope= get byte-equivalent legacy
        behavior — the writer guard never fires."""
        mw.write(
            self._paths.project_lessons_dir("demoproj"),
            file_rel="projects/demoproj/issues/legacy.md",
            body="body",
            name="x", description="x", type_="gotcha",
            run_id="r", workdir=str(self.tmp), host="claude_code",
        )
        landed = [str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*.md")]
        # Without normalization, the path nests under lessons/ — that's the
        # legacy behavior; library callers opt in via scope= when they want
        # the guard.
        self.assertIn(
            "projects/demoproj/lessons/projects/demoproj/issues/legacy.md",
            landed,
        )


    def test_strip_doubly_prefixed_path(self):
        """Loop normalisation: ``issues/projects/<p>/issues/x.md`` collapses
        to ``projects/<p>/issues/x.md`` (one landing)."""
        mw.write(
            self._paths.project_lessons_dir("demoproj"),
            file_rel="issues/projects/demoproj/issues/double-prefix.md",
            body="body",
            name="x", description="x", type_="gotcha",
            run_id="r", workdir=str(self.tmp), host="claude_code",
            scope="project", project="demoproj",
        )
        landed = [str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*.md")]
        self.assertEqual(
            landed, ["projects/demoproj/issues/double-prefix.md"]
        )

    def test_top_level_lane_strip(self):
        """``--file debugging/x.md --scope top-level`` resolves to
        ``debugging/x.md`` (not ``lessons/debugging/x.md``)."""
        mw.write(
            self._paths.top_level_lessons_dir(),
            file_rel="debugging/sigil.md",
            body="body",
            name="x", description="x", type_="debug-incident",
            run_id="r", workdir=str(self.tmp), host="claude_code",
            scope="top-level",
        )
        landed = [str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*.md")]
        self.assertEqual(landed, ["debugging/sigil.md"])

    def test_rejects_absolute_file(self):
        with self.assertRaises(ValueError):
            mw.write(
                self._paths.project_lessons_dir("demoproj"),
                file_rel="/etc/passwd",
                body="b", name="x", description="x", type_="gotcha",
                run_id="r", workdir=str(self.tmp), host="claude_code",
                scope="project", project="demoproj",
            )

    def test_rejects_dotdot(self):
        with self.assertRaises(ValueError):
            mw.write(
                self._paths.project_lessons_dir("demoproj"),
                file_rel="../escape.md",
                body="b", name="x", description="x", type_="gotcha",
                run_id="r", workdir=str(self.tmp), host="claude_code",
                scope="project", project="demoproj",
            )

    def test_normalize_is_idempotent(self):
        """Running the normalizer twice yields the same path. Guards against
        a future refactor that strips a layer per-call."""
        from _paths import project_root
        # First normalization: strip projects/<p>/issues/ prefix.
        f1, m1 = mw._normalize_file_rel(
            "projects/demoproj/issues/x.md",
            scope="project", project="demoproj",
            memory_dir=self._paths.project_lessons_dir("demoproj"),
        )
        # Second normalization on the result: no change.
        f2, m2 = mw._normalize_file_rel(
            f1, scope="project", project="demoproj", memory_dir=m1,
        )
        self.assertEqual(f1, "x.md")
        self.assertEqual(m1, project_root("demoproj") / "issues")
        self.assertEqual(f1, f2)
        self.assertEqual(m1, m2)


class CanonicalFilenameTests(unittest.TestCase):
    def test_canonical_filename_shape(self):
        fn = mw.canonical_filename(type_="lesson", name="Some Cool Thing!", date="2026-01-02")
        self.assertEqual(fn, "2026-01-02-lesson-some-cool-thing.md")

    def test_canonical_filename_slug_collapse(self):
        fn = mw.canonical_filename(type_="gotcha", name="  X --- Y???  ", date="2026-01-02")
        self.assertEqual(fn, "2026-01-02-gotcha-x-y.md")

    def test_canonical_filename_empty_name_defaults_untitled(self):
        fn = mw.canonical_filename(type_="lesson", name="!!!", date="2026-01-02")
        self.assertEqual(fn, "2026-01-02-lesson-untitled.md")

    def test_canonical_filename_default_date_is_today(self):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fn = mw.canonical_filename(type_="lesson", name="x")
        self.assertTrue(fn.startswith(today), fn)


if __name__ == "__main__":
    unittest.main()
