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
    # Write LIVE (non-expired) timestamped records — the current store shape for
    # "this model is down right now". Legacy bare-string expiry is covered by the
    # dedicated TtlExpiryTests below, so general resolver tests use live records.
    import time

    bl = workdir / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    records = [{"id": m, "recorded_at": time.time(), "ttl": 3600} for m in unavailable]
    (bl / "model-availability.json").write_text(
        json.dumps({"unavailable": records}), encoding="utf-8"
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
    """The load-bearing falsifier: the frontier DEFAULT (opus) being down must
    NEVER drop resolution below thinking."""

    def test_frontier_default_unavailable_resolves_in_tier_never_lower(self) -> None:
        # The exact production scenario: an Anthropic-only host (Claude Code) where
        # the cross-vendor frontier models are NOT dispatchable, so the frontier
        # default (opus) going down leaves exactly one reachable same-tier peer —
        # fable, the deliberate second choice. Result: frontier -> fable
        # automatically, never sonnet/haiku. This is the bug fix.
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["opus"])
            payload = resolve(td, "frontier", host_providers="anthropic")
            self.assertEqual(payload["model"], "fable", payload)
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertNotEqual(payload["model"], "haiku")
            # The decision is auditable.
            self.assertIn("resolution_path", payload)
            self.assertIn("opus", payload["unavailable_considered"])

    def test_frontier_default_down_with_reachable_alternate_uses_it(self) -> None:
        # The frontier default down should prefer an available same-tier alternate
        # over descending — "highest priority AVAILABLE model in the chain"
        # (req 1). Floor still respected. `source` proves the in-tier walk ran:
        # a cross-tier descent would report `tier-fallback` and yield opus.
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["opus"])
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "fable", payload)
            self.assertEqual(payload["source"], "in-tier-chain")
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertNotEqual(payload["model"], "haiku")

    def test_frontier_floor_holds_even_when_thinking_default_also_down(self) -> None:
        # Hard invariant from model_overrides: frontier never resolves to
        # code/pattern even when the WHOLE frontier chain is down — which now
        # includes opus, the thinking tier's default as well as frontier's.
        with tempfile.TemporaryDirectory() as td:
            _write_availability(
                Path(td), ["opus", "fable", "gpt-5.6-sol", "gpt-5.5", "gpt-5.4"]
            )
            payload = resolve(td, "frontier")
            self.assertIsNotNone(payload["model"])
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertNotEqual(payload["model"], "haiku")

    def test_in_tier_alternate_preferred_over_cross_tier_descent(self) -> None:
        # When BOTH Anthropic frontier models are down but a verified frontier
        # alternate exists in the registry (GPT-5.6 Sol), the in-tier walk should
        # pick it BEFORE descending. A descent here would return opus/thinking.
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["opus", "fable"])
            payload = resolve(td, "frontier")
            self.assertEqual(payload["source"], "in-tier-chain")
            self.assertEqual(payload["model"], "gpt-5.6-sol")


