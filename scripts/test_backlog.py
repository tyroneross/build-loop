# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""test_backlog.py — stdlib unittest suite for scripts/backlog.py.

backlog.py is loaded via importlib from its explicit file path so the test
never collides with the sibling ``scripts/backlog/`` package (a different
capability — triage/assess). This collision-proof import is itself part of the
contract being tested: the new system must coexist with the old package.
"""
from __future__ import annotations

import ast
import importlib.util
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_BACKLOG_PY = _THIS.parent / "backlog.py"


def _load_backlog():
    """Load scripts/backlog.py as a uniquely-named module (avoids the package)."""
    spec = importlib.util.spec_from_file_location("_backlog_cli_under_test", _BACKLOG_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bl = _load_backlog()


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "myproj-app"
        self.repo.mkdir(parents=True)
        # Isolate the memory mirror into the temp dir so tests never touch the
        # real personal-memory tree.
        self.mem = Path(self._tmp.name) / "memory"
        self._old_env = __import__("os").environ.get("BUILD_LOOP_MEMORY_DIR")
        __import__("os").environ["BUILD_LOOP_MEMORY_DIR"] = str(self.mem)

    def tearDown(self) -> None:
        import os
        if self._old_env is None:
            os.environ.pop("BUILD_LOOP_MEMORY_DIR", None)
        else:
            os.environ["BUILD_LOOP_MEMORY_DIR"] = self._old_env
        self._tmp.cleanup()

    def _new(self, area="search", typ="debt", title="Do a thing", **kw):
        ns = _NS(repo=str(self.repo), area=area, type=typ, title=title,
                 priority=kw.get("priority", "P2"), status=kw.get("status", "open"),
                 gated=kw.get("gated", "none"), entities=kw.get("entities", ""),
                 evidence=kw.get("evidence", ""),
                 provenance_source=kw.get("provenance_source", ""),
                 provenance_ref=kw.get("provenance_ref", ""),
                 owner=kw.get("owner", ""), context=kw.get("context", ""),
                 notes=kw.get("notes", ""),
                 review_days=kw.get("review_days", 30),
                 today=kw.get("today", "2026-06-16"))
        return bl.cmd_new(ns)

    def _sync(self, today="2026-06-16", no_mirror=False):
        return bl.cmd_sync(_NS(repo=str(self.repo), today=today, no_mirror=no_mirror))


class _NS:
    """Tiny argparse.Namespace stand-in."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestCreate(_Base):
    def test_create_writes_item_with_schema(self):
        res = self._new(area="search", typ="debt", title="pg_trgm for keyword leg")
        path = Path(res["path"])
        self.assertTrue(path.exists())
        fm, body = bl.parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(fm["id"], res["id"])
        self.assertEqual(fm["status"], "open")
        self.assertEqual(fm["priority"], "P2")
        self.assertEqual(fm["type"], "debt")
        self.assertEqual(fm["area"], "search")
        self.assertEqual(fm["created"], "2026-06-16")
        self.assertEqual(fm["review_by"], "2026-07-16")  # +30d
        self.assertIn("## Context", body)
        self.assertIn("## Acceptance", body)
        self.assertIn("## Notes", body)

    def test_id_format(self):
        res = self._new(area="search")
        # PROJSLUG prefix (MYPR from myproj-app) - AREA - NNN
        self.assertRegex(res["id"], r"^[A-Z]+-SEARCH-\d{3}$")
        self.assertTrue(res["id"].endswith("-001"))


class TestIdIncrement(_Base):
    def test_increment_and_uniqueness(self):
        a = self._new(area="search")
        b = self._new(area="search")
        c = self._new(area="ci")
        self.assertNotEqual(a["id"], b["id"])
        self.assertTrue(a["id"].endswith("-001"))
        self.assertTrue(b["id"].endswith("-002"))
        self.assertTrue(c["id"].endswith("-001"))  # separate area counter

    def test_counter_survives_archive(self):
        # done items move to archive; the counter must still increment past them
        self._new(area="search")  # 001
        d = self._new(area="search", status="done")  # 002, will archive
        self._sync()  # archives 002
        e = self._new(area="search")  # must be 003, not 002 again
        self.assertTrue(e["id"].endswith("-003"))
        self.assertNotEqual(e["id"], d["id"])


