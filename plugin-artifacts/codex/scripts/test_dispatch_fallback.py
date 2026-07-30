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
    # this is the real outage scenario, where the fallback target is the next
    # Anthropic frontier model (opus down -> fable).
    (bl / "model-availability.json").write_text(
        json.dumps({"hostProviders": ["anthropic"]}), encoding="utf-8"
    )


def _resolve_plain(workdir: str) -> str:
    """Resolve frontier through the NON-recording resolver, so the read itself
    persists nothing. Anthropic host so only Anthropic frontier models qualify."""
    r = subprocess.run(
        [sys.executable, str(HERE / "model_resolver.py"),
         "--workdir", workdir, "--tier", "frontier",
         "--host-providers", "anthropic", "--plain"],
        check=True, capture_output=True, text=True,
    )
    return r.stdout.strip()


class FallbackResolutionTests(unittest.TestCase):
    def test_opus_down_records_and_reresolves_to_fable(self) -> None:
        # The exact production flow: Agent tool errored "<model> unavailable" ->
        # orchestrator calls this helper -> records it, re-resolves to the next
        # available frontier model. Taking OPUS down is what proves the machinery
        # since 2026-07-28: opus is now the frontier default, so the pre-outage
        # answer is opus and only a real fallback can produce fable. (Recording
        # fable instead would leave the answer at opus either way — tautological.)
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            self.assertEqual(_resolve_plain(td), "opus")  # pre-outage baseline
            out = jrun(
                "--workdir", td, "--tier", "frontier",
                "--unavailable-model", "opus", "--json",
            )
            self.assertEqual(out["recorded"], "opus")
            self.assertTrue(out["newly_recorded"])
            self.assertEqual(out["model"], "fable")
            self.assertNotIn(out["model"], {"sonnet", "haiku"})

    def test_outage_persists_so_next_resolve_also_falls_back(self) -> None:
        # After the helper records the outage, the availability file holds it —
        # so a subsequent plain resolve returns the fallback without re-catching.
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            jrun("--workdir", td, "--tier", "frontier",
                 "--unavailable-model", "opus", "--json")
            self.assertIn("opus", _unavailable_ids(td))
            # Persistence is the point: a fresh, non-recording resolve still
            # returns the fallback rather than the recorded-down default.
            self.assertEqual(_resolve_plain(td), "fable")

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
                 "--unavailable-model", "opus", "--json")
            cleared = jrun("--workdir", td, "--clear", "opus", "--json")
            self.assertTrue(cleared["removed"])
            self.assertNotIn("opus", _unavailable_ids(td))
            # "Restores availability" means resolution goes back to opus, not
            # just that a row vanished from the file.
            self.assertEqual(_resolve_plain(td), "opus")


class TtlExpiryTests(unittest.TestCase):
    """Outages self-clear after their TTL — no manual --clear needed."""

    def _resolver(self, workdir: str) -> str:
        return _resolve_plain(workdir)

    def test_self_expiry_after_ttl(self) -> None:
        # Record opus down with a 2s TTL -> fable now; after 3s a fresh resolve
        # auto-expires the record and returns opus again (no manual clear).
        # Recording the DEFAULT (opus) is what makes expiry observable: the
        # answer differs before, during, and after the outage window.
        import time

        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            out = jrun("--workdir", td, "--tier", "frontier",
                       "--unavailable-model", "opus", "--ttl", "2", "--json")
            self.assertEqual(out["model"], "fable")
            time.sleep(3)
            self.assertEqual(self._resolver(td), "opus")
            # The expired record was lazily pruned from the store on read.
            self.assertEqual(_availability(td).get("unavailable"), [])

    def test_within_ttl_still_holds(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _anthropic_host(td)
            jrun("--workdir", td, "--tier", "frontier",
                 "--unavailable-model", "opus", "--ttl", "3600", "--json")
            # Well within TTL -> outage still in effect -> fable, not opus.
            self.assertEqual(self._resolver(td), "fable")
            ids = {r.get("id") for r in _availability(td)["unavailable"]}
            self.assertIn("opus", ids)

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
                "--unavailable-model", "opus", "--json",
            )
            # Which alternate wins depends on the host's detected providers, so
            # assert only what fail-open owes: a model resolved, and it is not
            # the one just recorded down.
            self.assertEqual(out["recorded"], "opus")
            self.assertIsNotNone(out["model"])
            self.assertNotEqual(out["model"], "opus")

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