class FloorClampTests(unittest.TestCase):
    """Closes independent-auditor f1/f2: a config override must not breach the floor.

    resolve_with_tier_fallback honors a modelOverrides value before its own floor
    walk, so a frontier override to a sub-thinking model would otherwise resolve
    frontier -> sonnet/haiku. The clamp in model_resolver.resolve() rejects any
    provably-below-floor model and re-resolves.
    """

    def _write_config(self, workdir: Path, overrides: dict, unavailable: list[str]) -> None:
        import time

        bl = workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        (bl / "config.json").write_text(
            json.dumps({"modelOverrides": overrides}), encoding="utf-8"
        )
        records = [{"id": m, "recorded_at": time.time(), "ttl": 3600} for m in unavailable]
        (bl / "model-availability.json").write_text(
            json.dumps({"unavailable": records}), encoding="utf-8"
        )

    # Every frontier registry model — the override path in
    # resolve_with_tier_fallback is only REACHED once the in-tier walk in
    # model_resolver.resolve() is exhausted, so the clamp tests must down them all.
    ALL_FRONTIER = ["opus", "fable", "gpt-5.6-sol", "gpt-5.5", "gpt-5.4"]

    def test_frontier_override_to_haiku_is_clamped(self) -> None:
        # modelOverrides.frontier=haiku (PATTERN tier, two below floor) + all
        # frontier registry models down. Must NOT resolve to haiku. The floor is
        # enforced at the source (resolve_with_tier_fallback), so the resolver
        # returns the floor-safe model directly.
        with tempfile.TemporaryDirectory() as td:
            self._write_config(Path(td), {"frontier": "haiku"}, self.ALL_FRONTIER)
            payload = resolve(td, "frontier")
            self.assertNotEqual(payload["model"], "haiku", payload)
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertEqual(payload["model"], "opus")  # thinking floor

    def test_frontier_override_to_sonnet_is_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._write_config(Path(td), {"frontier": "sonnet"}, self.ALL_FRONTIER)
            payload = resolve(td, "frontier")
            self.assertNotEqual(payload["model"], "sonnet", payload)
            self.assertNotEqual(payload["model"], "haiku")
            self.assertEqual(payload["model"], "opus")

    def test_frontier_override_to_thinking_model_is_allowed(self) -> None:
        # A frontier override to a THINKING-tier model is permitted (frontier's
        # standing fallback is thinking) — it is honored as `source: config`, not
        # clamped. gpt-5.6-terra is the probe rather than opus precisely because
        # opus is also what the floor walk would return: only an honored override
        # can produce gpt-5.6-terra here.
        with tempfile.TemporaryDirectory() as td:
            self._write_config(
                Path(td), {"frontier": "gpt-5.6-terra"}, self.ALL_FRONTIER
            )
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "gpt-5.6-terra", payload)
            self.assertEqual(payload["source"], "config")
            self.assertNotIn("floor_clamped", payload)

    def test_unknown_override_model_is_not_clamped(self) -> None:
        # A brand-new model id we can't place in the registry must NOT be refused
        # (we can't prove it's below floor; refusing all unknowns breaks valid
        # overrides to new models).
        with tempfile.TemporaryDirectory() as td:
            self._write_config(
                Path(td), {"frontier": "brand-new-frontier-x"}, self.ALL_FRONTIER
            )
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "brand-new-frontier-x")

    def test_resolution_path_reports_true_tier_not_requested(self) -> None:
        # f2: the audit trail must not label a sub-tier model as the requested
        # tier. Probed on a THINKING request whose whole registry is down, so the
        # descent genuinely lands on a lower-tier model (code/sonnet) — a frontier
        # request cannot falsify this any more, since its floor model (opus) is
        # itself frontier-tier.
        with tempfile.TemporaryDirectory() as td:
            self._write_config(
                Path(td), {}, ["opus", "gpt-5.6-terra", "gpt-5.4", "gemini-2.5-pro"]
            )
            payload = resolve(td, "thinking")
            self.assertEqual(payload["model"], "sonnet", payload)
            final = payload["resolution_path"][-1]
            self.assertEqual(final["model"], "sonnet")
            # sonnet must be recorded as its TRUE (code) tier, not "thinking".
            self.assertEqual(final["tier"], "code")


