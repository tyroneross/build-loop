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


def _unavailable_ids(workdir: str) -> list[str]:
    """The model ids recorded unavailable (records are timestamped objects now)."""
    out: list[str] = []
    for r in _availability(workdir).get("unavailable", []):
        out.append(r if isinstance(r, str) else r.get("id"))
    return out


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
            self.assertIn("fable", _unavailable_ids(td))

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
            self.assertEqual(_unavailable_ids(td).count("fable"), 1)

    def test_preserves_host_providers_key_on_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            jrun("--workdir", td, "--tier", "frontier",
                 "--unavailable-model", "fable", "--json")
            data = _availability(td)
            self.assertEqual(data["hostProviders"], ["anthropic"])
            self.assertIn("fable", _unavailable_ids(td))

    def test_clear_restores_availability(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            jrun("--workdir", td, "--tier", "frontier",
                 "--unavailable-model", "fable", "--json")
            cleared = jrun("--workdir", td, "--clear", "fable", "--json")
            self.assertTrue(cleared["removed"])
            self.assertNotIn("fable", _availability(td).get("unavailable", []))


class TtlExpiryTests(unittest.TestCase):
    """Outages self-clear after their TTL — no manual --clear needed."""

    def _resolver(self, workdir: str) -> str:
        # Use the NON-recording resolver for the "is it still down?" read so the
        # read itself doesn't persist anything. Anthropic host so fable->opus.
        r = subprocess.run(
            [sys.executable, str(HERE / "model_resolver.py"),
             "--workdir", workdir, "--tier", "frontier",
             "--host-providers", "anthropic", "--plain"],
            check=True, capture_output=True, text=True,
        )
        return r.stdout.strip()

    def test_self_expiry_after_ttl(self) -> None:
        # Record fable down with a 2s TTL -> opus now; after 3s a fresh resolve
        # auto-expires the record and returns fable (no manual clear).
        import time

        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            out = jrun("--workdir", td, "--tier", "frontier",
                       "--unavailable-model", "fable", "--ttl", "2", "--json")
            self.assertEqual(out["model"], "opus")
            time.sleep(3)
            self.assertEqual(self._resolver(td), "fable")
            # The expired record was lazily pruned from the store on read.
            self.assertEqual(_availability(td).get("unavailable"), [])

    def test_within_ttl_still_holds(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            jrun("--workdir", td, "--tier", "frontier",
                 "--unavailable-model", "fable", "--ttl", "3600", "--json")
            # Well within TTL -> outage still in effect -> opus.
            self.assertEqual(self._resolver(td), "opus")
            ids = {r.get("id") for r in _availability(td)["unavailable"]}
            self.assertIn("fable", ids)

    def test_per_record_ttl_override_stored(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            jrun("--workdir", td, "--tier", "frontier",
                 "--unavailable-model", "fable", "--ttl", "999", "--json")
            rec = _availability(td)["unavailable"][0]
            self.assertEqual(rec["id"], "fable")
            self.assertEqual(rec["ttl"], 999)
            self.assertIn("recorded_at", rec)

    def test_record_writes_timestamped_object_not_bare_string(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            jrun("--workdir", td, "--tier", "frontier",
                 "--unavailable-model", "fable", "--json")
            rec = _availability(td)["unavailable"][0]
            self.assertIsInstance(rec, dict)
            self.assertIn("recorded_at", rec)
            self.assertIn("ttl", rec)

    def test_clear_removes_object_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            jrun("--workdir", td, "--tier", "frontier",
                 "--unavailable-model", "fable", "--json")
            cleared = jrun("--workdir", td, "--clear", "fable", "--json")
            self.assertTrue(cleared["removed"])
            self.assertEqual(_availability(td)["unavailable"], [])

    def test_clear_removes_legacy_string_record(self) -> None:
        # --clear must still work against a pre-existing legacy flat-list entry.
        with tempfile.TemporaryDirectory() as td:
            bl = Path(td) / ".build-loop"
            bl.mkdir(parents=True, exist_ok=True)
            (bl / "model-availability.json").write_text(
                json.dumps({"unavailable": ["fable", "opus"]}), encoding="utf-8"
            )
            cleared = jrun("--workdir", td, "--clear", "fable", "--json")
            self.assertTrue(cleared["removed"])
            ids = {
                (r if isinstance(r, str) else r.get("id"))
                for r in _availability(td)["unavailable"]
            }
            self.assertNotIn("fable", ids)
            self.assertIn("opus", ids)


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
