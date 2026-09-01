#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""The join contract: every emit_read caller must emit the join key.

A read row without `returned_paths` can never be matched to a tool-trace span,
because the span records a PATH and carries no memory id. One caller omitting it
is not a partial signal, it is a silent hole -- and that is exactly what
happened: `memory_facade.recall` produced 39,987 of 41,935 rows and passed
nothing, so usefulness was unmeasurable while the instrumentation looked present.

This audits the callers STRUCTURALLY rather than by inspection, so a new caller
added later cannot quietly reintroduce the hole.
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Files that call emit_read as part of the production read path.
CALLER_FILES = (
    "memory_facade/__init__.py",
    "memory_locator.py",
    "context_bootstrap.py",
)


def emit_read_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name == "emit_read":
            out.append(node)
    return out


class CallerAuditTest(unittest.TestCase):
    """Enumerates callers from source. Adding a caller that omits the join key
    fails here, which is the point -- inspection does not scale to future code."""

    def test_every_caller_is_found(self):
        found = {f: len(emit_read_calls(HERE / f)) for f in CALLER_FILES}
        for f, n in found.items():
            self.assertGreaterEqual(n, 1, f"no emit_read call found in {f}")

    def test_every_caller_passes_returned_paths(self):
        offenders = []
        for f in CALLER_FILES:
            for call in emit_read_calls(HERE / f):
                kwargs = {kw.arg for kw in call.keywords if kw.arg}
                if "returned_paths" not in kwargs:
                    offenders.append(f)
        self.assertEqual(offenders, [],
                         "these callers emit a read with no join key, so their "
                         "rows can never be matched to a file open")

    def test_no_caller_asserts_an_effect_at_read_time(self):
        """`effect` at read time would claim an outcome before one is observed."""
        bad = []
        for f in CALLER_FILES:
            for call in emit_read_calls(HERE / f):
                for kw in call.keywords:
                    if kw.arg == "effect" and not (
                            isinstance(kw.value, ast.Constant) and kw.value.value is None):
                        bad.append(f)
        self.assertEqual(bad, [], "effect must not be asserted when a read is emitted")


class ContextBootstrapJoinTest(unittest.TestCase):
    def _packet(self):
        return {"query": "q", "lessons_progressive": [
            {"name": "a", "source_path": "/store/lessons/a.md"},
            {"name": "b", "source_path": "/store/lessons/b.md"},
        ]}

    def test_emits_paths_aligned_with_ids(self):
        import context_bootstrap as cb
        with tempfile.TemporaryDirectory() as d:
            tp = Path(d) / "t.jsonl"
            cid = cb.emit_read_telemetry(self._packet(), telemetry_path=tp)
            row = json.loads(tp.read_text().strip())
        self.assertTrue(cid)
        self.assertEqual(len(row["returned_paths"]), len(row["memory_ids_seen"]))
        self.assertIn("/store/lessons/a.md", row["returned_paths"])

    def test_dedup_keeps_ids_and_paths_aligned(self):
        """Dedup used to run on ids alone; doing that after adding paths would
        shear the two lists apart and mis-attribute every path."""
        import context_bootstrap as cb
        packet = {"query": "q", "lessons_progressive": [
            {"name": "a", "source_path": "/s/a.md"},
            {"name": "a", "source_path": "/s/a.md"},
            {"name": "b", "source_path": "/s/b.md"},
        ]}
        with tempfile.TemporaryDirectory() as d:
            tp = Path(d) / "t.jsonl"
            cb.emit_read_telemetry(packet, telemetry_path=tp)
            row = json.loads(tp.read_text().strip())
        self.assertEqual(row["memory_ids_seen"], ["/s/a.md", "/s/b.md"])
        self.assertEqual(row["returned_paths"], ["/s/a.md", "/s/b.md"])

    def test_lesson_without_a_source_path_does_not_shift_alignment(self):
        import context_bootstrap as cb
        packet = {"query": "q", "lessons_progressive": [
            {"name": "no-path"},
            {"name": "b", "source_path": "/s/b.md"},
        ]}
        with tempfile.TemporaryDirectory() as d:
            tp = Path(d) / "t.jsonl"
            cb.emit_read_telemetry(packet, telemetry_path=tp)
            row = json.loads(tp.read_text().strip())
        self.assertEqual(row["memory_ids_seen"], ["no-path", "/s/b.md"])
        self.assertEqual(row["returned_paths"], ["/s/b.md"])


class BacklogKindTest(unittest.TestCase):
    def test_backlog_rows_declare_their_lane(self):
        """Backlog was the only backend returning rows with `_kind` unset."""
        src = (HERE / "memory_facade" / "backlog.py").read_text(encoding="utf-8")
        self.assertIn('"_kind": "backlog"', src)


