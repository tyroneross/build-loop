#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for unit_guard.

The first fixture is the 2026-11-01 incident: peak memory under a key named
``mb`` held MiB values next to a raw macOS ``/usr/bin/time -l`` RSS in bytes.
Every guard has a defect fixture, not only a clean path.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unit_guard import Quantity, UnitError, check, main, parse_time_l  # noqa: E402

MACOS_TIME_L = """\
        0.10 real         0.00 user         0.00 sys
             1212416  maximum resident set size
                   0  average shared memory size
                 245  page reclaims
            10807634  instructions retired
             1261808  peak memory footprint
"""

GNU_TIME_V = """\
\tCommand being timed: "sleep 0.1"
\tUser time (seconds): 0.01
\tSystem time (seconds): 0.02
\tPercent of CPU this job got: 0%
\tElapsed (wall clock) time (h:mm:ss or m:ss): 1:02.50
\tMaximum resident set size (kbytes): 2048
\tExit status: 0
"""


def kinds(result: dict, severity: str | None = "error") -> list[str]:
    return [f["kind"] for f in result["findings"] if severity is None or f["severity"] == severity]


class TestIncident(unittest.TestCase):
    def test_bytes_value_in_mb_array_is_flagged(self):
        result = check({"runs": {"pipeline": {"mb": [3614, 4962435072]}}})
        self.assertIn("implausible", kinds(result))
        self.assertIn("mixed-magnitude", kinds(result))

    def test_bytes_value_in_mib_array_is_flagged(self):
        result = check({"peak_mib": [3614, 4962435072]})
        self.assertIn("implausible", kinds(result))
        self.assertIn("mixed-magnitude", kinds(result))

    def test_unit_mix_across_separate_arrays_sharing_a_key(self):
        # The incident shape: the bytes value sat in its own single-element array.
        doc = {"validate": {"mb": [3614, 3616]}, "pipeline": {"mb": [4962435]}}
        self.assertIn("mixed-magnitude", kinds(check(doc)))

    def test_unit_mix_across_sibling_records(self):
        doc = {"rows": [{"rss_mib": 3614}, {"rss_mib": 4962435}]}
        self.assertIn("mixed-magnitude", kinds(check(doc)))

    def test_decimal_mb_key_warns_without_failing(self):
        result = check({"mb": [3614]})
        self.assertEqual(kinds(result), [])
        self.assertEqual(kinds(result, "warn"), ["decimal-unit"])


class TestParseTime(unittest.TestCase):
    def test_macos_rss_is_bytes(self):
        out = parse_time_l(MACOS_TIME_L)
        self.assertEqual(out["format"], "bsd-darwin")
        self.assertEqual(out["max_rss_bytes"], 1212416)
        self.assertEqual(out["peak_footprint_bytes"], 1261808)
        self.assertAlmostEqual(out["real_s"], 0.10)

    def test_gnu_rss_is_kilobytes(self):
        out = parse_time_l(GNU_TIME_V)
        self.assertEqual(out["format"], "gnu-verbose")
        self.assertEqual(out["max_rss_bytes"], 2048 * 1024)
        self.assertAlmostEqual(out["real_s"], 62.5)
        self.assertAlmostEqual(out["user_s"], 0.01)

    def test_same_number_differs_by_platform(self):
        mac = parse_time_l("  1.0 real 0.0 user 0.0 sys\n  2048  maximum resident set size\n",
                           platform="darwin")
        bsd = parse_time_l("  1.0 real 0.0 user 0.0 sys\n  2048  maximum resident set size\n",
                           platform="freebsd")
        self.assertEqual(mac["max_rss_bytes"] * 1024, bsd["max_rss_bytes"])

    def test_bsd_without_platform_marker_raises(self):
        with self.assertRaises(UnitError):
            parse_time_l("  1.0 real 0.0 user 0.0 sys\n  2048  maximum resident set size\n")

    def test_unknown_format_raises(self):
        with self.assertRaises(UnitError):
            parse_time_l("real 0m0.100s\nuser 0m0.000s\n")