class TestFrontmatterRoundTrip(_Base):
    def test_round_trip_preserves_fields(self):
        data = {
            "id": "MYPR-SEARCH-001",
            "title": "title: with colon and, comma",
            "status": "blocked",
            "priority": "P1",
            "type": "infra",
            "area": "search",
            "entities": ["pg_trgm", "keyword-leg"],
            "gated": "db-migration",
            "provenance": {"source": "followup", "ref": "path/to/x.md"},
            "evidence": ["commit-abc", "PR#94"],
            "supersedes": None,
            "superseded_by": None,
            "created": "2026-06-16",
            "updated": "2026-06-16",
            "review_by": "2026-07-16",
            "owner": "unassigned",
        }
        rendered = bl.render_frontmatter(data)
        parsed, _ = bl.parse_frontmatter(rendered + "\n\nbody\n")
        for key, val in data.items():
            self.assertEqual(parsed.get(key), val, f"field {key} did not round-trip")

    def test_empty_list_and_null_round_trip(self):
        data = {"id": "X-Y-001", "entities": [], "supersedes": None,
                "title": "t", "status": "open", "priority": "P3",
                "type": "fix", "area": "y", "gated": "none",
                "provenance": {}, "evidence": [], "superseded_by": None,
                "created": "2026-06-16", "updated": "2026-06-16",
                "review_by": "2026-07-16", "owner": "unassigned"}
        rendered = bl.render_frontmatter(data)
        parsed, _ = bl.parse_frontmatter(rendered + "\n\nb\n")
        self.assertEqual(parsed["entities"], [])
        self.assertIsNone(parsed["supersedes"])


class TestIndexDeterminism(_Base):
    def test_index_byte_identical_across_runs(self):
        self._new(area="search", typ="debt", title="trigram scan", priority="P1")
        self._new(area="ci", typ="fix", title="future-date test", priority="P0")
        self._new(area="infra", typ="cleanup", title="prune worktrees",
                  status="blocked", gated="db-migration")
        r1 = self._sync()
        idx1 = Path(r1["index"]["path"]).read_text(encoding="utf-8")
        r2 = self._sync()
        idx2 = Path(r2["index"]["path"]).read_text(encoding="utf-8")
        self.assertEqual(idx1, idx2, "INDEX must be byte-identical on re-sync")

    def test_index_independent_of_creation_order(self):
        # Same logical item set created in two different orders -> same INDEX.
        self._new(area="search", title="A", priority="P1")
        self._new(area="ci", title="B", priority="P0")
        idx_a = bl.render_index(self.repo, bl.load_items(self.repo), "2026-06-16")

        # Fresh repo, reverse creation order
        repo2 = self.repo.parent / "myproj-app"  # same slug for ID parity
        # Use a distinct dir but identical basename to keep IDs comparable
        import shutil
        shutil.rmtree(self.repo)
        self.repo.mkdir(parents=True)
        self._new(area="ci", title="B", priority="P0")
        self._new(area="search", title="A", priority="P1")
        idx_b = bl.render_index(self.repo, bl.load_items(self.repo), "2026-06-16")
        self.assertEqual(idx_a, idx_b)

    def test_index_flags_stale(self):
        self._new(area="search", title="old", review_days=10, today="2026-01-01")
        # today is well past the 2026-01-11 review_by
        idx = bl.render_index(self.repo, bl.load_items(self.repo), "2026-06-16")
        self.assertIn("Stale (past review_by)", idx)
        self.assertIn("Past review_by (stale): 1", idx)


