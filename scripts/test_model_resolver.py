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


def run_resolver(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    import os

    run_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(RESOLVER), *args],
        check=False,
        capture_output=True,
        text=True,
        env=run_env,
    )


def resolve(workdir: str, tier: str, **kw: str) -> dict:
    # Default to --host-providers any so these tests are deterministic regardless
    # of which host they run on (host DETECTION is exercised by dedicated tests in
    # HostDetectionTests). Callers that want the filter pass host_providers= or
    # use a config hostProviders file.
    args = ["--workdir", workdir, "--tier", tier, "--json"]
    if "host_providers" not in kw:
        args += ["--host-providers", "any"]
    for k, v in kw.items():
        args += [f"--{k.replace('_', '-')}", v]
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


class FloorClampTests(unittest.TestCase):
    """Closes independent-auditor f1/f2: a config override must not breach the floor.

    resolve_with_tier_fallback honors a modelOverrides value before its own floor
    walk, so a frontier override to a sub-thinking model would otherwise resolve
    frontier -> sonnet/haiku. The clamp in model_resolver.resolve() rejects any
    provably-below-floor model and re-resolves.
    """

    def _write_config(self, workdir: Path, overrides: dict, unavailable: list[str]) -> None:
        bl = workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        (bl / "config.json").write_text(
            json.dumps({"modelOverrides": overrides}), encoding="utf-8"
        )
        (bl / "model-availability.json").write_text(
            json.dumps({"unavailable": unavailable}), encoding="utf-8"
        )

    def test_frontier_override_to_haiku_is_clamped(self) -> None:
        # modelOverrides.frontier=haiku (PATTERN tier, two below floor) + all
        # frontier registry models down. Must NOT resolve to haiku. The floor is
        # enforced at the source (resolve_with_tier_fallback), so the resolver
        # returns the floor-safe model directly.
        with tempfile.TemporaryDirectory() as td:
            self._write_config(
                Path(td), {"frontier": "haiku"}, ["fable", "gpt-5.5", "gpt-5.4"]
            )
            payload = resolve(td, "frontier")
            self.assertNotEqual(payload["model"], "haiku", payload)
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertEqual(payload["model"], "opus")  # thinking floor

    def test_frontier_override_to_sonnet_is_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._write_config(
                Path(td), {"frontier": "sonnet"}, ["fable", "gpt-5.5", "gpt-5.4"]
            )
            payload = resolve(td, "frontier")
            self.assertNotEqual(payload["model"], "sonnet", payload)
            self.assertNotEqual(payload["model"], "haiku")
            self.assertEqual(payload["model"], "opus")

    def test_frontier_override_to_thinking_model_is_allowed(self) -> None:
        # opus IS the thinking floor — a frontier override to opus is permitted
        # (frontier's standing fallback is thinking). Not clamped.
        with tempfile.TemporaryDirectory() as td:
            self._write_config(
                Path(td), {"frontier": "opus"}, ["fable", "gpt-5.5", "gpt-5.4"]
            )
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "opus")
            self.assertNotIn("floor_clamped", payload)

    def test_unknown_override_model_is_not_clamped(self) -> None:
        # A brand-new model id we can't place in the registry must NOT be refused
        # (we can't prove it's below floor; refusing all unknowns breaks valid
        # overrides to new models).
        with tempfile.TemporaryDirectory() as td:
            self._write_config(
                Path(td), {"frontier": "brand-new-frontier-x"},
                ["fable", "gpt-5.5", "gpt-5.4"],
            )
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "brand-new-frontier-x")

    def test_resolution_path_reports_true_tier_not_requested(self) -> None:
        # f2: the audit trail must not label a sub-tier model as 'frontier'.
        with tempfile.TemporaryDirectory() as td:
            self._write_config(
                Path(td), {"frontier": "haiku"}, ["fable", "gpt-5.5", "gpt-5.4"]
            )
            payload = resolve(td, "frontier")
            for step in payload["resolution_path"]:
                if step.get("model") == "haiku":
                    # haiku must be recorded as its true (pattern) tier, skipped.
                    self.assertNotEqual(step.get("tier"), "frontier")