class HostProvidersFilterTests(unittest.TestCase):
    """Host-neutral provider filter: a model the host can't dispatch is excluded."""

    def _write_host(self, workdir: Path, unavailable: list[str], providers: list[str]) -> None:
        import time

        bl = workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        records = [{"id": m, "recorded_at": time.time(), "ttl": 3600} for m in unavailable]
        (bl / "model-availability.json").write_text(
            json.dumps({"unavailable": records, "hostProviders": providers}),
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

    def test_anthropic_only_host_skips_unreachable_frontier_alternate(self) -> None:
        # Claude Code can only dispatch Anthropic models, so cross-vendor frontier
        # alternates are unreachable. BOTH Anthropic frontier entries down + config
        # hostProviders=[anthropic] -> the floor walk, NOT gpt-5.6-sol (which a
        # host-blind resolver would happily hand back). No manual config of each
        # cross-vendor id needed. The floor stops at thinking, whose default is
        # also opus — so the returned id is opus, reported as `tier-fallback`.
        with tempfile.TemporaryDirectory() as td:
            self._write_host(Path(td), ["opus", "fable"], ["anthropic"])
            payload = self._resolve_config_host(td)
            self.assertNotEqual(payload["model"], "gpt-5.6-sol", payload)
            self.assertNotEqual(payload["model"], "gpt-5.5")
            self.assertEqual(payload["source"], "tier-fallback")
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertNotEqual(payload["model"], "haiku")

    def test_host_filter_absent_keeps_all_providers(self) -> None:
        # No hostProviders + env detection suppressed = host-neutral: cross-vendor
        # allowed. Both Anthropic frontier models are down, so only a cross-vendor
        # pick can satisfy this. (The default dispatch path DETECTS the host —
        # covered by HostDetectionTests; this asserts the no-signal fallback.)
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["opus", "fable"])
            result = run_resolver(
                "--workdir", td, "--tier", "frontier", "--json",
                env={"BUILD_LOOP_HOST_PROVIDERS": "", "CLAUDECODE": "",
                     "CLAUDE_CODE": "", "CLAUDE_CODE_SESSION_ID": "",
                     "ANTHROPIC_API_KEY": ""},
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-5.6-sol")

    def test_anthropic_only_host_uses_anthropic_default_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._write_host(Path(td), [], ["anthropic"])
            payload = self._resolve_config_host(td)
            self.assertEqual(payload["model"], "opus")


class AvailabilityPersistenceTests(unittest.TestCase):
    def test_no_availability_file_resolves_default(self) -> None:
        # Fail-open: absent availability file = empty unavailable set.
        with tempfile.TemporaryDirectory() as td:
            payload = resolve(td, "frontier")
            self.assertEqual(payload["model"], "opus")
            self.assertEqual(payload["source"], "in-tier-chain")

    def test_extra_unavailable_merges_with_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            # opus + fable come from DISK, Sol + GPT-5.5 from the --unavailable
            # flag; GPT-5.4 remains available in-tier. Dropping either source
            # would resolve to a member of that source instead.
            _write_availability(Path(td), ["opus", "fable"])
            payload = resolve(td, "frontier", unavailable="gpt-5.6-sol,gpt-5.5")
            self.assertEqual(payload["model"], "gpt-5.4")
            self.assertEqual(payload["source"], "in-tier-chain")

    def test_all_frontier_unavailable_hands_off_to_floor_walk(self) -> None:
        # Every same-tier candidate down -> the in-tier walk is exhausted and
        # resolution hands off to the cross-tier floor walk. `source` is the
        # proof of the hand-off: an in-tier selection would report
        # "in-tier-chain". The walk stops AT thinking (never code/pattern) —
        # and since opus is now BOTH the frontier and the thinking default, the
        # floor-stop returns opus even though it is itself in the down set.
        with tempfile.TemporaryDirectory() as td:
            _write_availability(
                Path(td), ["opus", "fable", "gpt-5.6-sol", "gpt-5.5", "gpt-5.4"]
            )
            payload = resolve(td, "frontier")
            self.assertEqual(payload["source"], "tier-fallback")
            self.assertEqual(payload["fallback_tier"], "thinking")
            self.assertEqual(payload["model"], "opus")
            self.assertNotIn(payload["model"], {"sonnet", "haiku"})


class TtlExpiryTests(unittest.TestCase):
    """The resolve/dispatch read self-expires + prunes stale outage records."""

    def _write_records(self, workdir: Path, records: list) -> None:
        bl = workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        (bl / "model-availability.json").write_text(
            json.dumps({"unavailable": records}), encoding="utf-8"
        )

    # These probe the DEFAULT frontier model (opus): a record that fails to expire
    # would push resolution onto fable, so "expired" and "live" have distinguishable
    # outcomes and neither assertion can pass by default.

    def test_legacy_flat_list_self_heals_to_default(self) -> None:
        # The exact stale-state bug: a timestamp-less {"unavailable":["opus"]}
        # must be treated as expired on first read -> frontier resolves to opus.
        with tempfile.TemporaryDirectory() as td:
            self._write_records(Path(td), ["opus"])
            payload = resolve(td, "frontier", host_providers="anthropic")
            self.assertEqual(payload["model"], "opus", payload)
            # And the stale record is pruned from disk on read.
            disk = json.loads(
                (Path(td) / ".build-loop" / "model-availability.json").read_text()
            )
            self.assertEqual(disk["unavailable"], [])

    def test_expired_object_record_self_heals(self) -> None:
        import time

        with tempfile.TemporaryDirectory() as td:
            self._write_records(Path(td), [
                {"id": "opus", "recorded_at": time.time() - 10_000, "ttl": 1}
            ])
            payload = resolve(td, "frontier", host_providers="anthropic")
            self.assertEqual(payload["model"], "opus", payload)

    def test_within_ttl_object_record_still_blocks(self) -> None:
        import time

        with tempfile.TemporaryDirectory() as td:
            self._write_records(Path(td), [
                {"id": "opus", "recorded_at": time.time(), "ttl": 3600}
            ])
            payload = resolve(td, "frontier", host_providers="anthropic")
            # Live record honored -> the next reachable frontier peer, not opus.
            self.assertEqual(payload["model"], "fable", payload)
            self.assertNotIn(payload["model"], {"sonnet", "haiku"})


class TierIntegrityGuardTests(unittest.TestCase):
    """A guessed (unverified) tier-cache entry must never enter the frontier chain.

    Every registry frontier model (opus included) is downed in these tests so the
    cache entry is the only in-tier candidate left — otherwise the guard is never
    reached and the assertions pass for the wrong reason.
    """

    ALL_FRONTIER = ["opus", "fable", "gpt-5.6-sol", "gpt-5.5", "gpt-5.4"]

    def test_unverified_cached_frontier_id_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), self.ALL_FRONTIER)
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
            # The unverified id must NOT be selected; resolution hands off to the
            # floor walk instead (source proves the in-tier chain found nothing).
            self.assertNotEqual(payload["model"], "mystery-model-x")
            self.assertEqual(payload["source"], "tier-fallback")
            self.assertEqual(payload["model"], "opus")

    def test_verified_cached_frontier_id_is_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), self.ALL_FRONTIER)
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
            _write_availability(Path(td), self.ALL_FRONTIER)
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
            self.assertEqual(payload["source"], "tier-fallback")
            self.assertEqual(payload["model"], "opus")