class TestConsolidation(_Base):
    def test_done_moves_to_archive(self):
        r = self._new(area="search", title="finished", status="done")
        done_id = r["id"]
        self._sync()
        # item file gone from items/, present in archive/
        self.assertFalse((bl.items_dir(self.repo) / f"{done_id}.md").exists())
        self.assertTrue((bl.archive_dir(self.repo) / f"{done_id}.md").exists())

    def test_dropped_moves_to_archive(self):
        r = self._new(area="ci", title="abandoned", status="dropped")
        self._sync()
        self.assertTrue((bl.archive_dir(self.repo) / f"{r['id']}.md").exists())

    def test_open_stays(self):
        r = self._new(area="search", title="active", status="open")
        self._sync()
        self.assertTrue((bl.items_dir(self.repo) / f"{r['id']}.md").exists())

    def test_past_review_by_flagged(self):
        self._new(area="search", title="stale one", review_days=5, today="2026-01-01")
        res = self._sync()
        self.assertEqual(len(res["consolidation"]["stale"]), 1)


class TestMirror(_Base):
    def test_mirror_writes_to_memory(self):
        self._new(area="search", title="mirror me")
        res = self._sync()
        self.assertGreaterEqual(res["mirror"]["written"], 1)
        mem_dir = Path(res["mirror"]["dir"])
        self.assertTrue(mem_dir.is_dir())
        self.assertTrue((mem_dir / "INDEX.md").exists())
        mirrored = list(mem_dir.glob("*-SEARCH-*.md"))
        self.assertEqual(len(mirrored), 1)

    def test_no_mirror_flag_skips(self):
        self._new(area="search", title="x")
        res = self._sync(no_mirror=True)
        self.assertEqual(res["mirror"]["written"], 0)


class TestList(_Base):
    def test_filter_by_status(self):
        self._new(area="search", title="open one", status="open")
        self._new(area="search", title="blocked one", status="blocked")
        res = bl.cmd_list(_NS(repo=str(self.repo), status="blocked", area="",
                              priority="", include_archive=False))
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["items"][0]["status"], "blocked")

    def test_filter_by_area(self):
        self._new(area="search", title="s")
        self._new(area="ci", title="c")
        res = bl.cmd_list(_NS(repo=str(self.repo), status="", area="ci",
                              priority="", include_archive=False))
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["items"][0]["area"], "ci")


class TestRoundTripFidelity(_Base):
    """Regression tests for auditor findings f1 (quote escape accumulation),
    f2 (comma-in-list element loss), f3 (mirror prune over-reach)."""

    def test_f1_quote_value_round_trips_byte_for_byte(self):
        v = 'a "q" : b'  # contains a quote AND a colon (triggers needs_quote)
        d = {"id": "X-Y-001", "title": v, "status": "open", "priority": "P1",
             "type": "fix", "area": "y", "gated": "none", "provenance": {},
             "evidence": [], "entities": [], "supersedes": None,
             "superseded_by": None, "created": "2026-06-16",
             "updated": "2026-06-16", "review_by": "2026-07-16", "owner": "u"}
        r1 = bl.render_frontmatter(d)
        p1, _ = bl.parse_frontmatter(r1 + "\n\nb\n")
        self.assertEqual(p1["title"], v, "quote-bearing value must round-trip exactly")

    def test_f1_no_backslash_accumulation_across_cycles(self):
        v = 'has "quotes" and a \\ backslash'
        d = {"id": "X-Y-001", "title": v, "status": "open", "priority": "P1",
             "type": "fix", "area": "y", "gated": "none", "provenance": {},
             "evidence": [], "entities": [], "supersedes": None,
             "superseded_by": None, "created": "2026-06-16",
             "updated": "2026-06-16", "review_by": "2026-07-16", "owner": "u"}
        p = d
        for _ in range(3):
            p, _ = bl.parse_frontmatter(bl.render_frontmatter(p) + "\n\nb\n")
        self.assertEqual(p["title"], v, "value must be stable across repeated cycles")

    def test_f2_comma_bearing_list_element_preserved(self):
        d = {"id": "X-Y-001", "title": "t", "status": "open", "priority": "P1",
             "type": "fix", "area": "y", "gated": "none", "provenance": {},
             "evidence": [], "entities": ["a,b", "c"], "supersedes": None,
             "superseded_by": None, "created": "2026-06-16",
             "updated": "2026-06-16", "review_by": "2026-07-16", "owner": "u"}
        p, _ = bl.parse_frontmatter(bl.render_frontmatter(d) + "\n\nb\n")
        self.assertEqual(p["entities"], ["a,b", "c"],
                         "comma-bearing element must stay one element")

    def test_f2_new_command_with_comma_entity(self):
        # Full path: --entities with a comma-bearing token survives sync round-trip.
        res = self._new(area="search", title="x", entities="pg_trgm, with comma")
        # _csv splits on comma, so "pg_trgm" and "with comma" become two entities;
        # the point is neither is corrupted on re-read.
        fm, _ = bl.parse_frontmatter(Path(res["path"]).read_text(encoding="utf-8"))
        for e in fm["entities"]:
            self.assertNotIn('\\', e)

    def test_f3_mirror_prune_spares_non_item_files(self):
        self._new(area="search", title="x")
        res = self._sync()
        mem_dir = Path(res["mirror"]["dir"])
        # Drop a hand note in the mirror dir.
        note = mem_dir / "USER-NOTES.md"
        note.write_text("my hand-written note\n", encoding="utf-8")
        # Re-sync; the note must survive (it is not item-ID-shaped).
        self._sync()
        self.assertTrue(note.exists(), "non-item .md must not be pruned")

    def test_f3_mirror_prune_removes_orphan_item(self):
        r = self._new(area="search", title="will be removed")
        res = self._sync()
        mem_dir = Path(res["mirror"]["dir"])
        mirrored = mem_dir / f"{r['id']}.md"
        self.assertTrue(mirrored.exists())
        # Delete the source item, re-sync -> mirror copy must be pruned.
        (bl.items_dir(self.repo) / f"{r['id']}.md").unlink()
        self._sync()
        self.assertFalse(mirrored.exists(), "orphaned item mirror must be pruned")