class HostProvidersFilterTests(unittest.TestCase):
    """Host-neutral provider filter: a model the host can't dispatch is excluded."""

    def _write_host(self, workdir: Path, unavailable: list[str], providers: list[str]) -> None:
        bl = workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        (bl / "model-availability.json").write_text(
            json.dumps({"unavailable": unavailable, "hostProviders": providers}),
            encoding="utf-8",
        )

    def _resolve_config_host(self, td: str) -> dict:
        # Call WITHOUT --host-providers so the config-file hostProviders is the
        # source under test. Suppress env host-detection so the result depends
        # only on the config file.
        result = run_resolver(
            "--workdir", td, "--tier", "frontier", "--json",
            env={"BUILD_LOOP_HOST_PROVIDERS": "", "CLAUDECODE": "",
                 "CLAUDE_CODE": "", "CLAUDE_CODE_SESSION_ID": "",
                 "ANTHROPIC_API_KEY": ""},
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def test_anthropic_only_host_fable_down_resolves_to_opus(self) -> None:
        # Claude Code can only dispatch Anthropic models, so cross-vendor frontier
        # alternates are unreachable. Fable down + config hostProviders=[anthropic]
        # -> opus, no manual config of each cross-vendor id needed.
        with tempfile.TemporaryDirectory() as td:
            self._write_host(Path(td), ["fable"], ["anthropic"])
            payload = self._resolve_config_host(td)
            self.assertEqual(payload["model"], "opus", payload)
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertNotEqual(payload["model"], "haiku")

    def test_host_filter_absent_keeps_all_providers(self) -> None:
        # No hostProviders + env detection suppressed = host-neutral: cross-vendor
        # allowed. (The default dispatch path DETECTS the host — covered by
        # HostDetectionTests; this asserts the no-signal fallback.)
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable"])
            result = run_resolver(
                "--workdir", td, "--tier", "frontier", "--json",
                env={"BUILD_LOOP_HOST_PROVIDERS": "", "CLAUDECODE": "",
                     "CLAUDE_CODE": "", "CLAUDE_CODE_SESSION_ID": "",
                     "ANTHROPIC_API_KEY": ""},
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-5.5")

    def test_anthropic_only_host_uses_anthropic_default_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._write_host(Path(td), [], ["anthropic"])
            payload = self._resolve_config_host(td)
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


class CanonicalIdResolverTests(unittest.TestCase):
    """GAP 1 regression at the resolver/dispatch layer: outage by canonical id."""

    def test_canonical_fable_id_fires_fallback(self) -> None:
        # The literal outage signal id (claude-fable-5) must be treated as the
        # alias `fable` being down. On an anthropic host -> opus.
        with tempfile.TemporaryDirectory() as td:
            payload = resolve(
                td, "frontier",
                host_providers="anthropic",
                unavailable="claude-fable-5",
            )
            self.assertEqual(payload["model"], "opus", payload)
            self.assertNotEqual(payload["model"], "fable")

    def test_alias_and_canonical_both_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            by_alias = resolve(td, "frontier", host_providers="anthropic", unavailable="fable")
            by_canon = resolve(td, "frontier", host_providers="anthropic", unavailable="claude-fable-5")
            self.assertEqual(by_alias["model"], by_canon["model"])
            self.assertEqual(by_canon["model"], "opus")


class HostDetectionTests(unittest.TestCase):
    """GAP 2 regression: the host filter applies BY DEFAULT on the dispatch path."""

    def test_explicit_anthropic_host_fable_down_resolves_opus(self) -> None:
        # The exact GAP-2 failure: on a Claude host, fable down must NOT offer
        # gpt-5.5 (undispatchable here) — it resolves to opus.
        with tempfile.TemporaryDirectory() as td:
            payload = resolve(td, "frontier", host_providers="anthropic", unavailable="fable")
            self.assertEqual(payload["model"], "opus", payload)
            self.assertNotEqual(payload["model"], "gpt-5.5")

    def test_detected_anthropic_host_via_env_default(self) -> None:
        # No config, no explicit flag — host detection via env must fire so the
        # default dispatch path filters to anthropic.
        with tempfile.TemporaryDirectory() as td:
            result = run_resolver(
                "--workdir", td, "--tier", "frontier", "--unavailable", "fable",
                "--plain",
                env={"BUILD_LOOP_HOST_PROVIDERS": "anthropic"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "opus")

    def test_host_filter_any_disables_filtering(self) -> None:
        # --host-providers any opts out: cross-vendor frontier alternate allowed.
        with tempfile.TemporaryDirectory() as td:
            payload = resolve(td, "frontier", host_providers="any", unavailable="fable")
            self.assertEqual(payload["model"], "gpt-5.5")

    def test_help_exposes_host_flag(self) -> None:
        result = run_resolver("--help")
        self.assertIn("--host-providers", result.stdout)


class CliShapeTests(unittest.TestCase):
    def test_plain_prints_model_id_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["fable"])
            # host-providers any so this is deterministic across hosts.
            result = run_resolver(
                "--workdir", td, "--tier", "frontier", "--host-providers", "any",
                "--plain",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "gpt-5.5")

    def test_unknown_tier_rejected(self) -> None:
        result = run_resolver("--workdir", ".", "--tier", "bogus")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