class CanonicalIdResolverTests(unittest.TestCase):
    """GAP 1 regression at the resolver/dispatch layer: outage by canonical id."""

    def test_canonical_opus_id_fires_fallback(self) -> None:
        # The literal outage signal id (claude-opus-5) must be treated as the
        # alias `opus` being down. On an anthropic host -> fable, the second
        # frontier entry. Without the canonical<->alias fold, opus would be
        # selected as if nothing were down.
        with tempfile.TemporaryDirectory() as td:
            payload = resolve(
                td, "frontier",
                host_providers="anthropic",
                unavailable="claude-opus-5",
            )
            self.assertEqual(payload["model"], "fable", payload)
            self.assertNotEqual(payload["model"], "opus")

    def test_alias_and_canonical_both_recognized(self) -> None:
        # Both Anthropic frontier models down, declared by alias in one call and
        # by canonical id in the other. Host-neutral, so the surviving in-tier
        # candidate (gpt-5.6-sol) is reachable and proves BOTH ids folded.
        with tempfile.TemporaryDirectory() as td:
            by_alias = resolve(
                td, "frontier", host_providers="any", unavailable="opus,fable"
            )
            by_canon = resolve(
                td, "frontier", host_providers="any",
                unavailable="claude-opus-5,claude-fable-5",
            )
            self.assertEqual(by_alias["model"], by_canon["model"])
            self.assertEqual(by_canon["model"], "gpt-5.6-sol")


class HostDetectionTests(unittest.TestCase):
    """GAP 2 regression: the host filter applies BY DEFAULT on the dispatch path."""

    def test_explicit_anthropic_host_excludes_cross_vendor_frontier(self) -> None:
        # The exact GAP-2 failure: on a Claude host with BOTH Anthropic frontier
        # entries down, resolution must NOT offer gpt-5.6-sol/gpt-5.5
        # (undispatchable here) — it takes the floor walk instead.
        with tempfile.TemporaryDirectory() as td:
            payload = resolve(
                td, "frontier", host_providers="anthropic", unavailable="opus,fable"
            )
            self.assertNotEqual(payload["model"], "gpt-5.6-sol", payload)
            self.assertNotEqual(payload["model"], "gpt-5.5")
            self.assertEqual(payload["source"], "tier-fallback")

    def test_detected_anthropic_host_via_env_default(self) -> None:
        # No config, no explicit flag — host detection via env must fire so the
        # default dispatch path filters to anthropic. Without detection the next
        # available frontier entry is the cross-vendor gpt-5.6-sol; with it, the
        # walk floors at thinking (opus). Printing "opus" is therefore the proof
        # that the env-detected filter was applied.
        with tempfile.TemporaryDirectory() as td:
            result = run_resolver(
                "--workdir", td, "--tier", "frontier",
                "--unavailable", "opus,fable", "--plain",
                env={"BUILD_LOOP_HOST_PROVIDERS": "anthropic"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "opus")

    def test_host_filter_any_disables_filtering(self) -> None:
        # --host-providers any opts out: cross-vendor frontier alternate allowed
        # once both Anthropic frontier entries are down.
        with tempfile.TemporaryDirectory() as td:
            payload = resolve(
                td, "frontier", host_providers="any", unavailable="opus,fable"
            )
            self.assertEqual(payload["model"], "gpt-5.6-sol")

    def test_help_exposes_host_flag(self) -> None:
        result = run_resolver("--help")
        self.assertIn("--host-providers", result.stdout)


class CliShapeTests(unittest.TestCase):
    def test_plain_prints_model_id_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["opus", "fable"])
            # host-providers any so this is deterministic across hosts.
            result = run_resolver(
                "--workdir", td, "--tier", "frontier", "--host-providers", "any",
                "--plain",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "gpt-5.6-sol")

    def test_unknown_tier_rejected(self) -> None:
        result = run_resolver("--workdir", ".", "--tier", "bogus")
        self.assertNotEqual(result.returncode, 0)


