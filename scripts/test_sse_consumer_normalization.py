#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for sse_consumer.py adapter shape coercion.

Specifically covers _normalize_handler_locations() — the adapter's defensive
parser for the event_handler_locations envelope field, which the detector
emits as list[dict] and legacy hand-built envelopes pass as list[str].

Zero deps. Run: python3 test_sse_consumer_normalization.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "runtime_smoke_adapters"))

from sse_consumer import (  # noqa: E402
    SSE_CURL_DURATION_MAX_SECONDS,
    SSE_CURL_DURATION_SECONDS,
    _normalize_handler_locations,
    _resolve_probe_duration,
)


class TestNormalizeHandlerLocations(unittest.TestCase):
    def test_list_of_strings_passthrough(self):
        self.assertEqual(
            _normalize_handler_locations(["src/serve.py", "src/ui.tsx"]),
            ["src/serve.py", "src/ui.tsx"],
        )

    def test_list_of_dicts_extract_file(self):
        """Detector contract shape."""
        detector_output = [
            {"file": "src/serve.py", "line": 561, "function": "handleEvent"},
            {"file": "src/serve.py", "line": 720, "function": "onmessage"},
        ]
        # Same file appears twice — dedup expected.
        self.assertEqual(
            _normalize_handler_locations(detector_output),
            ["src/serve.py"],
        )

    def test_dedup_preserves_first_occurrence_order(self):
        detector_output = [
            {"file": "src/a.py", "line": 1, "function": "h1"},
            {"file": "src/b.py", "line": 2, "function": "h2"},
            {"file": "src/a.py", "line": 3, "function": "h3"},
        ]
        self.assertEqual(
            _normalize_handler_locations(detector_output),
            ["src/a.py", "src/b.py"],
        )

    def test_mixed_dict_and_string(self):
        """Tolerate accidentally mixed envelopes."""
        mixed = [
            {"file": "src/a.py", "line": 1, "function": "h"},
            "src/b.py",
            {"file": "src/a.py", "line": 9, "function": "h2"},  # dup
        ]
        self.assertEqual(
            _normalize_handler_locations(mixed),
            ["src/a.py", "src/b.py"],
        )

    def test_none_returns_empty(self):
        self.assertEqual(_normalize_handler_locations(None), [])

    def test_empty_list_returns_empty(self):
        self.assertEqual(_normalize_handler_locations([]), [])

    def test_dict_without_file_dropped(self):
        """Malformed entry — drop silently rather than crash the smoke gate."""
        bad = [
            {"line": 1, "function": "h"},  # no `file` key
            {"file": "src/ok.py", "line": 2, "function": "g"},
        ]
        self.assertEqual(_normalize_handler_locations(bad), ["src/ok.py"])

    def test_non_str_non_dict_entries_dropped(self):
        weird = ["src/a.py", 42, None, {"file": "src/b.py"}, ["nested"]]
        self.assertEqual(
            _normalize_handler_locations(weird),
            ["src/a.py", "src/b.py"],
        )


class TestResolveProbeDuration(unittest.TestCase):
    def test_missing_returns_default(self):
        self.assertEqual(_resolve_probe_duration({}), SSE_CURL_DURATION_SECONDS)

    def test_none_returns_default(self):
        self.assertEqual(
            _resolve_probe_duration({"smoke_duration_seconds": None}),
            SSE_CURL_DURATION_SECONDS,
        )

    def test_in_range_passes_through(self):
        self.assertEqual(_resolve_probe_duration({"smoke_duration_seconds": 15}), 15)

    def test_string_numeric_coerced(self):
        self.assertEqual(_resolve_probe_duration({"smoke_duration_seconds": "10"}), 10)

    def test_zero_falls_back_to_default(self):
        self.assertEqual(
            _resolve_probe_duration({"smoke_duration_seconds": 0}),
            SSE_CURL_DURATION_SECONDS,
        )

    def test_negative_falls_back_to_default(self):
        self.assertEqual(
            _resolve_probe_duration({"smoke_duration_seconds": -5}),
            SSE_CURL_DURATION_SECONDS,
        )

    def test_over_max_clamped(self):
        self.assertEqual(
            _resolve_probe_duration({"smoke_duration_seconds": 999}),
            SSE_CURL_DURATION_MAX_SECONDS,
        )

    def test_non_numeric_falls_back(self):
        self.assertEqual(
            _resolve_probe_duration({"smoke_duration_seconds": "soon"}),
            SSE_CURL_DURATION_SECONDS,
        )

    def test_float_truncated_via_int(self):
        # int("3.9") raises, so 3.9 → 3 via int(3.9)
        self.assertEqual(_resolve_probe_duration({"smoke_duration_seconds": 3.9}), 3)


if __name__ == "__main__":
    unittest.main()
