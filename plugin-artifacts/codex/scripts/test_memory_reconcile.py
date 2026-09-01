#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_reconcile.py.

Planted cases, not smoke tests. The two properties that matter most: the join
must survive the path-form mismatch that zeroed it on the first real run, and it
must never assert that an opened memory was useful.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import memory_reconcile as mrc  # noqa: E402


def span(tool, preview, *, session="s1", nanos=2_000_000_000):
    return {"end_time_unix_nano": nanos,
            "attributes": {"gen_ai.tool.name": tool,
                           "session.id": session,
                           "gen_ai.tool.call.arguments.preview":
                               preview if isinstance(preview, str) else json.dumps(preview)}}


def read(paths, ids=None, *, ts="2026-08-31T00:00:01Z", session=None, ranks=None, scores=None):
    return {"kind": "memory-read", "schema_version": "1.1", "source": "runtime",
            "correlation_id": "mt-test", "ts": ts, "query": "q",
            "reader_or_writer": "memory_facade.recall",
            "memory_ids_seen": ids or [f"id-{i}" for i in range(len(paths))],
            "returned_paths": paths,
            "ranks": ranks or list(range(len(paths))),
            "scores": scores or [0.9] * len(paths),
            **({"session_id": session} if session else {})}


class ExtractorTest(unittest.TestCase):
    def test_read_tool_names_its_file(self):
        s = span("Read", {"file_path": "/a/b.md"})
        self.assertEqual(mrc._EXTRACTORS["Read"](s["attributes"]), ["/a/b.md"])

    def test_bash_paths_come_from_the_command_text(self):
        """The coverage that makes shell-reading runtimes visible at all."""
        s = span("Bash", {"command": "sed -n '1,40p' /x/y/note.md | grep foo"})
        self.assertIn("/x/y/note.md", mrc._EXTRACTORS["Bash"](s["attributes"]))

    def test_shell_extractor_ignores_bare_directories(self):
        s = span("Bash", {"command": "cd /usr/local/bin && ls"})
        self.assertEqual(mrc._EXTRACTORS["Bash"](s["attributes"]), [])

    def test_grep_without_a_file_path_yields_nothing(self):
        s = span("Grep", {"pattern": "foo"})
        self.assertEqual(mrc._EXTRACTORS["Grep"](s["attributes"]), [])

    def test_truncated_preview_still_yields_paths(self):
        """Large calls truncate the preview, so JSON parsing fails by design."""
        s = span("Bash", '{"command":"cat /a/b/c.md and then some truncat')
        self.assertIn("/a/b/c.md", mrc._EXTRACTORS["Bash"](s["attributes"]))

    def test_registry_is_extensible(self):
        @mrc.extractor("MadeUpTool")
        def _fake(attrs):
            return ["/from/plugin.md"]
        try:
            self.assertIn("MadeUpTool", mrc._EXTRACTORS)
            self.assertEqual(mrc._EXTRACTORS["MadeUpTool"]({}), ["/from/plugin.md"])
        finally:
            mrc._EXTRACTORS.pop("MadeUpTool", None)

    def test_confidence_separates_named_from_inferred(self):
        self.assertEqual(mrc.Open("/a.md", "s", 1.0, "Read").confidence, "explicit")
        self.assertEqual(mrc.Open("/a.md", "s", 1.0, "Bash").confidence, "inferred")


class NormalizeTest(unittest.TestCase):
    def test_relative_read_path_matches_absolute_open_path(self):
        """THE BUG THAT ZEROED THE FIRST RUN.

        247 read paths against 2,345 open paths intersected at ZERO, purely
        because backends emit store-relative paths and spans record absolute
        ones.
        """
        with tempfile.TemporaryDirectory() as d:
            store = Path(d)
            (store / "lessons").mkdir()
            f = store / "lessons" / "x.md"
            f.write_text("x")
            self.assertEqual(mrc.normalize_path("lessons/x.md", store),
                             mrc.normalize_path(str(f), store))

    def test_absolute_path_is_unchanged_in_meaning(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d)
            f = store / "a.md"
            f.write_text("x")
            self.assertEqual(mrc.normalize_path(str(f), store), str(f.resolve()))

    def test_empty_path_is_dropped(self):
        self.assertEqual(mrc.normalize_path("", Path("/tmp")), "")


class StrategyTest(unittest.TestCase):
    def _rd(self, **kw):
        return mrc.Read(correlation_id="c", ts=100.0, query="q", ids=["m"],
                        paths=["/a.md"], ranks=[0], scores=[0.5],
                        reader="r", source="runtime", **kw)

    def test_path_only_ignores_time_and_session(self):
        r = self._rd(session=None)
        o = mrc.Open("/a.md", "other", 1.0, "Read")   # BEFORE the read
        self.assertTrue(mrc._STRATEGIES["path"](r, o, 60))

    def test_window_rejects_an_open_before_the_read(self):
        r = self._rd(session=None)
        o = mrc.Open("/a.md", "s1", 50.0, "Read")
        self.assertFalse(mrc._STRATEGIES["path-window"](r, o, 60))

    def test_window_rejects_an_open_past_the_horizon(self):
        r = self._rd(session=None)
        o = mrc.Open("/a.md", "s1", 100_000.0, "Read")
        self.assertFalse(mrc._STRATEGIES["path-window"](r, o, 60))

    def test_window_accepts_an_open_inside_it(self):
        r = self._rd(session=None)
        o = mrc.Open("/a.md", "s1", 130.0, "Read")
        self.assertTrue(mrc._STRATEGIES["path-window"](r, o, 60))

    def test_session_strategy_refuses_rather_than_guesses(self):
        """Reads carry no session id yet. The strict join must yield nothing
        VISIBLY instead of silently falling back to a looser rule."""
        r = self._rd(session=None)
        o = mrc.Open("/a.md", "s1", 130.0, "Read")
        self.assertFalse(mrc._STRATEGIES["session-path-window"](r, o, 60))

    def test_session_strategy_rejects_a_cross_session_open(self):
        r = self._rd(session="s1")
        o = mrc.Open("/a.md", "s2", 130.0, "Read")
        self.assertFalse(mrc._STRATEGIES["session-path-window"](r, o, 60))

    def test_session_strategy_accepts_same_session(self):
        r = self._rd(session="s1")
        o = mrc.Open("/a.md", "s1", 130.0, "Read")
        self.assertTrue(mrc._STRATEGIES["session-path-window"](r, o, 60))