class TestQuantity(unittest.TestCase):
    def test_binary_conversions_both_ways(self):
        self.assertEqual(Quantity(4962435072, "bytes").to("MiB").value, 4962435072 / 2 ** 20)
        self.assertEqual(Quantity(1, "GiB").to("bytes").value, 2 ** 30)
        self.assertEqual(Quantity(2048, "KiB").to("MiB").value, 2)

    def test_decimal_and_binary_stay_distinct(self):
        self.assertEqual(Quantity(1, "MB").to("bytes").value, 1_000_000)
        self.assertAlmostEqual(Quantity(1, "MiB").to("MB").value, 1.048576)

    def test_time_conversions_both_ways(self):
        self.assertEqual(Quantity(1500, "ms").to("s").value, 1.5)
        self.assertEqual(Quantity(2, "min").to("s").value, 120)
        self.assertAlmostEqual(Quantity(1, "s").to("us").value, 1e6)
        self.assertAlmostEqual(Quantity(3, "ns").to("us").value, 0.003)

    def test_pct_and_ratio(self):
        self.assertAlmostEqual(Quantity(87, "%").to("ratio").value, 0.87)

    def test_incompatible_conversion_raises(self):
        with self.assertRaises(UnitError):
            Quantity(3614, "MiB").to("s")
        with self.assertRaises(UnitError):
            Quantity(5, "count").to("%")

    def test_unknown_unit_raises(self):
        with self.assertRaises(UnitError):
            Quantity(1, "furlongs")


class TestCheck(unittest.TestCase):
    def test_clean_file_passes(self):
        doc = {"runs": [{"real_s": 6.52, "rss_mib": 3614, "pass_count": 3},
                        {"real_s": 4.12, "rss_mib": 460, "pass_count": 3}],
               "saved_pct": 87, "stage": 2, "pipeline_time_s": {"before": 15.07}}
        self.assertEqual(check(doc)["findings"], [])

    def test_missing_unit_is_flagged_once_per_array(self):
        result = check({"host_load": {"validate": [103.8, 102.3, 107.9]}})
        self.assertEqual(kinds(result), ["missing-unit"])
        self.assertEqual(result["findings"][0]["occurrences"], 3)

    def test_allow_list_exempts_key_and_descendants(self):
        doc = {"host_load": {"validate": [103.8, 102.3]}, "seed_value": 7}
        self.assertEqual(check(doc, allow={"host_load", "seed_value"})["findings"], [])

    def test_magnitude_guards(self):
        cases = [({"x_pct": 1200}, "pct"), ({"x_s": -1}, "negative"), ({"x_s": 2e7}, "s"),
                 ({"x_bytes": 1.5}, "integer"), ({"x_count": 2.5}, "count")]
        for doc, _ in cases:
            with self.subTest(doc=doc):
                self.assertEqual(kinds(check(doc)), ["implausible"])

    def test_counts_may_span_orders_of_magnitude(self):
        self.assertEqual(check({"file_count": [1, 50000]})["findings"], [])

    def test_records_mode_uses_sibling_unit(self):
        doc = {"memory": {"measure": "Peak memory", "unit": "MiB",
                          "records": [{"id": "a", "value": 3614.0}, {"id": "b", "value": 460.0}]},
               "saved": {"unit": "%", "records": [{"value": 87}, {"value": 0}]}}
        self.assertEqual(check(doc)["findings"], [])

    def test_records_mode_flags_implausible_value_for_unit(self):
        doc = {"memory": {"unit": "MiB",
                          "records": [{"value": 3614.0}, {"value": 4962435072}]}}
        self.assertIn("implausible", kinds(check(doc)))
        self.assertIn("mixed-magnitude", kinds(check(doc)))

    def test_records_mode_flags_unknown_unit(self):
        doc = {"memory": {"unit": "megs", "records": [{"value": 3614.0}]}}
        self.assertEqual(kinds(check(doc)), ["unknown-unit"])


class TestCli(unittest.TestCase):
    def _run(self, doc: dict, *extra: str) -> tuple[int, str]:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(doc, fh)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["check", fh.name, *extra])
            return code, buf.getvalue()
        finally:
            os.unlink(fh.name)

    def test_exit_codes_and_json(self):
        code, out = self._run({"mb": [3614, 4962435072]}, "--json")
        self.assertEqual(code, 1)
        self.assertGreaterEqual(json.loads(out)["errors"], 1)
        code, _ = self._run({"rss_mib": [3614, 460]})
        self.assertEqual(code, 0)

    def test_allow_flag(self):
        code, _ = self._run({"host_load": [1.0, 2.0]}, "--allow", "host_load")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
