# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Unit + activation tests for the reference-capture capability."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import memory_writer as mw  # noqa: E402
from reference_capture import (  # noqa: E402
    capture_reference,
    classify_content_class,
    days_until_refresh,
    default_refresh_days,
    is_stale,
    scan_reference_lane,
)


class TestHorizons(unittest.TestCase):
    def test_fast_classes_short_horizon(self):
        self.assertEqual(default_refresh_days("api-docs"), 7)
        self.assertEqual(default_refresh_days("pricing"), 7)

    def test_slow_classes_long_horizon(self):
        self.assertEqual(default_refresh_days("ecosystem-survey"), 90)
        self.assertEqual(default_refresh_days("standard-spec"), 180)

    def test_unknown_class_falls_back_to_general(self):
        self.assertEqual(default_refresh_days("not-a-real-class"), default_refresh_days("general"))
        self.assertEqual(default_refresh_days(None), default_refresh_days("general"))

    def test_classify_priority_pricing_before_api(self):
        # "api pricing" should land on the faster-aging pricing class.
        self.assertEqual(classify_content_class("Stripe API pricing tiers"), "pricing")

    def test_classify_api_docs(self):
        self.assertEqual(classify_content_class("REST endpoint config keys"), "api-docs")

    def test_classify_survey(self):
        self.assertEqual(classify_content_class("landscape of vector databases"), "ecosystem-survey")

    def test_classify_default_general(self):
        self.assertEqual(classify_content_class("some unrelated topic"), "general")

    def test_classify_uses_source_urls(self):
        cls = classify_content_class("overview", source_urls=["https://docs.x.com/api/reference"])
        self.assertEqual(cls, "api-docs")


class TestStaleness(unittest.TestCase):
    def test_fresh_reference_not_stale(self):
        fm = {"retrieved_at": date.today().isoformat(), "refresh_after": 30}
        self.assertFalse(is_stale(fm))
        self.assertGreater(days_until_refresh(fm), 0)

    def test_backdated_reference_is_stale(self):
        old = (date.today() - timedelta(days=40)).isoformat()
        fm = {"retrieved_at": old, "refresh_after": 30}
        self.assertTrue(is_stale(fm))
        self.assertLessEqual(days_until_refresh(fm), 0)

    def test_exact_horizon_boundary_is_stale(self):
        # retrieved_at + refresh_after == today  → stale (<= 0).
        old = (date.today() - timedelta(days=30)).isoformat()
        fm = {"retrieved_at": old, "refresh_after": 30}
        self.assertTrue(is_stale(fm))
        self.assertEqual(days_until_refresh(fm), 0)

    def test_missing_dates_unknown_not_stale(self):
        self.assertFalse(is_stale({}))
        self.assertIsNone(days_until_refresh({}))
        self.assertFalse(is_stale({"retrieved_at": "2026-01-01"}))  # no horizon

    def test_datetime_string_retrieved_at(self):
        old = (date.today() - timedelta(days=40)).isoformat() + "T12:00:00Z"
        fm = {"retrieved_at": old, "refresh_after": 30}
        self.assertTrue(is_stale(fm))


class TestCaptureWritesViaCanonicalWriter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.lane = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_capture_writes_dated_reference_with_required_fields(self):
        result = capture_reference(
            workdir=self.lane,
            topic="claude-opus model card context window",
            findings="claude-opus-4-8 is the latest; 1M context variant exists.",
            source_urls=[{"url": "https://docs.anthropic.com/models", "tier": "T1"}],
            informed_decision="Chose opus-4-8 for the orchestrator tier.",
            run_id="run_test_1",
            memory_dir=self.lane,
            project="build-loop",
        )
        path = Path(result["path"])
        self.assertTrue(path.exists())
        # Filename is date-prefixed reference-* class.
        self.assertTrue(path.name.startswith(date.today().isoformat()))
        self.assertIn("-reference-", path.name)

        fm = result["frontmatter"]
        # Reference-specific temporal metadata present.
        self.assertEqual(fm["retrieved_at"], date.today().isoformat())
        self.assertIn("refresh_after", fm)
        self.assertEqual(fm["type"], "reference")
        self.assertEqual(fm["content_class"], "model-info")
        self.assertEqual(fm["refresh_after"], default_refresh_days("model-info"))
        self.assertEqual(fm["source_urls"][0]["tier"], "T1")
        self.assertIn("informed_decision", fm)
        # Canonical writer provenance came along for free.
        for field in mw.REQUIRED_PROVENANCE_FIELDS:
            self.assertIn(field, fm)

    def test_capture_appends_global_update_ledger(self):
        capture_reference(
            workdir=self.lane,
            topic="vercel cron limits",
            findings="x",
            run_id="run_test_2",
            memory_dir=self.lane,
            project="build-loop",
        )
        ledger = self.lane / "indexes" / "updates.jsonl"
        self.assertTrue(ledger.exists(), "memory_writer should have appended the global ledger")

    def test_explicit_horizon_override(self):
        result = capture_reference(
            workdir=self.lane,
            topic="some survey",
            findings="x",
            run_id="run_test_3",
            content_class="ecosystem-survey",
            refresh_after_days=5,
            memory_dir=self.lane,
            project="build-loop",
        )
        self.assertEqual(result["frontmatter"]["refresh_after"], 5)

    def test_body_contains_extracted_findings_and_temporal_prose(self):
        result = capture_reference(
            workdir=self.lane,
            topic="topic x",
            findings="EXTRACTED-FINDING-MARKER",
            source_urls=["https://example.com/doc"],
            run_id="run_test_4",
            memory_dir=self.lane,
            project="build-loop",
        )
        text = Path(result["path"]).read_text()
        self.assertIn("EXTRACTED-FINDING-MARKER", text)
        self.assertIn("Retrieved:", text)
        self.assertIn("refresh horizon", text)


class TestScanReferenceLane(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.lane = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_scan_flags_backdated_reference_as_stale(self):
        # Fresh reference.
        capture_reference(
            workdir=self.lane, topic="fresh one", findings="x",
            run_id="r_fresh", memory_dir=self.lane, project="build-loop",
        )
        # Backdated reference — retrieved 100 days ago with a 7-day api-docs horizon.
        old = (date.today() - timedelta(days=100)).isoformat()
        capture_reference(
            workdir=self.lane, topic="stale api docs", findings="x",
            source_urls=["https://docs.x/api"], retrieved_at=old,
            run_id="r_stale", memory_dir=self.lane, project="build-loop",
        )
        records = scan_reference_lane(self.lane)
        by_status = {r["status"] for r in records}
        self.assertIn("stale", by_status)
        self.assertIn("fresh", by_status)
        stale = [r for r in records if r["stale"]]
        self.assertEqual(len(stale), 1)
        self.assertIn("stale-api-docs", stale[0]["file"])

    def test_scan_empty_lane_returns_empty(self):
        self.assertEqual(scan_reference_lane(self.lane), [])

    def test_scan_missing_dir_returns_empty(self):
        self.assertEqual(scan_reference_lane(self.lane / "nope"), [])


if __name__ == "__main__":
    unittest.main()
