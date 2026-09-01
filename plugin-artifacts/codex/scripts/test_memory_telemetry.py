# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/memory_telemetry.py.

Covers:
    - schema_version is "1.1" on every row
    - kind enum: memory-read | memory-write | memory-effect | memory-use
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
import os
import re
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import memory_telemetry as mt  # noqa: E402


class SchemaVersionTests(unittest.TestCase):
    def test_module_constant_is_1_1(self):
        self.assertEqual(mt.SCHEMA_VERSION, "1.1")

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
            self.assertEqual(rows[0]["schema_version"], "1.1")

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
            self.assertEqual(rows[0]["schema_version"], "1.1")


class KindEnumTests(unittest.TestCase):
    def test_known_kinds_exhaustive(self):
        self.assertEqual(
            mt.VALID_KINDS, {"memory-read", "memory-write", "memory-effect", "memory-use"}
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

    def test_append_does_not_read_existing_file(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            path.write_text('{"existing":true}\n', encoding="utf-8")
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("rewrite path")):
                mt.emit_read(
                    phase="p", reader="r", query="q",
                    memory_ids_seen=["a"], telemetry_path=path,
                )
            self.assertEqual(len(mt.read_rows(path)), 2)

    def test_concurrent_appends_preserve_every_row(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"

            def emit(index: int) -> None:
                mt.emit_read(
                    phase="stress",
                    reader=f"r{index % 8}",
                    query="q",
                    memory_ids_seen=[str(index)],
                    telemetry_path=path,
                    source="test",
                )

            with ThreadPoolExecutor(max_workers=12) as pool:
                list(pool.map(emit, range(100)))
            rows = mt.read_rows(path)
            self.assertEqual(len(rows), 100)
            self.assertEqual(len({row["memory_ids_seen"][0] for row in rows}), 100)


class SourceSeparationTests(unittest.TestCase):
    def test_pid_default_test_stream_creates_its_parent(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td, \
                mock.patch.object(mt.tempfile, "gettempdir", return_value=td), \
                mock.patch.dict(os.environ, {"BUILD_LOOP_TELEMETRY_SOURCE": "test"}, clear=False):
            os.environ.pop("BUILD_LOOP_TEST_TELEMETRY_PATH", None)
            expected = mt.default_telemetry_path()
            mt.emit_read(phase="p", reader="r", query="q", memory_ids_seen=[])
            self.assertTrue(expected.is_file())
            self.assertEqual(mt.read_rows(expected)[0]["source"], "test")

    def test_explicit_test_source_uses_isolated_default_path(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            test_path = Path(td) / "test-telemetry.jsonl"
            with mock.patch.dict(
                os.environ,
                {
                    "BUILD_LOOP_TELEMETRY_SOURCE": "test",
                    "BUILD_LOOP_TEST_TELEMETRY_PATH": str(test_path),
                },
                clear=False,
            ):
                mt.emit_read(
                    phase="p", reader="r", query="q", memory_ids_seen=[]
                )
            rows = mt.read_rows(test_path)
            self.assertEqual(rows[0]["source"], "test")
            self.assertFalse(mt.DEFAULT_TELEMETRY_PATH == test_path)

    def test_retrieval_fields_are_recorded(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            mt.emit_read(
                phase="1-assess",
                reader="memory_locator",
                query="hook timeout",
                memory_ids_seen=["lesson-hook"],
                telemetry_path=path,
                source="interactive",
                engine="index-jsonl",
                returned_paths=["lessons/hook.md"],
                latency_ms=3.5,
                zero_result=False,
            )
            row = mt.read_rows(path)[0]
            self.assertEqual(row["source"], "interactive")
            self.assertEqual(row["engine"], "index-jsonl")
            self.assertEqual(row["returned_paths"], ["lessons/hook.md"])
            self.assertEqual(row["latency_ms"], 3.5)
            self.assertFalse(row["zero_result"])

    def test_use_event_records_files_actually_read(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            mt.emit_use(
                correlation_id="mt-receipt",
                memory_ids_used=["lesson-hook"],
                files_read=["lessons/hook.md"],
                effect="added_check",
                telemetry_path=path,
                source="runtime",
            )
            row = mt.read_rows(path)[0]
            self.assertEqual(row["kind"], "memory-use")
            self.assertEqual(row["correlation_id"], "mt-receipt")
            self.assertEqual(row["files_read"], ["lessons/hook.md"])
            self.assertEqual(row["effect"], "added_check")


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
                imports_memory_index = re.search(r"^(import|from)\s+memory_index\b", stripped)
                self.assertIsNone(
                    imports_memory_index,
                    f"telemetry module must not import memory_index; offending line: {line!r}",
                )


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
    def test_non_string_ids_are_serialized_without_warning(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            path = Path(td) / "TELEMETRY.jsonl"
            seen = uuid.uuid4()
            mt.emit_read(
                phase="p", reader="r", query="q",
                memory_ids_seen=[seen], telemetry_path=path,
            )
            rows = mt.read_rows(path)
            self.assertEqual(rows[0]["memory_ids_seen"], [str(seen)])

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


class ExposureFieldTests(unittest.TestCase):
    """emit_read must persist the exposure record when given one, and stay
    backward-compatible when not."""

    def _emit(self, path, **kw):
        return mt.emit_read(phase="p", reader="r", query="q",
                            memory_ids_seen=["a-long-id-1", "a-long-id-2"],
                            telemetry_path=path, source="test", **kw)

    def test_exposure_fields_persisted(self):
        import tempfile, json as _json
        from pathlib import Path as _P
        with tempfile.TemporaryDirectory() as d:
            p = _P(d) / "t.jsonl"
            self._emit(p, ranks=[0, 1], scores=[0.9, 0.4], shown_count=2,
                       session_id="sess-abc")
            row = _json.loads(p.read_text().strip())
            self.assertEqual(row["ranks"], [0, 1])
            self.assertEqual(row["scores"], [0.9, 0.4])
            self.assertEqual(row["shown_count"], 2)
            self.assertEqual(row["session_id"], "sess-abc")

    def test_exposure_fields_absent_when_not_supplied(self):
        """Backward compatibility: old callers must not gain empty fields."""
        import tempfile, json as _json, os
        from pathlib import Path as _P
        with tempfile.TemporaryDirectory() as d:
            p = _P(d) / "t.jsonl"
            prev = {k: os.environ.pop(k, None) for k in mt.SESSION_ENV_VARS}
            try:
                self._emit(p)
            finally:
                for k, v in prev.items():
                    if v is not None:
                        os.environ[k] = v
            row = _json.loads(p.read_text().strip())
            for k in ("ranks", "scores", "shown_count"):
                self.assertNotIn(k, row)

    def test_session_id_is_resolved_from_the_environment(self):
        """The caller should not have to know its own session id -- the runtime
        already exports it, and the trace hook records the SAME value."""
        import tempfile, json as _json, os
        from pathlib import Path as _P
        with tempfile.TemporaryDirectory() as d:
            p = _P(d) / "t.jsonl"
            prev = os.environ.get("BUILD_LOOP_SESSION_ID")
            os.environ["BUILD_LOOP_SESSION_ID"] = "sess-from-env"
            try:
                self._emit(p)
            finally:
                if prev is None:
                    os.environ.pop("BUILD_LOOP_SESSION_ID", None)
                else:
                    os.environ["BUILD_LOOP_SESSION_ID"] = prev
            self.assertEqual(_json.loads(p.read_text().strip())["session_id"],
                             "sess-from-env")

    def test_session_id_absent_when_nothing_identifies_the_session(self):
        """Never invent one. A wrong session id credits one agent's activity to
        another, which is the exact failure the field exists to prevent."""
        import tempfile, json as _json, os
        from pathlib import Path as _P
        with tempfile.TemporaryDirectory() as d:
            p = _P(d) / "t.jsonl"
            prev = {k: os.environ.pop(k, None) for k in mt.SESSION_ENV_VARS}
            try:
                self._emit(p)
            finally:
                for k, v in prev.items():
                    if v is not None:
                        os.environ[k] = v
            self.assertNotIn("session_id", _json.loads(p.read_text().strip()))

    def test_explicit_session_id_wins_over_environment(self):
        import tempfile, json as _json, os
        from pathlib import Path as _P
        with tempfile.TemporaryDirectory() as d:
            p = _P(d) / "t.jsonl"
            prev = os.environ.get("BUILD_LOOP_SESSION_ID")
            os.environ["BUILD_LOOP_SESSION_ID"] = "from-env"
            try:
                self._emit(p, session_id="explicit")
            finally:
                if prev is None:
                    os.environ.pop("BUILD_LOOP_SESSION_ID", None)
                else:
                    os.environ["BUILD_LOOP_SESSION_ID"] = prev
            self.assertEqual(_json.loads(p.read_text().strip())["session_id"],
                             "explicit")


if __name__ == "__main__":
    unittest.main()
