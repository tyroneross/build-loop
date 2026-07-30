#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/load_current.py (P0 short-term working-context layer).

Runnable via ``python3 scripts/test_load_current.py`` (no pytest dependency)
per CLAUDE.md guardrails. Uses ``unittest.TestCase`` + ``__main__`` so the
discovered tests actually execute when invoked directly.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import load_current as lc  # noqa: E402


VALID_CURRENT_MD = """\
# Build Loop Working Context

- Updated: 2026-06-07T17:00:00+00:00
- Trigger: phase_transition
- Phase: assess
- Run: run-test-p0
- Build loop ID: bl-test
- Branch: main @ abc123

## Current Work

- Agent: orchestrator
- Chunk: c1
- Status: dispatching
- Task: implement P0 short-term context layer
- Next action: run validation

## Changed Files

- Dirty count: 3
- scripts/context_snapshot.py
- scripts/load_current.py
- scripts/test_load_current.py

## Validation

- Result: pending
- Commands recorded: 1

## Memory Backlinks

- decision: P1 hybrid retrieval picks MLX [build-loop] — `~/dev/git-folder/build-loop-memory/projects/build-loop/decisions/p1.md`
- lesson: build-loop memory closeout mandatory [build-loop] — `~/dev/git-folder/build-loop-memory/projects/build-loop/lessons/closeout.md`
- implementation: prior-art P4 digest source [sample-news] — `projects/sample-news/code/recall.py`

## Pointers

- Snapshot JSON: `.build-loop/context/snapshots/`
- Snapshot index: `.build-loop/context/index.json`
- Memory store: `~/dev/git-folder/build-loop-memory/projects/build-loop/`
- Prior art digest (full): `packet.prior_art.digest_text`
"""


class LoadCurrentTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)
        self.ctx_dir = self.workdir / ".build-loop" / "context"
        self.ctx_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, text: str) -> Path:
        path = self.ctx_dir / "current.md"
        path.write_text(text, encoding="utf-8")
        return path

    # ---- happy path: parse + measured latency + backlinks present ----
    def test_load_parses_valid_current_md_and_records_latency(self) -> None:
        self._write(VALID_CURRENT_MD)
        env = lc.load_current(self.workdir)
        self.assertTrue(env.exists)
        self.assertIsNotNone(env.warm_read_latency_ms)
        self.assertGreaterEqual(env.warm_read_latency_ms, 0.0)
        # Fast: a local FS read of < 4 KB MUST be under 50 ms on any reasonable host.
        self.assertLess(env.warm_read_latency_ms, 50.0, "warm read should be fast")

        parsed = env.parsed
        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["header"].get("phase"), "assess")
        self.assertEqual(parsed["header"].get("run"), "run-test-p0")
        self.assertEqual(parsed["current_work"].get("task"), "implement P0 short-term context layer")
        self.assertEqual(parsed["current_work"].get("next_action"), "run validation")

        # ≥1 long-term memory backlink — the core P0 req #6.
        self.assertGreaterEqual(len(parsed["links_down"]), 3)
        self.assertEqual(parsed["links_down"][0]["kind"], "decision")
        self.assertIn("build-loop", parsed["links_down"][0]["project"])
        self.assertTrue(parsed["links_down"][0]["path"])

        # Pointers section present + non-empty (progressive disclosure).
        self.assertGreaterEqual(len(parsed["pointers"]), 3)

    # ---- absence: returns clean envelope, never raises ----
    def test_load_returns_clean_envelope_when_current_md_missing(self) -> None:
        env = lc.load_current(self.workdir)
        self.assertFalse(env.exists)
        self.assertIsNone(env.warm_read_latency_ms)
        self.assertEqual(env.parsed, {})
        self.assertTrue(any("missing:" in r for r in env.reasons))

    # ---- corrupt UTF-8: returns degraded envelope, never raises ----
    def test_load_handles_corrupt_utf8_gracefully(self) -> None:
        path = self.ctx_dir / "current.md"
        path.write_bytes(b"\xff\xfe\xfd invalid utf-8 content")
        env = lc.load_current(self.workdir)
        # exists=True (file is there), latency=None (couldn't read).
        self.assertTrue(env.exists)
        self.assertIsNone(env.warm_read_latency_ms)
        self.assertTrue(any("decode_error" in r for r in env.reasons))

    # ---- unrecognized title: parsed.valid=False, but loader does not crash ----
    def test_load_handles_unrecognized_title(self) -> None:
        self._write("# Some Other Markdown\n\nHello world.\n")
        env = lc.load_current(self.workdir)
        self.assertTrue(env.exists)
        self.assertFalse(env.parsed.get("valid"))
        self.assertTrue(any("parse_error" in r for r in env.reasons))

    # ---- v1 marker (back-compat) parses too ----
    def test_load_accepts_v1_snapshot_marker(self) -> None:
        v1 = "# Build Loop Context Snapshot\n\n- Phase: report\n"
        self._write(v1)
        env = lc.load_current(self.workdir)
        self.assertTrue(env.parsed["valid"])
        self.assertEqual(env.parsed["header"].get("phase"), "report")

    # ---- ordering test: load_current runs WITHOUT importing/triggering bootstrap ----
    def test_load_does_not_import_or_run_bootstrap(self) -> None:
        # Module is loaded already (via import at top); the point of the
        # test is that calling `load_current` does no network/DB/recall
        # call. We assert this by snapshotting the modules dict and
        # confirming context_bootstrap is NOT pulled in as a side effect.
        before = set(sys.modules)
        self._write(VALID_CURRENT_MD)
        lc.load_current(self.workdir)
        added = set(sys.modules) - before
        # context_bootstrap is heavy (~1000 lines + memory_facade) — must
        # NOT be a side effect of loading the working context.
        self.assertNotIn("context_bootstrap", added,
                         "load_current must not trigger bootstrap import")
        # memory_facade is the P1 dense-recall tier; also must stay out.
        self.assertNotIn("memory_facade", added)
        # No prior-art lookup either.
        self.assertNotIn("prior_art", added)

    # ---- index.json reuse: surfaces last_write latency to the loader ----
    def test_load_surfaces_last_write_latency_from_index(self) -> None:
        self._write(VALID_CURRENT_MD)
        index_path = self.ctx_dir / "index.json"
        index_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "last_snapshot_id": "ctx-test-abc",
                    "warm_read_latency_ms": 0.42,
                    "pointer_density_findings": [],
                }
            ),
            encoding="utf-8",
        )
        env = lc.load_current(self.workdir)
        self.assertEqual(env.parsed["last_write_warm_read_ms"], 0.42)
        self.assertEqual(env.parsed["last_snapshot_id"], "ctx-test-abc")
        self.assertEqual(env.parsed["last_write_density_findings"], [])

    # ---- envelope is JSON-serializable (so it can go into the bootstrap packet) ----
    def test_envelope_is_json_serializable(self) -> None:
        self._write(VALID_CURRENT_MD)
        env = lc.load_current(self.workdir)
        as_dict = lc.envelope_to_dict(env)
        # Round-trip via json must succeed — that's the contract for
        # passing this envelope into downstream packets / agent briefs.
        json.dumps(as_dict)
        self.assertEqual(as_dict["path"], env.path)
        self.assertEqual(as_dict["exists"], True)

    # ---- warm read latency is BENCHMARKED, not asserted at a magic number ----
    def test_warm_read_latency_benchmark_is_recorded(self) -> None:
        self._write(VALID_CURRENT_MD)
        # Do 5 reads and report the mean — this is the "MEASURED, not asserted"
        # contract from the DoD. Test passes regardless of the number; we
        # store it on the test class so the runner can surface it.
        samples: list[float] = []
        for _ in range(5):
            env = lc.load_current(self.workdir)
            if env.warm_read_latency_ms is not None:
                samples.append(env.warm_read_latency_ms)
            time.sleep(0.001)
        mean_ms = sum(samples) / max(1, len(samples))
        # Surface the number for the test runner output (printed by --verbose).
        print(f"\n[bench] load_current warm read mean: {mean_ms:.4f} ms over {len(samples)} samples")
        self.assertGreaterEqual(len(samples), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
