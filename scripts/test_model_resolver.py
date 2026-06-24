#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for model_resolver.py — availability fallback + in-tier chain + floor."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESOLVER = HERE / "model_resolver.py"


def _write_availability(workdir: Path, unavailable: list[str]) -> None:
    bl = workdir / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    (bl / "model-availability.json").write_text(
        json.dumps({"unavailable": unavailable}), encoding="utf-8"
    )


def _write_tier_cache(workdir: Path, entries: dict) -> None:
    bl = workdir / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    (bl / "model-tier-cache.json").write_text(json.dumps(entries), encoding="utf-8")


def run_resolver(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RESOLVER), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def resolve(workdir: str, tier: str, **kw: str) -> dict:
    args = ["--workdir", workdir, "--tier", tier, "--json"]
    for k, v in kw.items():
        args += [f"--{k}", v]
    result = run_resolver(*args)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class FloorInvariantTests(unittest.TestCase):
    """The load-bearing falsifier: Fable down must NEVER drop below thinking."""

    def test_frontier_fable_unavailable_resolves_to_opus_never_lower(self) -> None:
        # The exact production scenario: an Anthropic-only host (Claude Code) where
        # the cross-vendor frontier models (gpt-5.5/gpt-5.4) are NOT dispatchable,
        # so they belong in the unavailable set alongside the down Fable. Result:
        # frontier -> opus automatically, never sonnet/haiku. This is the bug fix.
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable", "gpt-5.5", "gpt-5.4"])
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "opus", payload)
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertNotEqual(payload["model"], "haiku")
            # The decision is auditable.
            self.assertIn("resolution_path", payload)
            self.assertIn("fable", payload["unavailable_considered"])

    def test_frontier_fable_down_with_reachable_alternate_uses_it(self) -> None:
        # On a multi-vendor host where gpt-5.5 IS dispatchable, Fable down should
        # prefer the available same-tier alternate over descending — "highest
        # priority AVAILABLE model in the chain" (req 1). Floor still respected.
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable"])
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "gpt-5.5", payload)
            self.assertEqual(payload["source"], "in-tier-chain")
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertNotEqual(payload["model"], "haiku")

    def test_frontier_floor_holds_even_when_thinking_default_also_down(self) -> None:
        # Hard invariant from model_overrides: frontier never resolves to
        # code/pattern even when both fable AND opus are unavailable.
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable", "opus"])
            payload = resolve(td, "frontier")
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertNotEqual(payload["model"], "haiku")

    def test_in_tier_alternate_preferred_over_cross_tier_descent(self) -> None:
        # When fable is down but a verified frontier alternate exists in the
        # registry (gpt-5.5), the in-tier walk should pick it BEFORE descending.
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable"])
            # Make the registry's next frontier model the only available one by
            # NOT marking gpt-5.5 unavailable; resolver should select it.
            payload = resolve(td, "frontier")
            # opus (cross-tier) only if gpt-5.5 also unavailable; here it's not,
            # so an in-tier candidate must win.
            self.assertEqual(payload["source"], "in-tier-chain")
            self.assertEqual(payload["model"], "gpt-5.5")


class HostProvidersFilterTests(unittest.TestCase):
    """Host-neutral provider filter: a model the host can't dispatch is excluded."""

    def _write_host(self, workdir: Path, unavailable: list[str], providers: list[str]) -> None:
        bl = workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        (bl / "model-availability.json").write_text(
            json.dumps({"unavailable": unavailable, "hostProviders": providers}),
            encoding="utf-8",
        )

    def test_anthropic_only_host_fable_down_resolves_to_opus(self) -> None:
        # The real production case stated plainly: Claude Code can only dispatch
        # Anthropic models, so cross-vendor frontier alternates are unreachable.
        # Fable down + hostProviders=[anthropic] -> opus, no manual config of
        # each cross-vendor id needed.
        with tempfile.TemporaryDirectory() as td:
            self._write_host(Path(td), ["fable"], ["anthropic"])
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "opus", payload)
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertNotEqual(payload["model"], "haiku")

    def test_host_filter_absent_keeps_all_providers(self) -> None:
        # No hostProviders declared = host-neutral default: cross-vendor allowed.
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable"])
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "gpt-5.5")

    def test_anthropic_only_host_uses_anthropic_default_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._write_host(Path(td), [], ["anthropic"])
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "fable")


class AvailabilityPersistenceTests(unittest.TestCase):
    def test_no_availability_file_resolves_default(self) -> None:
        # Fail-open: absent availability file = empty unavailable set.
        with tempfile.TemporaryDirectory() as td:
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "fable")
            self.assertEqual(payload["source"], "in-tier-chain")

    def test_extra_unavailable_merges_with_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable"])
            # gpt-5.5 marked unavailable ad-hoc; gpt-5.4 still available in-tier.
            payload = resolve(td, "frontier", unavailable="gpt-5.5")
            self.assertEqual(payload["model"], "gpt-5.4")
            self.assertEqual(payload["source"], "in-tier-chain")

    def test_all_frontier_unavailable_descends_to_opus(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable", "gpt-5.5", "gpt-5.4"])
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "opus")
            self.assertEqual(payload["source"], "tier-fallback")


class TierIntegrityGuardTests(unittest.TestCase):
    """A guessed (unverified) tier-cache entry must never enter the frontier chain."""

    def test_unverified_cached_frontier_id_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable", "gpt-5.5", "gpt-5.4"])
            _write_tier_cache(
                Path(td),
                {
                    "mystery-model-x": {
                        "tier": "frontier",
                        "provider": "unknown",
                        "provenance": "unverified",
                    }
                },
            )
            payload = resolve(td, "frontier")
            # The unverified id must NOT be selected; resolution descends to opus.
            self.assertNotEqual(payload["model"], "mystery-model-x")
            self.assertEqual(payload["model"], "opus")

    def test_verified_cached_frontier_id_is_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable", "gpt-5.5", "gpt-5.4"])
            _write_tier_cache(
                Path(td),
                {
                    "new-frontier-model": {
                        "tier": "frontier",
                        "provider": "somevendor",
                        "provenance": "verified",
                    }
                },
            )
            payload = resolve(td, "frontier")
            # A verified frontier alternate is selectable in-tier before descent.
            self.assertEqual(payload["model"], "new-frontier-model")
            self.assertEqual(payload["source"], "in-tier-chain")

    def test_cached_id_for_wrong_tier_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable", "gpt-5.5", "gpt-5.4"])
            _write_tier_cache(
                Path(td),
                {
                    "code-tier-model": {
                        "tier": "code",
                        "provider": "x",
                        "provenance": "verified",
                    }
                },
            )
            payload = resolve(td, "frontier")
            self.assertNotEqual(payload["model"], "code-tier-model")
            self.assertEqual(payload["model"], "opus")


class CliShapeTests(unittest.TestCase):
    def test_plain_prints_model_id_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable"])
            result = run_resolver("--workdir", td, "--tier", "frontier", "--plain")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "gpt-5.5")

    def test_unknown_tier_rejected(self) -> None:
        result = run_resolver("--workdir", ".", "--tier", "bogus")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