class ResolveRoleTests(unittest.TestCase):
    """The two-axis resolve_role path: persistent availability + host-provider
    reachability layered over model_overrides.resolve_role."""

    def setUp(self) -> None:
        import importlib
        self.mr = importlib.import_module("model_resolver")

    def test_anthropic_host_filters_unreachable_recency_winner(self) -> None:
        # GR/code preferred = [sonnet, gpt-5.6-terra, gpt-5.4, gemini-2.5-pro];
        # gpt-5.6-terra is newer (recency promotes it to the front) but is
        # unreachable on a Claude host -> sonnet selected.
        with tempfile.TemporaryDirectory() as td:
            r = self.mr.resolve_role(
                segment="generative_reasoning", tier="code",
                workdir=Path(td), host_providers={"anthropic"},
            )
            self.assertEqual(r["model"], "sonnet")

    def test_host_neutral_picks_recency_winner(self) -> None:
        # Same cell, no host filter: recency must re-order the list so the newer
        # gpt-5.6-terra beats the list-order first entry (sonnet).
        with tempfile.TemporaryDirectory() as td:
            r = self.mr.resolve_role(
                segment="generative_reasoning", tier="code",
                workdir=Path(td), host_providers=self.mr.HOST_FILTER_DISABLED,
            )
            self.assertEqual(r["model"], "gpt-5.6-terra")  # newer, no host filter

    def test_persistent_availability_respected(self) -> None:
        # opus recorded unavailable on disk + anthropic host -> no reachable GR
        # T2 model -> floor walk inherits to T3 (sonnet, reachable on Claude).
        with tempfile.TemporaryDirectory() as td:
            _write_availability(Path(td), ["opus"])
            r = self.mr.resolve_role(
                segment="generative_reasoning", tier="thinking",
                workdir=Path(td), host_providers={"anthropic"},
            )
            # opus down + gpt-5.5 unreachable -> ladder floor walk -> sonnet (code)
            self.assertEqual(r["model"], "sonnet")

    def test_cli_segment_flag_uses_two_axis_path(self) -> None:
        # --segment routes through resolve_role; without it, legacy single-axis.
        # The `code` tier discriminates the two paths: the two-axis preferred
        # list applies the recency tiebreak (-> gpt-5.6-terra) while the legacy
        # in-tier registry walk keeps registry order (-> sonnet).
        with tempfile.TemporaryDirectory() as td:
            two_axis = run_resolver(
                "--workdir", td, "--tier", "code",
                "--segment", "generative_reasoning",
                "--host-providers", "any", "--plain",
            )
            self.assertEqual(two_axis.returncode, 0, two_axis.stderr)
            self.assertEqual(two_axis.stdout.strip(), "gpt-5.6-terra")

    def test_cli_no_segment_is_unchanged_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            legacy = run_resolver(
                "--workdir", td, "--tier", "code", "--host-providers", "any",
                "--plain",
            )
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            self.assertEqual(legacy.stdout.strip(), "sonnet")

    def test_cli_ladder_rung_tier_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = run_resolver(
                "--workdir", td, "--tier", "T3", "--segment", "agentic_execution",
                "--host-providers", "anthropic", "--plain",
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "sonnet")


if __name__ == "__main__":
    unittest.main(verbosity=2)
