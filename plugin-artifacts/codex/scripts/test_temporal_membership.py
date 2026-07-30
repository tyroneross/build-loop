# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/temporal_membership — the shared run-membership preflight."""
from __future__ import annotations

import datetime as _dt
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import temporal_membership as tm  # noqa: E402


def _dt_utc(s: str) -> _dt.datetime:
    return tm.parse_ts(s)  # type: ignore[return-value]


class ParseTsTests(unittest.TestCase):
    def test_iso_z(self) -> None:
        d = tm.parse_ts("2026-07-10T08:37:46Z")
        self.assertIsNotNone(d)
        self.assertEqual(d.tzinfo, _dt.timezone.utc)
        self.assertEqual(d.year, 2026)

    def test_iso_offset(self) -> None:
        d = tm.parse_ts("2026-07-10T01:47:51-07:00")
        self.assertIsNotNone(d)
        # -07:00 01:47 == 08:47 UTC
        self.assertEqual(d.astimezone(_dt.timezone.utc).hour, 8)

    def test_compact_runid_form(self) -> None:
        d = tm.parse_ts("20260710T083746Z")
        self.assertIsNotNone(d)
        self.assertEqual((d.month, d.day, d.hour), (7, 10, 8))

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(tm.parse_ts("not-a-date"))
        self.assertIsNone(tm.parse_ts(""))
        self.assertIsNone(tm.parse_ts(None))
        self.assertIsNone(tm.parse_ts(1234))


class RunWindowTests(unittest.TestCase):
    def test_single_date_field(self) -> None:
        start, end = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        self.assertEqual(start, end)
        self.assertEqual(start.day, 10)

    def test_commit_ts_extends_end(self) -> None:
        start, end = tm.run_window(
            {"date": "2026-07-10T08:00:00Z"},
            commit_timestamps=["2026-07-10T12:00:00Z", "2026-07-10T09:00:00Z"],
        )
        self.assertEqual(start.hour, 8)
        self.assertEqual(end.hour, 12)

    def test_empty_run_open_window(self) -> None:
        self.assertEqual(tm.run_window({}), (None, None))
        self.assertEqual(tm.run_window(None), (None, None))


class IsMemberTests(unittest.TestCase):
    def test_same_day_record_is_member(self) -> None:
        rs = re = _dt_utc("2026-07-10T10:00:00Z")
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        ok, reason = tm.is_member(rs, re, ws, we)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")

    def test_three_week_stale_record_rejected(self) -> None:
        # The observed transcript span: 2026-06-12 .. 2026-06-20; run date 2026-07-10.
        rs = _dt_utc("2026-06-12T01:04:02Z")
        re = _dt_utc("2026-06-20T14:47:07Z")
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        ok, reason = tm.is_member(rs, re, ws, we)
        self.assertFalse(ok)
        self.assertIn("stale by", reason)

    def test_record_postdating_window_rejected(self) -> None:
        rs = re = _dt_utc("2026-08-15T00:00:00Z")
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        ok, reason = tm.is_member(rs, re, ws, we)
        self.assertFalse(ok)
        self.assertIn("postdates", reason)

    def test_host_mismatch_rejected(self) -> None:
        rs = re = _dt_utc("2026-07-10T10:00:00Z")
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        ok, reason = tm.is_member(rs, re, ws, we, record_host="claude_code", run_host="codex")
        self.assertFalse(ok)
        self.assertIn("host mismatch", reason)

    def test_host_match_ok(self) -> None:
        rs = re = _dt_utc("2026-07-10T10:00:00Z")
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        ok, _ = tm.is_member(rs, re, ws, we, record_host="claude_code", run_host="claude_code")
        self.assertTrue(ok)

    def test_open_bounds_conservative_member(self) -> None:
        # No timestamps anywhere → cannot disprove membership → member.
        ok, reason = tm.is_member(None, None, None, None)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_within_24h_tolerance_is_member(self) -> None:
        # Record 6h before the run window opens — inside the 24h slop.
        rs = re = _dt_utc("2026-07-10T02:00:00Z")
        ws, we = tm.run_window({"date": "2026-07-10T08:00:00Z"})
        ok, _ = tm.is_member(rs, re, ws, we)
        self.assertTrue(ok)


class AbsenceMarkerTests(unittest.TestCase):
    def test_marker_names_host_and_window(self) -> None:
        ws, we = tm.run_window({"date": "2026-07-10T08:37:46Z"})
        marker = tm.absence_marker("codex", ws, we, kind="transcript")
        self.assertIn("host=codex", marker)
        self.assertIn("2026-07-10", marker)
        self.assertIn("no transcript for this run", marker)

    def test_marker_unknown_host(self) -> None:
        marker = tm.absence_marker(None, None, None, kind="verdict")
        self.assertIn("host=unknown", marker)
        self.assertIn("window=unknown", marker)


if __name__ == "__main__":
    unittest.main(verbosity=2)