class StoreStatsTest(unittest.TestCase):
    def test_tier_taxonomy_matches_memory_health(self):
        """Two tools that disagree about 'clean' produce two irreconcilable
        numbers, which is the failure this whole script exists to prevent."""
        import memory_store_stats as mss
        import memory_health as mh
        for row in ({"schema_version": "1.0"},
                    {"schema_version": "1.1"},
                    {"schema_version": "1.1", "source": "runtime"},
                    {"schema_version": "1.1", "source": "test"}):
            self.assertEqual(mss.tier_of(row), mh.tier_of(row), row)

    def test_reports_both_churn_definitions(self):
        """mtime and commit answer different questions. Printing one silently is
        how an unreproducible number entered a design argument."""
        import memory_store_stats as mss
        with tempfile.TemporaryDirectory() as d:
            store = Path(d)
            (store / "a.md").write_text("x")
            c = mss.corpus_stats(store, "2026-01-01")
        self.assertIn("files_touched_by_mtime", c)
        self.assertIn("files_touched_by_commit", c)

    def test_rates_are_per_tier_never_blended(self):
        import memory_store_stats as mss
        with tempfile.TemporaryDirectory() as d:
            store = Path(d)
            (store / "indexes").mkdir()
            rows = [{"kind": "memory-read", "schema_version": "1.0",
                     "memory_ids_seen": []} for _ in range(9)]
            rows.append({"kind": "memory-read", "schema_version": "1.1",
                         "source": "runtime", "memory_ids_seen": ["x"],
                         "returned_paths": ["/x.md"]})
            (store / "indexes" / "TELEMETRY.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows))
            t = mss.telemetry_stats(store)
        self.assertEqual(t["tiers"]["clean"]["hit_rate"], 1.0)
        self.assertEqual(t["tiers"]["legacy"]["hit_rate"], 0.0)
        self.assertEqual(t["tiers"]["clean"]["joinable_rate"], 1.0)

    def test_empty_store_does_not_crash(self):
        import memory_store_stats as mss
        with tempfile.TemporaryDirectory() as d:
            s = mss.collect(Path(d), None, False)
        self.assertEqual(s["corpus"]["markdown_files"], 0)


class TargetCheckTest(unittest.TestCase):
    """Targets live in data so "are we on track" is a command, not an argument."""

    def _stats(self, hit=0.8, joinable=0.5, session=1.0, exposure=0.4, use=0):
        return {"telemetry": {"use_rows": use, "tiers": {"clean": {
            "hit_rate": hit, "joinable_rate": joinable,
            "session_rate": session, "exposure_rate": exposure}}}}

    def test_declared_targets_parse_and_cover_every_reported_metric(self):
        import json as _json
        import memory_store_stats as mss
        declared = _json.loads(mss.TARGETS_PATH.read_text())
        self.assertEqual(
            set(declared["metrics"]),
            {"hit_rate", "joinable_rate", "session_rate", "exposure_rate", "use_rows"})
        for name, spec in declared["metrics"].items():
            self.assertIn("target_rationale", spec, f"{name} target has no stated reason")
            self.assertIn("falsifier", spec, f"{name} has no falsifier")

    def test_relative_target_resolves_against_live_hit_rate(self):
        """joinable/exposure ceilings MOVE with retrieval quality: a zero-result
        read has no paths to carry, so hit_rate is the ceiling, not 1.0."""
        import memory_store_stats as mss
        r = mss.check_targets(self._stats(hit=0.8, joinable=0.8))
        self.assertEqual(r["metrics"]["joinable_rate"]["resolved_target"], 0.8)
        self.assertEqual(r["metrics"]["joinable_rate"]["status"], "on_target")

    def test_below_target_is_reported_not_hidden(self):
        import memory_store_stats as mss
        r = mss.check_targets(self._stats(hit=0.5))
        self.assertEqual(r["metrics"]["hit_rate"]["status"], "below")

    def test_use_rows_graded_on_being_nonzero(self):
        import memory_store_stats as mss
        self.assertEqual(
            mss.check_targets(self._stats(use=0))["metrics"]["use_rows"]["status"], "below")
        self.assertEqual(
            mss.check_targets(self._stats(use=3))["metrics"]["use_rows"]["status"], "on_target")

    def test_missing_targets_file_reports_rather_than_raises(self):
        import memory_store_stats as mss
        r = mss.check_targets(self._stats(), targets_path=Path("/nonexistent/t.json"))
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
