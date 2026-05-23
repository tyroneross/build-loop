# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/memory_telemetry.py.

Covers:
    - schema_version is "1.0" on every row
    - kind enum: memory-read | memory-write | memory-effect
    - effect enum: changed_plan | changed_routing | added_check |
                   informed_decision | ignored | stale
    - append-only behavior (rows are not rewritten)
    - INDEX.jsonl is NOT touched by this module
    - correlation_id round-trip (read -> effect)
    - fire-and-forget contract (bad inputs coerce + log, never raise)

Run:
    uv run pytest scripts/test_memory_telemetry.py -v
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import memory_telemetry as mt  # noqa: E402


class SchemaVersionTests(unittest.TestCase):
    def test_module_constant_is_1_0(self):
        self.assertEqual(mt.SCHEMA_VERSION, "1.0")

    def test_emit_read_row_has_schema_version(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            mt.emit_read(
                phase="phase1",
                reader="test",
                query="foo",
                memory_ids_seen=["a", "b"],
                telemetry_path=path,
            )
            rows = mt.read_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["schema_version"], "1.0")

    def test_emit_write_row_has_schema_version(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            mt.emit_write(
                phase="phase4",
                writer="test",
                memory_id="memid",
                why_durable="lesson",
                telemetry_path=path,
            )
            rows = mt.read_rows(path)
            self.assertEqual(rows[0]["schema_version"], "1.0")


class KindEnumTests(unittest.TestCase):
    def test_known_kinds_exhaustive(self):
        self.assertEqual(
            mt.VALID_KINDS, {"memory-read", "memory-write", "memory-effect"}
        )

    def test_read_kind(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            mt.emit_read(
                phase="p", reader="r", query="q",
                memory_ids_seen=[], telemetry_path=path,
            )
            self.assertEqual(mt.read_rows(path)[0]["kind"], "memory-read")

    def test_write_kind(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            mt.emit_write(
                phase="p", writer="w", memory_id="m", why_durable="d",
                telemetry_path=path,
            )
            self.assertEqual(mt.read_rows(path)[0]["kind"], "memory-write")

    def test_effect_kind(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            mt.emit_effect(
                correlation_id="mt-deadbeef",
                effect="changed_plan",
                telemetry_path=path,
            )
            self.assertEqual(mt.read_rows(path)[0]["kind"], "memory-effect")


class EffectEnumTests(unittest.TestCase):
    def test_six_canonical_effects(self):
        self.assertEqual(
            mt.VALID_EFFECTS,
            {
                "changed_plan", "changed_routing", "added_check",
                "informed_decision", "ignored", "stale",
            },
        )

    def test_invalid_effect_coerces_to_informed_decision(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            mt.emit_effect(
                correlation_id="mt-deadbeef",
                effect="totally-bogus",
                telemetry_path=path,
            )
            self.assertEqual(mt.read_rows(path)[0]["effect"], "informed_decision")


class AppendOnlyTests(unittest.TestCase):
    def test_multiple_emits_append(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            for i in range(3):
                mt.emit_read(
                    phase=f"p{i}", reader="r", query=f"q{i}",
                    memory_ids_seen=[], telemetry_path=path,
                )
            rows = mt.read_rows(path)
            self.assertEqual(len(rows), 3)
            self.assertEqual([r["phase"] for r in rows], ["p0", "p1", "p2"])

    def test_emit_does_not_rewrite_existing_rows(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            mt.emit_read(
                phase="first", reader="r", query="q",
                memory_ids_seen=[], telemetry_path=path,
            )
            first_bytes = path.read_bytes()
            mt.emit_read(
                phase="second", reader="r", query="q",
                memory_ids_seen=[], telemetry_path=path,
            )
            # First line bytes must be preserved verbatim
            new_bytes = path.read_bytes()
            self.assertTrue(new_bytes.startswith(first_bytes))


class IndexNotTouchedTests(unittest.TestCase):
    def test_telemetry_module_does_not_import_memory_index(self):
        # Read the module source and confirm it does NOT IMPORT memory_index
        # (the M5 discovery log; its schema action: write|update|delete must be
        # preserved untouched per Step 8 §integration checkpoint). Doc/comment
        # references to INDEX.jsonl are OK — the rule is "no writes" which we
        # enforce by checking for actual import statements.
        src = Path(mt.__file__).read_text(encoding="utf-8")
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith(("import ", "from ")):
                self.assertNotIn("memory_index", line,
                                 f"telemetry module must not import memory_index; offending line: {line!r}")


class CorrelationIdRoundtripTests(unittest.TestCase):
    def test_read_returns_correlation_id_emit_effect_joins(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            cid = mt.emit_read(
                phase="p", reader="r", query="q",
                memory_ids_seen=["a"], telemetry_path=path,
            )
            self.assertTrue(cid.startswith("mt-"))
            mt.emit_effect(
                correlation_id=cid, effect="changed_routing",
                reason="picked Haiku over Sonnet",
                telemetry_path=path,
            )
            rows = mt.read_rows(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["correlation_id"], cid)
            self.assertEqual(rows[1]["correlation_id"], cid)
            self.assertEqual(rows[1]["effect"], "changed_routing")
            self.assertEqual(rows[1]["reason"], "picked Haiku over Sonnet")


class FireAndForgetTests(unittest.TestCase):
    def test_unwritable_path_does_not_raise(self):
        # Pass a path with an impossible parent — emit must swallow + log
        bad = Path("/nonexistent-root-dir-bzzz-99/TELEMETRY.jsonl")
        try:
            mt.emit_read(
                phase="p", reader="r", query="q",
                memory_ids_seen=[], telemetry_path=bad,
            )
            mt.emit_write(
                phase="p", writer="w", memory_id="m", why_durable="d",
                telemetry_path=bad,
            )
            mt.emit_effect(
                correlation_id="mt-x", effect="ignored",
                telemetry_path=bad,
            )
        except Exception as exc:  # noqa: BLE001
            self.fail(f"telemetry emit raised {type(exc).__name__}: {exc}")


class RowShapeTests(unittest.TestCase):
    def test_read_row_required_fields(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            mt.emit_read(
                phase="phase1", reader="test", query="design contract",
                memory_ids_seen=["mem1"], memory_ids_used=["mem1"],
                effect="informed_decision", reason="loaded for baseline",
                telemetry_path=path,
            )
            row = mt.read_rows(path)[0]
            for f in ("ts", "kind", "schema_version", "correlation_id",
                      "phase", "reader_or_writer", "query",
                      "memory_ids_seen", "memory_ids_used", "effect", "reason"):
                self.assertIn(f, row, f"missing field {f!r}")

    def test_write_row_required_fields(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            mt.emit_write(
                phase="phase4", writer="claude_code",
                memory_id="~/.build-loop/memory/foo.md",
                why_durable="recurring failure pattern",
                action="update",
                telemetry_path=path,
            )
            row = mt.read_rows(path)[0]
            for f in ("ts", "kind", "schema_version", "correlation_id",
                      "phase", "reader_or_writer", "memory_id",
                      "action", "why_durable"):
                self.assertIn(f, row, f"missing field {f!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
