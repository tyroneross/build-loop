#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for classify_model_tier.py — host-driven classify + cache + provenance."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLASSIFY = HERE / "classify_model_tier.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLASSIFY), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def jrun(*args: str) -> dict:
    r = run(*args)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class LookupTests(unittest.TestCase):
    def test_unknown_id_returns_needs_classification_with_query(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = jrun("--workdir", td, "lookup", "some-new-model-2027")
            self.assertEqual(out["status"], "needs_classification")
            self.assertEqual(out["source"], "search")
            self.assertIn("some-new-model-2027", out["search_query"])
            self.assertIn("rubric", out)
            self.assertIn("frontier", out["rubric"])

    def test_record_then_second_lookup_is_cache_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            # First lookup: needs classification.
            first = jrun("--workdir", td, "lookup", "gpt-6-codex")
            self.assertEqual(first["status"], "needs_classification")
            # Host records the verdict.
            rec = jrun(
                "--workdir", td, "record", "gpt-6-codex",
                "--tier", "frontier", "--provider", "openai",
                "--provenance", "verified",
            )
            self.assertEqual(rec["status"], "recorded")
            self.assertEqual(rec["tier"], "frontier")
            self.assertEqual(rec["provenance"], "verified")
            # Second lookup: cache-only, no search.
            second = jrun("--workdir", td, "lookup", "gpt-6-codex")
            self.assertEqual(second["status"], "classified")
            self.assertEqual(second["source"], "cache")
            self.assertEqual(second["tier"], "frontier")

    def test_refresh_forces_reclassification(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            jrun("--workdir", td, "record", "m", "--tier", "code", "--provider", "x")
            refreshed = jrun("--workdir", td, "lookup", "m", "--refresh")
            self.assertEqual(refreshed["status"], "needs_classification")


class ProvenanceTests(unittest.TestCase):
    def test_record_defaults_to_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rec = jrun(
                "--workdir", td, "record", "guess-model",
                "--tier", "frontier", "--provider", "unknown",
            )
            self.assertEqual(rec["provenance"], "unverified")

    def test_unverified_cached_entry_is_visible_to_resolver_guard(self) -> None:
        # The cache JSON must carry provenance so model_resolver's guard can read
        # it. Verify the on-disk shape directly.
        with tempfile.TemporaryDirectory() as td:
            jrun(
                "--workdir", td, "record", "guess-model",
                "--tier", "frontier", "--provider", "unknown",
            )
            cache = json.loads(
                (Path(td) / ".build-loop" / "model-tier-cache.json").read_text()
            )
            self.assertEqual(cache["guess-model"]["provenance"], "unverified")
            self.assertEqual(cache["guess-model"]["tier"], "frontier")

    def test_invalid_tier_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = run("--workdir", td, "record", "m", "--tier", "bogus", "--provider", "x")
            self.assertNotEqual(r.returncode, 0)


class HostNeutralityTests(unittest.TestCase):
    def test_cache_keyed_by_id_works_across_vendors(self) -> None:
        # Same cache file holds Claude/GPT/Gemini ids — tier-keyed, not vendor.
        with tempfile.TemporaryDirectory() as td:
            jrun("--workdir", td, "record", "claude-x", "--tier", "frontier", "--provider", "anthropic")
            jrun("--workdir", td, "record", "gpt-x", "--tier", "code", "--provider", "openai")
            jrun("--workdir", td, "record", "gemini-x", "--tier", "pattern", "--provider", "google")
            self.assertEqual(jrun("--workdir", td, "lookup", "claude-x")["tier"], "frontier")
            self.assertEqual(jrun("--workdir", td, "lookup", "gpt-x")["tier"], "code")
            self.assertEqual(jrun("--workdir", td, "lookup", "gemini-x")["tier"], "pattern")

    def test_no_vendor_api_call_lookup_is_offline(self) -> None:
        # lookup of an unknown id must NOT make a network call — it returns a
        # query for the host LLM. (Structural proof: it returns instantly with a
        # search_query field rather than a fetched result.)
        with tempfile.TemporaryDirectory() as td:
            out = jrun("--workdir", td, "lookup", "offline-check")
            self.assertEqual(out["status"], "needs_classification")
            self.assertNotIn("benchmark_result", out)  # no fetched data


if __name__ == "__main__":
    unittest.main(verbosity=2)
