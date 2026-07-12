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

    def _sync(self, today="2026-06-16", no_mirror=False, prune=False):
        return bl.cmd_sync(_NS(repo=str(self.repo), today=today,
                               no_mirror=no_mirror, prune=prune))


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
        # PROJSLUG prefix (MYPR from myproj-app) - AREA - <token>. The token is
        # lowercase Crockford base32 (no coordination), NOT a sequential -NNN.
        self.assertRegex(res["id"], r"^[A-Z]+-SEARCH-[0-9a-z]{6,}$")
        # The suffix is a token, not the legacy zero-padded counter.
        self.assertFalse(res["id"].endswith("-001"))
        # And the ID-shape regex (the single membership test) accepts it.
        self.assertRegex(res["id"], bl._ITEM_ID_RE)


class TestIdIncrement(_Base):
    def test_ids_are_unique_per_area_and_across_areas(self):
        a = self._new(area="search")
        b = self._new(area="search")
        c = self._new(area="ci")
        # Uniqueness — not sequence — is the contract now.
        self.assertNotEqual(a["id"], b["id"])
        self.assertNotEqual(a["id"], c["id"])
        # Prefix is still readable + area-scoped.
        self.assertIn("-SEARCH-", a["id"])
        self.assertIn("-SEARCH-", b["id"])
        self.assertIn("-CI-", c["id"])

    def test_ids_unique_even_after_archive(self):
        # An archived item never frees or collides with a later item's ID — but
        # now that's guaranteed by token uniqueness, not by a monotonic counter.
        self._new(area="search")
        d = self._new(area="search", status="done")  # will archive
        self._sync()  # archives the done item
        e = self._new(area="search")
        self.assertNotEqual(e["id"], d["id"])
        # All three distinct.
        archived = list(bl.archive_dir(self.repo).glob("*.md"))
        self.assertEqual(len(archived), 1)

    def test_ids_roughly_time_ordered(self):
        # The time-prefixed token makes IDs sort roughly by creation, so `ls`
        # navigation stays useful. Ordering is carried by the TIME PREFIX (first
        # _TOKEN_TIME_WIDTH chars); the random tail breaks ties WITHIN a
        # millisecond (expected, and irrelevant to navigation). Assert the time
        # prefixes are monotonic non-decreasing.
        ids = [self._new(area="search")["id"] for _ in range(5)]
        time_prefixes = [
            i.rsplit("-", 1)[1][:bl._TOKEN_TIME_WIDTH] for i in ids
        ]
        self.assertEqual(time_prefixes, sorted(time_prefixes),
                         "time-prefix must be monotonic (roughly creation-ordered)")

    def test_mint_id_token_monotonic_time_prefix(self):
        # Direct: a later timestamp yields a lexically >= time prefix.
        t1 = bl.mint_id_token(now_ms=1_000_000_000_000)
        t2 = bl.mint_id_token(now_ms=1_000_000_001_000)
        self.assertLess(t1[:bl._TOKEN_TIME_WIDTH], t2[:bl._TOKEN_TIME_WIDTH])


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
        # Same logical item set created in two different orders -> same ROW
        # ORDERING. IDs are now random per-mint, so the INDEX is no longer
        # byte-identical across two creation runs (that property belonged to the
        # sequential counter). The durable contract is that sort order
        # (status -> area -> priority) is independent of creation order; assert
        # that on the rendered rows minus the random id column.
        def _ordered_rows(idx_text: str) -> list[str]:
            # Keep table rows, strip the first (id) cell so random IDs don't
            # defeat the comparison. A table row looks like `| id | ... |`.
            rows = []
            for ln in idx_text.splitlines():
                if ln.startswith("| ") and "----" not in ln and " id " not in f" {ln} ":
                    cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                    rows.append("|".join(cells[1:]))  # drop id cell
            return rows

        self._new(area="search", title="A", priority="P1")
        self._new(area="ci", title="B", priority="P0")
        idx_a = bl.render_index(self.repo, bl.load_items(self.repo), "2026-06-16")

        # Fresh repo, reverse creation order.
        import shutil
        shutil.rmtree(self.repo)
        self.repo.mkdir(parents=True)
        self._new(area="ci", title="B", priority="P0")
        self._new(area="search", title="A", priority="P1")
        idx_b = bl.render_index(self.repo, bl.load_items(self.repo), "2026-06-16")

        self.assertEqual(_ordered_rows(idx_a), _ordered_rows(idx_b),
                         "row ordering must be independent of creation order")

    def test_index_byte_identical_for_same_items(self):
        # Determinism still holds for a FIXED item set: re-rendering the same
        # items (same IDs on disk) is byte-identical. This is the property `sync`
        # relies on to be diff-clean; only the cross-creation-run identity was
        # lost to random IDs.
        self._new(area="search", title="A", priority="P1")
        self._new(area="ci", title="B", priority="P0")
        items = bl.load_items(self.repo)
        self.assertEqual(
            bl.render_index(self.repo, items, "2026-06-16"),
            bl.render_index(self.repo, items, "2026-06-16"),
        )

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

    def test_f3_default_sync_keeps_orphan_mirror_item(self):
        # MERGE-by-id contract (BUIL-TOOLING-syncsafety-001): a mirror item whose
        # local source disappears is NOT deleted by a default sync. The local
        # store can be partial/mis-rooted, so a default sync must never prune.
        r = self._new(area="search", title="orphan-safe")
        res = self._sync()
        mem_dir = Path(res["mirror"]["dir"])
        mirrored = mem_dir / f"{r['id']}.md"
        self.assertTrue(mirrored.exists())
        (bl.items_dir(self.repo) / f"{r['id']}.md").unlink()
        res2 = self._sync()  # default: no --prune
        self.assertTrue(mirrored.exists(),
                        "default sync must NOT prune an orphaned mirror item")
        self.assertEqual(res2["mirror"]["pruned"], [])

    def test_f3_prune_flag_removes_orphan_item(self):
        # Opt-in --prune (local store known authoritative) DOES remove an
        # item-ID-shaped mirror file whose source is gone.
        r = self._new(area="search", title="will be pruned")
        res = self._sync()
        mem_dir = Path(res["mirror"]["dir"])
        mirrored = mem_dir / f"{r['id']}.md"
        self.assertTrue(mirrored.exists())
        (bl.items_dir(self.repo) / f"{r['id']}.md").unlink()
        res2 = self._sync(prune=True)
        self.assertFalse(mirrored.exists(),
                         "--prune must remove an orphaned mirror item")
        self.assertIn(f"{r['id']}.md", res2["mirror"]["pruned"])