class TestSlugOverride(_Base):
    def test_env_slug_overrides_dir_basename(self):
        import os
        old = os.environ.get("BACKLOG_SLUG")
        os.environ["BACKLOG_SLUG"] = "atomize-ai"
        try:
            # repo dir basename is myproj-app, but slug must be the override
            self.assertEqual(bl.project_slug(self.repo), "atomize-ai")
            res = self._new(area="search", title="x")
            # ID prefix derives from the override slug -> ATOM
            self.assertTrue(res["id"].startswith("ATOM-SEARCH-"))
            self.assertEqual(res["slug"], "atomize-ai")
        finally:
            if old is None:
                os.environ.pop("BACKLOG_SLUG", None)
            else:
                os.environ["BACKLOG_SLUG"] = old

    def test_mirror_path_uses_override_slug(self):
        import os
        old = os.environ.get("BACKLOG_SLUG")
        os.environ["BACKLOG_SLUG"] = "atomize-ai"
        try:
            self._new(area="search", title="x")
            res = self._sync()
            self.assertIn("/projects/atomize-ai/backlog", res["mirror"]["dir"])
        finally:
            if old is None:
                os.environ.pop("BACKLOG_SLUG", None)
            else:
                os.environ["BACKLOG_SLUG"] = old


class TestNoThirdPartyImports(unittest.TestCase):
    """Assert backlog.py imports ONLY Python stdlib — the host-agnostic contract."""

    # Python 3.10+ stdlib top-level module allowlist used by backlog.py.
    _STDLIB = {
        "__future__", "argparse", "datetime", "json", "os", "re",
        "sys", "pathlib", "typing",
    }

    def test_only_stdlib_imports(self):
        tree = ast.parse(_BACKLOG_PY.read_text(encoding="utf-8"))
        imported_top: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_top.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    imported_top.add(node.module.split(".")[0])
        nonstdlib = imported_top - self._STDLIB
        self.assertEqual(
            nonstdlib, set(),
            f"backlog.py must import only stdlib; found non-allowlisted: {sorted(nonstdlib)}",
        )


if __name__ == "__main__":
    unittest.main()
