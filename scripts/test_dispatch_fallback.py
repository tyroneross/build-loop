#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for dispatch_fallback.py — record outage + re-resolve + idempotent."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FALLBACK = HERE / "dispatch_fallback.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FALLBACK), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def jrun(*args: str) -> dict:
    r = run(*args)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _availability(workdir: str) -> dict:
    p = Path(workdir) / ".build-loop" / "model-availability.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _anthropic_host(workdir: str) -> None:
    bl = Path(workdir) / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    # Anthropic-only host so cross-vendor frontier alternates aren't offered —
    # this is the real Fable-down scenario where the fallback target is Opus.
    (bl / "model-availability.json").write_text(
        json.dumps({"hostProviders": ["anthropic"]}), encoding="utf-8"
    )


class FallbackResolutionTests(unittest.TestCase):
    def test_fable_down_records_and_reresolves_to_opus(self) -> None:
        # The exact production flow: Agent tool errored "Fable unavailable" ->
        # orchestrator calls this helper -> records fable, re-resolves to opus.
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            out = jrun(
                "--workdir", td, "--tier", "frontier",
                "--unavailable-model", "fable", "--json",
            )
            self.assertEqual(out["recorded"], "fable")
            self.assertTrue(out["newly_recorded"])
            self.assertEqual(out["model"], "opus")
            self.assertNotIn(out["model"], {"sonnet", "haiku"})

    def test_outage_persists_so_next_resolve_also_falls_back(self) -> None:
        # After the helper records the outage, the availability file holds it —
        # so a subsequent plain resolve returns the fallback without re-catching.
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            jrun("--workdir", td, "--tier", "frontier",
                 "--unavailable-model", "fable", "--json")
            self.assertIn("fable", _availability(td)["unavailable"])

    def test_record_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            first = jrun("--workdir", td, "--tier", "frontier",
                         "--unavailable-model", "fable", "--json")
            second = jrun("--workdir", td, "--tier", "frontier",
                          "--unavailable-model", "fable", "--json")
            self.assertTrue(first["newly_recorded"])
            self.assertFalse(second["newly_recorded"])
            # Still exactly one entry.
            self.assertEqual(_availability(td)["unavailable"].count("fable"), 1)

    def test_preserves_host_providers_key_on_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            jrun("--workdir", td, "--tier", "frontier",
                 "--unavailable-model", "fable", "--json")
            data = _availability(td)
            self.assertEqual(data["hostProviders"], ["anthropic"])
            self.assertIn("fable", data["unavailable"])

    def test_clear_restores_availability(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            jrun("--workdir", td, "--tier", "frontier",
                 "--unavailable-model", "fable", "--json")
            cleared = jrun("--workdir", td, "--clear", "fable", "--json")
            self.assertTrue(cleared["removed"])
            self.assertNotIn("fable", _availability(td).get("unavailable", []))


class FailOpenTests(unittest.TestCase):
    def test_missing_files_fail_open(self) -> None:
        # No .build-loop at all — helper still records + resolves, no crash.
        with tempfile.TemporaryDirectory() as td:
            out = jrun(
                "--workdir", td, "--tier", "frontier",
                "--unavailable-model", "fable", "--json",
            )
            # Without a host filter, the next frontier alternate is gpt-5.5.
            self.assertEqual(out["recorded"], "fable")
            self.assertIsNotNone(out["model"])

    def test_clear_nonexistent_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = jrun("--workdir", td, "--clear", "never-recorded", "--json")
            self.assertFalse(out["removed"])

    def test_requires_tier_and_model_without_clear(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = run("--workdir", td, "--json")
            self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