class TestSlugOverride(_Base):
    def test_env_slug_overrides_dir_basename(self):
        import os
        old = os.environ.get("BACKLOG_SLUG")
        os.environ["BACKLOG_SLUG"] = "sample-app"
        try:
            # repo dir basename is myproj-app, but slug must be the override
            self.assertEqual(bl.project_slug(self.repo), "sample-app")
            res = self._new(area="search", title="x")
            # ID prefix derives from the override slug -> SAMP
            self.assertTrue(res["id"].startswith("SAMP-SEARCH-"))
            self.assertEqual(res["slug"], "sample-app")
        finally:
            if old is None:
                os.environ.pop("BACKLOG_SLUG", None)
            else:
                os.environ["BACKLOG_SLUG"] = old

    def test_mirror_path_uses_override_slug(self):
        import os
        old = os.environ.get("BACKLOG_SLUG")
        os.environ["BACKLOG_SLUG"] = "sample-app"
        try:
            self._new(area="search", title="x")
            res = self._sync()
            self.assertIn("/projects/sample-app/backlog", res["mirror"]["dir"])
        finally:
            if old is None:
                os.environ.pop("BACKLOG_SLUG", None)
            else:
                os.environ["BACKLOG_SLUG"] = old


class TestConcurrentCreate(_Base):
    """Change 1 — the atomic-create race fix.

    Before: `new` read max-NNN then wrote, a TOCTOU race; 6 concurrent creates
    in one area produced only 2 survivors (4 silently clobbered). This is the
    core multi-agent use case (Claude + Codex / parallel fan-out adding items).
    After: O_EXCL create + bounded recompute-and-retry → N distinct files, N
    unique IDs, zero loss.
    """

    def _spawn_new(self, area: str, n: int) -> None:
        """Launch n `backlog.py new` PROCESSES in parallel against one area."""
        import os as _os
        import subprocess
        env = dict(_os.environ)
        env["BUILD_LOOP_MEMORY_DIR"] = str(self.mem)
        procs = []
        for i in range(n):
            procs.append(subprocess.Popen(
                ["python3", str(_BACKLOG_PY), "new",
                 "--repo", str(self.repo), "--area", area, "--type", "debt",
                 "--title", f"concurrent item {i}", "--today", "2026-06-16"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
            ))
        # Start them as close together as possible, then wait for all.
        for p in procs:
            p.wait()

    def test_8_parallel_new_same_area_zero_loss(self):
        # The O_EXCL fix (kept) must still guarantee 8/8 survivors on ONE
        # filesystem — the token change must not regress same-fs concurrency.
        n = 8
        self._spawn_new("search", n)
        files = list(bl.items_dir(self.repo).glob("*-SEARCH-*.md"))
        self.assertEqual(len(files), n,
                         f"expected {n} item files, got {len(files)} (data loss)")
        ids = set()
        for f in files:
            fm, _ = bl.parse_frontmatter(f.read_text(encoding="utf-8"))
            ids.add(fm["id"])
            # the stem and the frontmatter id must agree (no clobbered content)
            self.assertEqual(f.stem, fm["id"])
            # every survivor is a valid token-shaped item ID
            self.assertRegex(fm["id"], bl._ITEM_ID_RE)
        self.assertEqual(len(ids), n, "IDs must be unique across all survivors")

    def test_atomic_helper_distinct_ids_no_clobber(self):
        # In-process check that two creates in one area produce two distinct,
        # non-clobbered files (the O_EXCL path holds with token IDs).
        a = self._new(area="ci", title="first")
        b = self._new(area="ci", title="second")
        self.assertNotEqual(a["id"], b["id"])
        self.assertTrue(Path(a["path"]).exists())
        self.assertTrue(Path(b["path"]).exists())

    def test_atomic_create_remints_on_forced_token_collision(self):
        # Force the rare same-token clash: a stubbed minter returns a duplicate
        # token once, then a unique one. The O_EXCL retry must catch the clash
        # and re-mint rather than clobber the existing file.
        import os as _os
        repo = self.repo
        bl.items_dir(repo).mkdir(parents=True, exist_ok=True)
        seq = iter(["aaaaaaaaaaaaa", "aaaaaaaaaaaaa", "bbbbbbbbbbbbb"])
        prefix = f"{bl.proj_id_prefix(repo)}-{bl.area_slug('ci')}-"
        orig = bl.make_item_id
        try:
            bl.make_item_id = lambda r, area: prefix + next(seq)  # type: ignore
            id1, p1 = bl.atomic_create_item(repo, "ci", lambda i: f"---\nid: {i}\n---\n")
            id2, p2 = bl.atomic_create_item(repo, "ci", lambda i: f"---\nid: {i}\n---\n")
        finally:
            bl.make_item_id = orig
        self.assertEqual(id1, prefix + "aaaaaaaaaaaaa")
        # Second create's first token collided with id1 -> re-minted to the next.
        self.assertEqual(id2, prefix + "bbbbbbbbbbbbb")
        self.assertNotEqual(p1, p2)
        self.assertTrue(p1.exists() and p2.exists())


class TestSchemaVersioning(_Base):
    """Change 3 — schema versioning + tolerant reader (download/upgrade compat).

    The reader must DEFAULT missing fields and PRESERVE unknown fields so an
    item written by a newer OR older build-loop still reads cleanly.
    """

    def test_new_item_carries_schema_version(self):
        res = self._new(area="search", title="x")
        fm, _ = bl.parse_frontmatter(Path(res["path"]).read_text(encoding="utf-8"))
        # On disk every scalar is text; compare against the string form.
        self.assertEqual(str(fm["schema_version"]), str(bl.SCHEMA_VERSION))

    def test_item_missing_new_fields_reads_with_defaults(self):
        # An OLD-style item: no schema_version, no owner, no entities.
        old_text = (
            "---\n"
            "id: OLD-SEARCH-001\n"
            "title: legacy item\n"
            "status: open\n"
            "area: search\n"
            "---\n\n## Context\nlegacy\n"
        )
        item, body = bl.read_item(old_text)
        self.assertEqual(item["id"], "OLD-SEARCH-001")
        self.assertEqual(item["title"], "legacy item")
        # defaulted fields present despite being absent in the source.
        # Absent -> the int default from item_defaults() is supplied.
        self.assertEqual(item["schema_version"], bl.SCHEMA_VERSION)
        self.assertEqual(item["priority"], "P2")
        self.assertEqual(item["owner"], "unassigned")
        self.assertEqual(item["entities"], [])
        self.assertEqual(item["gated"], "none")
        self.assertEqual(item["provenance"], {})
        self.assertIn("legacy", body)

    def test_item_with_unknown_future_field_reads_without_error(self):
        # A NEWER-style item carrying a field this version doesn't know.
        future_text = (
            "---\n"
            "id: NEW-SEARCH-002\n"
            "schema_version: 99\n"
            "title: future item\n"
            "status: open\n"
            "area: search\n"
            "horizon_score: 0.91\n"          # unknown future field (scalar)
            "linked_runs: [r1, r2]\n"        # unknown future field (list)
            "---\n\n## Context\nfuture\n"
        )
        item, _ = bl.read_item(future_text)
        # unknown fields preserved intact (present, non-None, carry the value)
        self.assertIn("horizon_score", item)
        self.assertEqual(str(item["horizon_score"]), "0.91")
        self.assertEqual(item["linked_runs"], ["r1", "r2"])
        # newer schema_version preserved (explicit value wins over default).
        # On disk it's text, so the explicit "99" is preserved as-is.
        self.assertEqual(str(item["schema_version"]), "99")
        # known fields still readable
        self.assertEqual(item["id"], "NEW-SEARCH-002")
        self.assertEqual(item["title"], "future item")

    def test_load_items_applies_tolerant_defaults_on_disk(self):
        # Closure proof for the wiring fix: the PRODUCTION read path (load_items)
        # — not just read_item in isolation — must default missing fields and
        # preserve unknown ones. An old-style item on disk with no schema_version
        # / owner / entities must come back defaulted; an unknown field survives.
        old_item = (
            "---\n"
            "id: OLD-SEARCH-001\n"
            "title: legacy on disk\n"
            "status: open\n"
            "area: search\n"
            "future_field: keep-me\n"
            "---\n\n## Context\nlegacy\n"
        )
        bl.items_dir(self.repo).mkdir(parents=True, exist_ok=True)
        (bl.items_dir(self.repo) / "OLD-SEARCH-001.md").write_text(
            old_item, encoding="utf-8")
        items = bl.load_items(self.repo)
        self.assertEqual(len(items), 1)
        it = items[0]
        # defaulted-but-absent known fields are present
        self.assertEqual(it["owner"], "unassigned")
        self.assertEqual(it["priority"], "P2")
        self.assertEqual(it["entities"], [])
        self.assertEqual(it["schema_version"], bl.SCHEMA_VERSION)
        # unknown future field preserved through the production read
        self.assertEqual(it["future_field"], "keep-me")
        # the read still works for INDEX rendering (no None-status crash)
        idx = bl.render_index(self.repo, items, "2026-06-16")
        self.assertIn("OLD-SEARCH-001", idx)


class TestAdopt(_Base):
    """Change 2 — `adopt`: safe, idempotent migration of existing data.

    Additive and never-destructive: gitignore-fix + scaffold + import existing
    followup/issues/proposals as items that LINK to (never move) their source.
    Dry-run by default; --apply executes; re-apply is a no-op (idempotent).
    """

    def _seed_queue(self, kind: str, name: str, body: str) -> Path:
        qdir = self.repo / ".build-loop" / kind
        qdir.mkdir(parents=True, exist_ok=True)
        p = qdir / name
        p.write_text(body, encoding="utf-8")
        return p

    def _adopt(self, apply=False, today="2026-06-16", no_mirror=True):
        ns = _NS(repo=str(self.repo), apply=apply, today=today,
                 review_days=30, no_mirror=no_mirror, slug="")
        return bl.cmd_adopt(ns)

    def test_dry_run_writes_nothing(self):
        self._seed_queue("followup", "stuck-thing.md", "# Stuck thing\n\nbody\n")
        res = self._adopt(apply=False)
        self.assertEqual(res["mode"], "dry-run")
        # No items dir materialised, no INDEX, no BACKLOG.md written.
        self.assertFalse(bl.items_dir(self.repo).exists())
        self.assertFalse((self.repo / "BACKLOG.md").exists())
        # But the planned import IS reported.
        self.assertEqual(len(res["imported"]), 1)
        self.assertFalse(res["imported"][0]["applied"])

    def test_apply_imports_with_provenance_and_evidence(self):
        src = self._seed_queue("issues", "broken-login.md",
                               "# Broken login\n\nrepro steps\n")
        res = self._adopt(apply=True)
        self.assertEqual(res["mode"], "apply")
        self.assertEqual(len(res["imported"]), 1)
        item_path = Path(res["imported"][0]["path"])
        fm, _ = bl.parse_frontmatter(item_path.read_text(encoding="utf-8"))
        rel = src.relative_to(self.repo).as_posix()
        self.assertEqual(fm["provenance"]["source"], "issues")
        self.assertEqual(fm["provenance"]["ref"], rel)
        self.assertIn(rel, fm["evidence"])
        self.assertEqual(fm["imported_from"], rel)
        self.assertEqual(fm["status"], "open")
        self.assertEqual(fm["type"], "fix")  # issues -> fix
        self.assertEqual(fm["title"], "Broken login")  # first heading
        # Source file is UNTOUCHED (still present, still its original content).
        self.assertTrue(src.exists())
        self.assertEqual(src.read_text(encoding="utf-8"),
                         "# Broken login\n\nrepro steps\n")

    def test_apply_is_idempotent_no_dupes(self):
        self._seed_queue("followup", "a.md", "# A\n")
        self._seed_queue("proposals", "b.md", "# B\n")
        r1 = self._adopt(apply=True)
        self.assertEqual(len(r1["imported"]), 2)
        items_after_first = sorted(p.name for p in bl.items_dir(self.repo).glob("*.md"))
        # Second run: nothing new imported, both sources skipped.
        r2 = self._adopt(apply=True)
        self.assertEqual(len(r2["imported"]), 0)
        self.assertEqual(sorted(r2["skipped_already_imported"]),
                         [".build-loop/followup/a.md", ".build-loop/proposals/b.md"])
        items_after_second = sorted(p.name for p in bl.items_dir(self.repo).glob("*.md"))
        self.assertEqual(items_after_first, items_after_second,
                         "re-adopt must not create duplicate items")

    def test_gitignore_guard_appends_unignore(self):
        # Simulate the default-ignored case (build-loop + sample both do this).
        (self.repo / ".gitignore").write_text(".build-loop/\nnode_modules/\n",
                                              encoding="utf-8")
        res = self._adopt(apply=True)
        self.assertTrue(res["gitignore"]["was_ignored"])
        self.assertIn("!/.build-loop/backlog/", res["gitignore"]["added"])
        gi = (self.repo / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("!/.build-loop/backlog/", gi)
        self.assertIn("!/BACKLOG.md", gi)

    def test_gitignore_guard_idempotent(self):
        (self.repo / ".gitignore").write_text(".build-loop/\n", encoding="utf-8")
        self._adopt(apply=True)
        gi1 = (self.repo / ".gitignore").read_text(encoding="utf-8")
        # Re-run: the un-ignore block must not be appended twice.
        self._adopt(apply=True)
        gi2 = (self.repo / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(gi1, gi2, "un-ignore rules must not duplicate on re-adopt")
        # Exact-line count (avoid the !.build-loop/backlog/** substring match).
        exact = [ln.strip() for ln in gi2.splitlines() if ln.strip() == "!/.build-loop/backlog/"]
        self.assertEqual(len(exact), 1)

    def test_gitignore_unignore_is_root_scoped(self):
        import subprocess

        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / ".gitignore").write_text(
            ".build-loop/\n"
            "# build-loop backlog (added by `backlog.py adopt` — keep so the backlog travels)\n"
            "!.build-loop/\n"
            ".build-loop/*\n"
            "!.build-loop/backlog/\n"
            "!.build-loop/backlog/**\n"
            "!BACKLOG.md\n",
            encoding="utf-8",
        )
        report = self._adopt(apply=True)
        self.assertEqual(report["gitignore"]["action"], "gitignore_unignore_migrated")
        migrated = (self.repo / ".gitignore").read_text(encoding="utf-8")
        self.assertNotIn("!.build-loop/backlog/", migrated)
        self.assertIn("!/.build-loop/backlog/", migrated)
        root_item = self.repo / ".build-loop" / "backlog" / "items" / "root.md"
        nested_item = (
            self.repo
            / "agent-rally-point"
            / ".build-loop"
            / "backlog"
            / "items"
            / "nested.md"
        )
        root_item.parent.mkdir(parents=True, exist_ok=True)
        nested_item.parent.mkdir(parents=True, exist_ok=True)
        root_item.write_text("root\n", encoding="utf-8")
        nested_item.write_text("nested\n", encoding="utf-8")

        root = subprocess.run(
            ["git", "-C", str(self.repo), "check-ignore", "-q", str(root_item)],
            check=False,
        )
        nested = subprocess.run(
            ["git", "-C", str(self.repo), "check-ignore", "-q", str(nested_item)],
            check=False,
        )
        self.assertEqual(root.returncode, 1, "repo-root backlog must be visible")
        self.assertEqual(nested.returncode, 0, "nested runtime backlog must stay ignored")

    def test_gitignore_not_ignored_is_noop(self):
        # No .gitignore at all -> nothing to fix.
        res = self._adopt(apply=True)
        self.assertFalse(res["gitignore"]["was_ignored"])
        self.assertEqual(res["gitignore"]["added"], [])

    def test_cli_dry_run_flag_accepted_and_is_noop(self):
        # The literal `adopt --dry-run --repo .` form (per the docs/notice) must
        # parse and behave as the read-only default — no writes.
        import os as _os
        import subprocess
        self._seed_queue("followup", "x.md", "# X\n")
        env = dict(_os.environ)
        env["BUILD_LOOP_MEMORY_DIR"] = str(self.mem)
        out = subprocess.run(
            ["python3", str(_BACKLOG_PY), "adopt", "--dry-run",
             "--repo", str(self.repo), "--today", "2026-06-16"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        import json as _json
        rep = _json.loads(out.stdout)
        self.assertEqual(rep["mode"], "dry-run")
        self.assertFalse(bl.items_dir(self.repo).exists())

    def test_scaffold_creates_pointer_and_dirs(self):
        res = self._adopt(apply=True)
        self.assertTrue(bl.items_dir(self.repo).is_dir())
        self.assertTrue(bl.archive_dir(self.repo).is_dir())
        self.assertTrue((self.repo / "BACKLOG.md").exists())
        self.assertTrue((bl.backlog_root(self.repo) / "README.md").exists())
        self.assertTrue((bl.backlog_root(self.repo) / "INDEX.md").exists())  # sync ran


class TestDistributedCollision(_Base):
    """THE reproduction: two INDEPENDENT repo dirs (different worktrees/clones,
    e.g. Claude on branch A, Codex on branch B) each create items in the SAME
    area. With the retired sequential counter both minted `...-001/002/003` for
    DIFFERENT items, so a `git merge` of the two trees collided filenames/IDs and
    lost or conflicted items. The token scheme must make the two ID sets DISJOINT
    with zero coordination.
    """

    def _new_in(self, repo: Path, area: str, title: str) -> dict:
        ns = _NS(repo=str(repo), area=area, type="debt", title=title,
                 priority="P2", status="open", gated="none", entities="",
                 evidence="", provenance_source="", provenance_ref="",
                 owner="", context="", notes="", review_days=30,
                 today="2026-06-16")
        return bl.cmd_new(ns)

    def test_two_independent_repos_zero_id_overlap(self):
        # Same slug on BOTH (identical basename) so the PREFIX-AREA part is
        # identical — the worst case where the old counter guaranteed collision.
        repo_a = Path(self._tmp.name) / "wt-a" / "myproj-app"
        repo_b = Path(self._tmp.name) / "wt-b" / "myproj-app"
        repo_a.mkdir(parents=True)
        repo_b.mkdir(parents=True)

        ids_a = {self._new_in(repo_a, "search", f"a{i}")["id"] for i in range(3)}
        ids_b = {self._new_in(repo_b, "search", f"b{i}")["id"] for i in range(3)}

        # Each side minted 3 distinct items.
        self.assertEqual(len(ids_a), 3)
        self.assertEqual(len(ids_b), 3)
        # The reproduction assertion: ZERO overlap across the two repos.
        overlap = ids_a & ids_b
        self.assertEqual(overlap, set(),
                         f"distributed ID collision (the bug): {overlap}")
        # Filenames are therefore disjoint too -> a merge of items/ from both
        # trees lands 6 distinct files, nothing clobbered.
        names_a = {p.name for p in bl.items_dir(repo_a).glob("*.md")}
        names_b = {p.name for p in bl.items_dir(repo_b).glob("*.md")}
        self.assertEqual(names_a & names_b, set(),
                         "item filenames must not overlap across worktrees")

    def test_simulated_merge_of_two_worktrees_no_loss(self):
        # Concretely simulate the post-merge state: copy both trees' items into
        # one dir (what `git merge` does for non-conflicting paths). All 6 must
        # coexist; a sync over the merged tree reports 6 active items.
        import shutil
        repo_a = Path(self._tmp.name) / "wt-a" / "myproj-app"
        repo_b = Path(self._tmp.name) / "wt-b" / "myproj-app"
        repo_a.mkdir(parents=True)
        repo_b.mkdir(parents=True)
        for i in range(3):
            self._new_in(repo_a, "search", f"a{i}")
            self._new_in(repo_b, "search", f"b{i}")

        merged = Path(self._tmp.name) / "merged" / "myproj-app"
        bl.items_dir(merged).mkdir(parents=True)
        for src in (repo_a, repo_b):
            for p in bl.items_dir(src).glob("*.md"):
                shutil.copy2(p, bl.items_dir(merged) / p.name)

        merged_items = bl.load_items(merged)
        self.assertEqual(len(merged_items), 6, "merge must preserve all 6 items")
        # No duplicate IDs in the merged tree.
        idx = bl.render_index(merged, merged_items, "2026-06-16")
        self.assertNotIn("Duplicate IDs", idx)


class TestLegacyIdBackCompat(_Base):
    """Back-compat: legacy `-NNN` items (the 8 seeded sample-app items + anything
    already created before the token switch) must still read/list/sync. Parsing
    must not assume a numeric suffix; legacy and token items must coexist.
    """

    def _seed_legacy(self, item_id: str, title: str, status: str = "open",
                     area: str = "search") -> Path:
        bl.items_dir(self.repo).mkdir(parents=True, exist_ok=True)
        text = (
            "---\n"
            f"id: {item_id}\n"
            "schema_version: 1\n"
            f"title: {title}\n"
            f"status: {status}\n"
            "priority: P2\n"
            "type: debt\n"
            f"area: {area}\n"
            "gated: none\n"
            "provenance: {}\n"
            "created: 2026-05-01\n"
            "updated: 2026-05-01\n"
            "review_by: 2026-06-01\n"
            "owner: unassigned\n"
            "---\n\n## Context\nlegacy seed\n"
        )
        p = bl.items_dir(self.repo) / f"{item_id}.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_legacy_id_matches_item_id_regex(self):
        # The membership regex must still accept the legacy -NNN shape.
        self.assertRegex("ATOM-SEARCH-001", bl._ITEM_ID_RE)
        self.assertRegex("ATOM-SEARCH-008", bl._ITEM_ID_RE)

    def test_legacy_items_read_and_list(self):
        self._seed_legacy("ATOM-SEARCH-001", "legacy one")
        self._seed_legacy("ATOM-SEARCH-002", "legacy two", status="blocked")
        res = bl.cmd_list(_NS(repo=str(self.repo), status="", area="",
                              priority="", include_archive=False))
        ids = {r["id"] for r in res["items"]}
        self.assertIn("ATOM-SEARCH-001", ids)
        self.assertIn("ATOM-SEARCH-002", ids)

    def test_legacy_and_token_coexist_in_sync(self):
        # Mix: 2 legacy + 2 freshly-minted token items. sync must consolidate,
        # render INDEX, and mirror all four without choking on either shape.
        self._seed_legacy("ATOM-SEARCH-001", "legacy one")
        self._seed_legacy("ATOM-SEARCH-002", "legacy two")
        self._new(area="search", title="token one")
        self._new(area="search", title="token two")
        res = self._sync()
        self.assertEqual(res["index"]["active_count"], 4)
        idx = Path(res["index"]["path"]).read_text(encoding="utf-8")
        self.assertIn("ATOM-SEARCH-001", idx)
        self.assertIn("token one", idx)
        # Mirror copied all four (legacy IDs are item-ID-shaped, so not pruned).
        self.assertGreaterEqual(res["mirror"]["written"], 4)

    def test_legacy_done_item_archives(self):
        # A legacy item moving to done must still consolidate to archive/.
        self._seed_legacy("ATOM-SEARCH-003", "finishing legacy", status="done")
        self._sync()
        self.assertTrue((bl.archive_dir(self.repo) / "ATOM-SEARCH-003.md").exists())
        self.assertFalse((bl.items_dir(self.repo) / "ATOM-SEARCH-003.md").exists())

    def test_legacy_mirror_prune_still_targets_legacy_shape(self):
        # Under the opt-in --prune path, an orphaned LEGACY mirror file must
        # still be pruned (the prune regex must accept -NNN). A default sync
        # never prunes (merge-by-id, BUIL-TOOLING-syncsafety-001).
        self._seed_legacy("ATOM-SEARCH-004", "will orphan")
        res = self._sync()
        mem_dir = Path(res["mirror"]["dir"])
        self.assertTrue((mem_dir / "ATOM-SEARCH-004.md").exists())
        (bl.items_dir(self.repo) / "ATOM-SEARCH-004.md").unlink()
        self._sync(prune=True)
        self.assertFalse((mem_dir / "ATOM-SEARCH-004.md").exists(),
                         "orphaned legacy mirror must be pruned under --prune")


class TestDuplicateIdDetection(_Base):
    """Defense in depth: if two items ever share an id (a bad merge, a hand-edit),
    sync must surface it LOUDLY in the INDEX rather than silently rendering one.
    """

    def _seed(self, item_id: str, title: str, name: str) -> Path:
        bl.items_dir(self.repo).mkdir(parents=True, exist_ok=True)
        text = (
            "---\n"
            f"id: {item_id}\n"
            f"title: {title}\n"
            "status: open\n"
            "priority: P2\n"
            "type: debt\n"
            "area: search\n"
            "gated: none\n"
            "created: 2026-06-16\n"
            "updated: 2026-06-16\n"
            "review_by: 2026-07-16\n"
            "---\n\n## Context\nx\n"
        )
        p = bl.items_dir(self.repo) / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_duplicate_ids_surface_in_index(self):
        # Two DIFFERENT files carrying the SAME id (what a merge collision looks
        # like once both sides minted the same legacy -NNN).
        self._seed("ATOM-SEARCH-001", "first claim", "ATOM-SEARCH-001.md")
        self._seed("ATOM-SEARCH-001", "second claim", "ATOM-SEARCH-001-dup.md")
        res = self._sync()
        idx = Path(res["index"]["path"]).read_text(encoding="utf-8")
        self.assertIn("Duplicate IDs", idx)
        self.assertIn("ATOM-SEARCH-001", idx)
        # BOTH titles are surfaced — neither is silently hidden.
        self.assertIn("first claim", idx)
        self.assertIn("second claim", idx)

    def test_no_duplicate_section_when_clean(self):
        self._new(area="search", title="solo")
        res = self._sync()
        idx = Path(res["index"]["path"]).read_text(encoding="utf-8")
        self.assertNotIn("Duplicate IDs", idx)

    def test_duplicate_helper_groups_across_archive(self):
        # A collision visible even after one side archived.
        active = self._seed("ATOM-SEARCH-002", "active dup", "ATOM-SEARCH-002.md")
        bl.archive_dir(self.repo).mkdir(parents=True, exist_ok=True)
        (bl.archive_dir(self.repo) / "ATOM-SEARCH-002.md").write_text(
            active.read_text(encoding="utf-8"), encoding="utf-8")
        items = bl.load_items(self.repo, include_archive=True)
        groups = bl._duplicate_id_groups(items)
        self.assertIn("ATOM-SEARCH-002", groups)
        self.assertEqual(len(groups["ATOM-SEARCH-002"]), 2)


class TestTokenUniquenessAtVolume(unittest.TestCase):
    """Token uniqueness at volume — mint many IDs in-process, assert all unique."""

    def test_1000_tokens_all_unique(self):
        tokens = {bl.mint_id_token() for _ in range(1000)}
        self.assertEqual(len(tokens), 1000, "all 1000 minted tokens must be unique")

    def test_token_is_lowercase_crockford(self):
        tok = bl.mint_id_token()
        self.assertRegex(tok, r"^[0-9a-z]+$")
        # No ambiguous Crockford-excluded letters (i, l, o, u).
        for bad in ("i", "l", "o", "u"):
            self.assertNotIn(bad, tok)

    def test_token_entropy_uses_urandom_not_clock(self):
        # Same millisecond, many mints -> the random tail must vary (otherwise
        # the ID would be clock-only and collide). Pin now_ms; the tails differ.
        fixed = 1_700_000_000_000
        tails = {bl.mint_id_token(now_ms=fixed)[bl._TOKEN_TIME_WIDTH:]
                 for _ in range(200)}
        # With 25 random bits, 200 draws are essentially always distinct.
        self.assertGreater(len(tails), 190,
                           "random tail must vary within one millisecond")


class TestGitattributesScaffold(_Base):
    """INDEX.md merge handling — the .gitattributes rule is scaffolded by both
    the `adopt` scaffold path AND any backlog materialisation (new/sync)."""

    def test_new_drops_gitattributes(self):
        self._new(area="search", title="x")
        ga = bl.backlog_root(self.repo) / ".gitattributes"
        self.assertTrue(ga.exists(), "ensure_dirs must drop the INDEX merge rule")
        self.assertIn("INDEX.md merge=ours", ga.read_text(encoding="utf-8"))

    def test_adopt_scaffolds_gitattributes(self):
        ns = _NS(repo=str(self.repo), apply=True, today="2026-06-16",
                 review_days=30, no_mirror=True, slug="")
        bl.cmd_adopt(ns)
        ga = bl.backlog_root(self.repo) / ".gitattributes"
        self.assertTrue(ga.exists())
        self.assertIn("INDEX.md merge=ours", ga.read_text(encoding="utf-8"))

    def test_gitattributes_not_overwritten_if_customised(self):
        # ensure_dirs writes once; a user customisation survives a later sync.
        self._new(area="search", title="x")
        ga = bl.backlog_root(self.repo) / ".gitattributes"
        ga.write_text("INDEX.md merge=ours\n# my custom note\n", encoding="utf-8")
        self._sync()
        self.assertIn("my custom note", ga.read_text(encoding="utf-8"))


class TestNoThirdPartyImports(unittest.TestCase):
    """Assert backlog.py imports ONLY Python stdlib — the host-agnostic contract."""

    # Python 3.10+ stdlib top-level module allowlist used by backlog.py.
    _STDLIB = {
        "__future__", "argparse", "datetime", "json", "os", "re",
        "secrets", "sys", "time", "pathlib", "typing",
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


class TestSyncSafety(_Base):
    """Acceptance tests for BUIL-TOOLING-syncsafety-001 — sync is non-destructive
    and `--repo X` from inside X does not doubly-nest the store."""

    def test_sync_from_partial_store_leaves_mirror_item_intact(self):
        # Acceptance (a): a sync from a local store MISSING item K leaves K
        # intact in the durable mirror — file AND INDEX row.
        k = self._new(area="selfmod", title="keep me")
        kid = k["id"]
        res = self._sync()
        mem_dir = Path(res["mirror"]["dir"])
        k_mirror = mem_dir / f"{kid}.md"
        self.assertTrue(k_mirror.exists(), "precondition: K mirrored")

        # Simulate the bug's partial/mis-rooted local store: K is gone locally,
        # a DIFFERENT item exists. A default sync must merge, not overwrite.
        (bl.items_dir(self.repo) / f"{kid}.md").unlink()
        j = self._new(area="other", title="partial-store item")
        jid = j["id"]
        res2 = self._sync()

        self.assertTrue(k_mirror.exists(),
                        "K must survive a sync from a store that lacks it")
        index_text = (mem_dir / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn(kid, index_text,
                      "mirror INDEX must still list the mirror-only item K")
        self.assertTrue((mem_dir / f"{jid}.md").exists(),
                        "the new local item must also be mirrored")
        self.assertIn(jid, index_text)

    def test_new_repo_named_from_inside_itself_writes_to_dot_build_loop(self):
        # Acceptance (b): `new --repo <name>` run from inside repo <name> writes
        # to ./.build-loop/backlog/, NOT ./<name>/.build-loop/backlog/.
        import os
        name = self.repo.name  # "myproj-app"
        old_cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            ns = _NS(repo=name, area="search", type="debt", title="x",
                     priority="P2", status="open", gated="none", entities="",
                     evidence="", provenance_source="", provenance_ref="",
                     owner="", context="", notes="", review_days=30,
                     today="2026-06-16")
            res = bl.cmd_new(ns)
        finally:
            os.chdir(old_cwd)
        item_path = Path(res["path"]).resolve()
        good_root = (self.repo / ".build-loop" / "backlog").resolve()
        nested_root = (self.repo / name / ".build-loop").resolve()
        self.assertTrue(str(item_path).startswith(str(good_root)),
                        f"item must land under {good_root}, got {item_path}")
        self.assertFalse(nested_root.exists(),
                         f"must NOT create doubly-nested store at {nested_root}")


class TestNormalizeRepo(unittest.TestCase):
    """Unit tests for normalize_repo path resolution (no fs writes)."""

    def test_existing_dir_honoured_literally(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(bl.normalize_repo(d), Path(d))

    def test_dot_resolves_to_cwd_dir(self):
        # "." is an existing dir -> returned as-is.
        self.assertEqual(bl.normalize_repo("."), Path("."))

    def test_relative_name_matching_cwd_basename_resolves_to_cwd(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "build-loop"
            repo.mkdir()
            old = os.getcwd()
            os.chdir(repo)
            try:
                # No nested ./build-loop child -> name matches cwd basename.
                self.assertEqual(bl.normalize_repo("build-loop").resolve(),
                                 repo.resolve())
            finally:
                os.chdir(old)

    def test_unrelated_relative_name_stays_literal(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            old = os.getcwd()
            os.chdir(d)
            try:
                self.assertEqual(bl.normalize_repo("new-thing"), Path("new-thing"))
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
