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

    def test_bytes_value_in_its_own_array_under_a_sibling_record(self):
        # The incident shape: the bytes value sat in its own single-element array
        # under the same key in a sibling record. Mixed-magnitude compares only
        # siblings, so the implausibility bound must catch it.
        doc = {"store": {"validate": {"before": {"mib": [3614]}},
                         "pipeline": {"before": {"mib": [4962435072]}}}}
        result = check(doc)
        self.assertEqual(kinds(result), ["implausible"])
        self.assertEqual(result["findings"][0]["path"], "store.pipeline.before.mib[0]")

    def test_unit_mix_across_sibling_records(self):
        # KB stored as MiB: each value is plausible alone, so the spread warns.
        doc = {"rows": [{"rss_mib": 3614}, {"rss_mib": 4962435}]}
        self.assertEqual(kinds(check(doc), "warn"), ["mixed-magnitude"])
        doc = {"rows": [{"rss_mib": 3614}, {"rss_mib": 4962435072}]}
        self.assertEqual(sorted(kinds(check(doc))), ["implausible", "mixed-magnitude"])

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


class TestReviewFindings(unittest.TestCase):
    """Regressions for the 2026-11-01 independent review of unit_guard."""

    def test_decimal_numeric_strings_cannot_bypass_checks(self):
        for text in ("4962435072.", "+4962435072", ".4962435072e10", "-0.5", "-.5"):
            with self.subTest(text=text):
                result = check({"rss_mib": text})
                self.assertEqual(result["checked"], 1)
                self.assertIn("implausible", kinds(result))

    def test_records_keep_version_label_semantics(self):
        doc = {"unit": "ms", "records": [{"schema_version": 1, "value": 12}]}
        self.assertEqual(check(doc)["findings"], [])

    # 1. Statistic and dimension suffixes are not units.
    def test_statistic_suffix_keeps_the_unit_before_it(self):
        doc = {"latency_ms_min": 12, "latency_ms_max": 40, "latency_ms_p95": 31,
               "wall_s_mean": 2.5, "rss_mib_total": 900}
        self.assertEqual(check(doc)["findings"], [])
        # 12 MINUTES would pass; 2e7 ms read as ms is still checked as ms.
        self.assertEqual(kinds(check({"latency_ms_min": -1})), ["implausible"])

    def test_single_letter_h_and_min_are_not_units(self):
        self.assertEqual(kinds(check({"box_h": 900})), ["missing-unit"])
        self.assertEqual(kinds(check({"duration_min": 5})), ["missing-unit"])
        self.assertEqual(check({"box_h_px": 900, "duration_minutes": 5,
                                "uptime_hours": 3, "idle_mins": 2, "job_hrs": 1})["findings"], [])

    # 2. Rates are compound units, never the last token.
    def test_rate_is_a_compound_unit(self):
        doc = {"throughput_mib_per_s": 300, "ops_per_s": 1.2e5, "tokens_per_s": 80,
               "price_usd_per_mtok": 3.0}
        self.assertEqual(check(doc)["findings"], [])
        # Read as seconds, 2e7 would be implausible; as bytes/s it is a rate.
        self.assertEqual(kinds(check({"throughput_bytes_per_s": 2e7})), [])
        self.assertEqual(kinds(check({"throughput_mib_per_s": 2e7})), ["implausible"])

    def test_rate_with_unknown_side_is_flagged(self):
        result = check({"speed_furlongs_per_s": 3, "load_mib_per_fortnight": 4})
        self.assertEqual(kinds(result), ["unknown-unit", "unknown-unit"])

    def test_negative_rate_is_implausible(self):
        self.assertEqual(kinds(check({"throughput_mib_per_s": -5})), ["implausible"])

    # 3. Mixed magnitude compares siblings only, and plausible spreads only warn.
    def test_unrelated_subtrees_are_not_compared(self):
        doc = {"unit_tests": {"wall_s": 0.004}, "pipeline": {"wall_s": 30}}
        self.assertEqual(check(doc)["findings"], [])

    def test_before_after_speedup_is_not_a_unit_mix(self):
        self.assertEqual(check({"time_s": {"before": 15, "after": 0.01}})["findings"], [])

    def test_plausible_spread_in_one_array_warns_without_failing(self):
        result = check({"wall_s": [0.004, 30]})
        self.assertEqual(kinds(result), [])
        self.assertEqual(kinds(result, "warn"), ["mixed-magnitude"])

    def test_spread_with_implausible_value_still_errors(self):
        result = check({"mib": [3614, 4962435072]})
        self.assertIn("mixed-magnitude", kinds(result))
        self.assertIn("implausible", kinds(result))

    def test_spread_across_records_in_one_list_warns(self):
        doc = {"rows": [{"wall_s": 0.004}, {"wall_s": 30}]}
        self.assertEqual(kinds(check(doc), "warn"), ["mixed-magnitude"])

    # 4. Labels, declared-unit siblings, numeric strings.
    def test_version_is_a_label(self):
        self.assertEqual(check({"version": 1.2, "schema_version": 3})["findings"], [])

    def test_declared_unit_covers_sibling_statistics(self):
        doc = {"latency": {"unit": "ms", "p50": 12, "p90": 30, "min": 1, "max": 80, "mean": 20}}
        self.assertEqual(check(doc)["findings"], [])
        bad = {"rss": {"unit": "MiB", "p50": 3614, "max": 4962435072}}
        self.assertIn("implausible", kinds(check(bad)))

    def test_numeric_string_warns(self):
        # A unit-keyed numeric string is checked like a number (some consumers,
        # e.g. chart facets, require text); only a key with no unit warns.
        self.assertEqual(kinds(check({"rss_mib": "3614"}), "warn"), [])
        self.assertEqual(kinds(check({"rss_mib": "4962435072"})), ["implausible"])
        self.assertEqual(kinds(check({"rss": "3614"}), "warn"), ["numeric-string"])


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



class NumericStringWithUnitKeyTests(unittest.TestCase):
    """Text fields that must stay strings pass when the key names the unit."""

    def _check(self, doc):
        import unit_guard
        return unit_guard.check_document(doc) if hasattr(unit_guard, "check_document") else None

    def test_unit_keyed_numeric_strings(self) -> None:
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        script = Path(__file__).resolve().parent / "unit_guard.py"
        cases = {
            "unit-keyed ok": ({"facets": {"before_mib": "3614.0", "after_s": "4.2", "pair_index": "1"}}, 0, 0),
            "bare key warns": ({"facets": {"before": "3614.0"}}, 0, 1),
            "unit-keyed but implausible": ({"facets": {"before_mib": "4962435072"}}, 1, None),
        }
        for name, (doc, want_rc, want_warn) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as td:
                f = Path(td) / "f.json"
                f.write_text(json.dumps(doc))
                p = subprocess.run([sys.executable, str(script), "check", str(f), "--json"],
                                   capture_output=True, text=True)
                self.assertEqual(p.returncode, want_rc, p.stdout + p.stderr)
                if want_warn is not None:
                    out = json.loads(p.stdout)
                    warns = [x for x in out.get("findings", []) if x.get("severity") == "warn"]
                    self.assertEqual(len(warns), want_warn, p.stdout)


if __name__ == "__main__":
    unittest.main()
