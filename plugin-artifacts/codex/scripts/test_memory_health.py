#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_health.py -- tier separation and loop-closure reporting.

The point of this tool is that it must NEVER blend legacy test-polluted rows
into a trustworthy rate. These tests plant a store where blending would produce
a visibly different (and wrong) number, then assert the tiers stay apart.
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

import memory_health as mh  # noqa: E402


def read_row(*, sv, source=None, seen=None, used=None, effect=None,
             ts="2026-08-25T00:00:00Z", reader="memory_facade.recall"):
    row = {"ts": ts, "kind": "memory-read", "schema_version": sv,
           "correlation_id": "mt-x", "phase": "p", "reader_or_writer": reader,
           "query": "q", "memory_ids_seen": seen or [],
           "memory_ids_used": used or [], "effect": effect, "reason": ""}
    if source is not None:
        row["source"] = source
    return row


class MemoryHealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name)
        (self.store / "indexes").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rows, lane="indexes"):
        p = self.store / lane / "TELEMETRY.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def _summary(self):
        return mh.summarize(mh.collect(self.store))

    def test_tier_classification(self):
        self.assertEqual(mh.tier_of({"schema_version": "1.0"}), "legacy")
        self.assertEqual(mh.tier_of({"schema_version": "1.1"}), "legacy",
                         "1.1 without a source field is still unfilterable")
        self.assertEqual(mh.tier_of({"schema_version": "1.1", "source": "runtime"}), "clean")
        self.assertEqual(mh.tier_of({"schema_version": "1.1", "source": "test"}), "non_runtime")

    def test_legacy_pollution_never_blends_into_the_clean_rate(self):
        """PLANTED: legacy is 0% hit, clean is 100%. A blend would read 9%."""
        self._write([read_row(sv="1.0", seen=[]) for _ in range(10)])
        self._write([read_row(sv="1.1", source="runtime", seen=["decision-abc-123456"])])
        s = self._summary()
        self.assertEqual(s["tiers"]["clean"]["hit_rate"], 1.0)
        self.assertEqual(s["tiers"]["legacy"]["hit_rate"], 0.0)
        self.assertTrue(s["tiers"]["clean"]["trustworthy"])
        self.assertFalse(s["tiers"]["legacy"]["trustworthy"])
        # the blended number (1/11 = 0.0909) must appear nowhere as a rate
        self.assertNotIn(0.0909, [t["hit_rate"] for t in s["tiers"].values()])

    def test_non_runtime_is_split_out_from_clean(self):
        self._write([read_row(sv="1.1", source="test", seen=["decision-abc-123456"])])
        self._write([read_row(sv="1.1", source="runtime", seen=[])])
        s = self._summary()
        self.assertEqual(s["tiers"]["non_runtime"]["reads"], 1)
        self.assertEqual(s["tiers"]["clean"]["hit_rate"], 0.0)

    def test_open_loop_is_reported_as_open(self):
        self._write([read_row(sv="1.1", source="runtime", seen=["decision-abc-123456"])])
        s = self._summary()
        self.assertFalse(s["loop"]["closed"])
        self.assertEqual(s["loop"]["closure_rate"], 0.0)
        self.assertIn("OPEN LOOP", mh.render(s))

    def test_closed_loop_is_reported_as_closed(self):
        """A use row must flip the verdict -- proves the detector is not stuck."""
        self._write([read_row(sv="1.1", source="runtime", seen=["decision-abc-123456"])])
        self._write([{"ts": "2026-08-26T00:00:00Z", "kind": "memory-use",
                      "schema_version": "1.1", "correlation_id": "mt-x",
                      "memory_ids_used": ["decision-abc-123456"], "files_read": [],
                      "effect": "informed_decision", "reason": "referenced in commit abc",
                      "source": "runtime"}])
        s = self._summary()
        self.assertTrue(s["loop"]["closed"])
        self.assertEqual(s["loop"]["use_rows"], 1)
        self.assertNotIn("OPEN LOOP", mh.render(s))

    def test_multiple_lanes_are_aggregated(self):
        self._write([read_row(sv="1.1", source="runtime", seen=["decision-abc-123456"])],
                    lane="indexes")
        self._write([read_row(sv="1.1", source="runtime", seen=["decision-def-123456"])],
                    lane="lessons")
        self.assertEqual(self._summary()["tiers"]["clean"]["reads"], 2)

    def test_empty_store_does_not_crash(self):
        s = self._summary()
        self.assertEqual(s["loop"]["reads_all_tiers"], 0)
        self.assertIsNone(s["loop"]["closure_rate"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