class ReconcileTest(unittest.TestCase):
    def _pair(self):
        rd = mrc.Read(correlation_id="c1", ts=100.0, query="q",
                      ids=["mem-a", "mem-b"], paths=["/a.md", "/b.md"],
                      ranks=[0, 1], scores=[0.9, 0.4], session=None,
                      reader="r", source="runtime")
        return rd

    def test_matches_carry_the_rank_they_were_shown_at(self):
        """Rank travels with the match or the signal cannot be debiased later."""
        rd = self._pair()
        opens = [mrc.Open("/b.md", "s1", 150.0, "Read")]
        ms = mrc.reconcile([rd], opens, strategy_name="path-window", window=600)
        self.assertEqual(len(ms), 1)
        mem_id, path, rank, score, op = ms[0].opened[0]
        self.assertEqual((mem_id, rank), ("mem-b", 1))
        self.assertEqual(score, 0.4)

    def test_unopened_memories_are_not_matched(self):
        rd = self._pair()
        ms = mrc.reconcile([rd], [mrc.Open("/b.md", "s1", 150.0, "Read")],
                           strategy_name="path-window", window=600)
        self.assertEqual([m[0] for m in ms[0].opened], ["mem-b"])

    def test_no_opens_produces_no_matches(self):
        self.assertEqual(mrc.reconcile([self._pair()], [], strategy_name="path"), [])

    def test_summary_reports_every_strategy(self):
        """Tightening the join must never be able to flatter the number."""
        s = mrc.summarize([self._pair()], [mrc.Open("/b.md", "s1", 150.0, "Read")], 600)
        self.assertEqual(set(s["by_strategy"]), set(mrc._STRATEGIES))


class EmitTest(unittest.TestCase):
    def test_emitted_row_never_asserts_an_effect(self):
        """An open proves INSPECTED, never USEFUL. The panel was unanimous."""
        rd = mrc.Read(correlation_id="c1", ts=100.0, query="q", ids=["mem-a"],
                      paths=["/a.md"], ranks=[3], scores=[0.7], session=None,
                      reader="r", source="runtime")
        ms = mrc.reconcile([rd], [mrc.Open("/a.md", "s1", 150.0, "Read")],
                           strategy_name="path-window", window=600)
        with tempfile.TemporaryDirectory() as d:
            tp = Path(d) / "t.jsonl"
            n = mrc.emit(ms, strategy_name="path-window", telemetry_path=tp, source="test")
            row = json.loads(tp.read_text().strip())
        self.assertEqual(n, 1)
        self.assertIsNone(row["effect"], "an open is not evidence of usefulness")
        self.assertEqual(row["memory_ids_used"], ["mem-a"])
        self.assertEqual(row["correlation_id"], "c1")
        self.assertIn("rank3", row["reason"])

    def test_reason_records_the_extractor_confidence(self):
        rd = mrc.Read(correlation_id="c1", ts=100.0, query="q", ids=["mem-a"],
                      paths=["/a.md"], ranks=[0], scores=[None], session=None,
                      reader="r", source="runtime")
        ms = mrc.reconcile([rd], [mrc.Open("/a.md", "s1", 150.0, "Bash")],
                           strategy_name="path-window", window=600)
        with tempfile.TemporaryDirectory() as d:
            tp = Path(d) / "t.jsonl"
            mrc.emit(ms, strategy_name="path-window", telemetry_path=tp, source="test")
            row = json.loads(tp.read_text().strip())
        self.assertIn("inferred", row["reason"])


class LoadTest(unittest.TestCase):
    def test_legacy_rows_excluded_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d)
            (store / "indexes").mkdir()
            p = store / "indexes" / "TELEMETRY.jsonl"
            legacy = read(["/a.md"]); legacy.pop("source"); legacy["schema_version"] = "1.0"
            p.write_text("\n".join(json.dumps(r) for r in (legacy, read(["/b.md"]))))
            self.assertEqual(len(mrc.load_reads(store)), 1)
            self.assertEqual(len(mrc.load_reads(store, runtime_only=False)), 2)

    def test_reads_without_paths_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d)
            (store / "indexes").mkdir()
            r = read([]); r["returned_paths"] = []
            (store / "indexes" / "TELEMETRY.jsonl").write_text(json.dumps(r))
            self.assertEqual(mrc.load_reads(store), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
