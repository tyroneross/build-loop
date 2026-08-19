#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for model_resolver.py — availability fallback + in-tier chain + floor."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parent))
import model_resolver  # noqa: E402
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

    # The floor tier's live model once opus (its default) is in the down set.
    # These tests run host-neutral (`resolve` defaults to --host-providers any),
    # so the cross-vendor thinking entry is reachable.
    LIVE_FLOOR_MODEL = "gpt-5.6-terra"

    def test_frontier_override_to_haiku_is_clamped(self) -> None:
        # modelOverrides.frontier=haiku (PATTERN tier, two below floor) + all
        # frontier registry models down. Must NOT resolve to haiku. The floor is
        # enforced both here (the override is refused before the chain) and at
        # the source (resolve_with_tier_fallback), so the resolver returns a
        # floor-safe model directly.
        with tempfile.TemporaryDirectory() as td:
            self._write_config(Path(td), {"frontier": "haiku"}, self.ALL_FRONTIER)
            payload = resolve(td, "frontier")
            self.assertNotEqual(payload["model"], "haiku", payload)
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertEqual(payload["model"], self.LIVE_FLOOR_MODEL)  # thinking

    def test_frontier_override_to_sonnet_is_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._write_config(Path(td), {"frontier": "sonnet"}, self.ALL_FRONTIER)
            payload = resolve(td, "frontier")
            self.assertNotEqual(payload["model"], "sonnet", payload)
            self.assertNotEqual(payload["model"], "haiku")
            self.assertEqual(payload["model"], self.LIVE_FLOOR_MODEL)

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


class ConfigOverrideHonoredTests(unittest.TestCase):
    """BUIL-MODEL-RESOLUTION-kynysz4f4852m: a config override must beat the chain.

    `.build-loop/config.json` modelOverrides[tier] is read ONLY by
    `model_overrides.resolve_model`, which `resolve()` reaches through the
    cross-tier floor walk — i.e. only AFTER the in-tier availability chain is
    exhausted. With opus always available at frontier the chain never exhausts,
    so the user's explicit override was silently discarded in the NORMAL case.
    An override that silently does nothing is worse than one that errors.
    """

    def _write_override(self, workdir: Path, tier: str, model: str) -> None:
        bl = workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        (bl / "config.json").write_text(
            json.dumps({"modelOverrides": {tier: model}}), encoding="utf-8"
        )

    def test_config_override_honored_when_tier_default_available(self) -> None:
        # The repro: a non-default REGISTERED frontier id, nothing declared down.
        # fable is the deliberate second frontier entry, so "opus" here is proof
        # the in-tier chain ran and the override was thrown away.
        with tempfile.TemporaryDirectory() as td:
            self._write_override(Path(td), "frontier", "fable")
            payload = resolve(td, "frontier", host_providers="anthropic")
            self.assertEqual(payload["model"], "fable", payload)
            self.assertEqual(payload["source"], "config")
            self.assertTrue(payload["configured"])

    def test_state_override_honored_when_tier_default_available(self) -> None:
        # state.json is the older snapshot source and carries the same contract.
        with tempfile.TemporaryDirectory() as td:
            bl = Path(td) / ".build-loop"
            bl.mkdir(parents=True, exist_ok=True)
            (bl / "state.json").write_text(
                json.dumps({"config": {"modelOverrides": {"code": "gpt-5.4-mini"}}}),
                encoding="utf-8",
            )
            payload = resolve(td, "code", host_providers="any")
            self.assertEqual(payload["model"], "gpt-5.4-mini", payload)
            self.assertEqual(payload["source"], "state")

    def test_override_still_clamped_below_floor(self) -> None:
        # Honoring the override must NOT reopen the floor breach: a frontier
        # override to a pattern-tier model is still refused, chain runs instead.
        with tempfile.TemporaryDirectory() as td:
            self._write_override(Path(td), "frontier", "haiku")
            payload = resolve(td, "frontier", host_providers="anthropic")
            self.assertNotEqual(payload["model"], "haiku", payload)
            self.assertNotEqual(payload["model"], "sonnet")
            self.assertEqual(payload["model"], "opus")

    def test_unavailable_override_falls_through_to_chain(self) -> None:
        # An override naming a model that is DOWN degrades gracefully into the
        # normal chain rather than handing the dispatcher a dead id.
        with tempfile.TemporaryDirectory() as td:
            self._write_override(Path(td), "frontier", "fable")
            _write_availability(Path(td), ["fable"])
            payload = resolve(td, "frontier", host_providers="anthropic")
            self.assertEqual(payload["model"], "opus", payload)
            self.assertEqual(payload["source"], "in-tier-chain")


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
                 "ANTHROPIC_API_KEY": "", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "",
                 "CODEX_THREAD_ID": "", "CODEX_SHELL": "", "CODEX_CI": "",
                 "CODEX_SANDBOX": "", "CODEX_HOME": "", "OPENAI_API_KEY": ""},
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
                     "ANTHROPIC_API_KEY": "", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "",
                     "CODEX_THREAD_ID": "", "CODEX_SHELL": "", "CODEX_CI": "",
                     "CODEX_SANDBOX": "", "CODEX_HOME": "", "OPENAI_API_KEY": ""},
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
        # "in-tier-chain". The walk stops AT thinking (never code/pattern), and
        # since opus is BOTH the frontier and the thinking default it is already
        # in the down set — the floor-stop must skip it for the thinking tier's
        # next registered model, not hand back a dead id.
        with tempfile.TemporaryDirectory() as td:
            _write_availability(
                Path(td), ["opus", "fable", "gpt-5.6-sol", "gpt-5.5", "gpt-5.4"]
            )
            payload = resolve(td, "frontier")
            self.assertEqual(payload["source"], "tier-fallback")
            self.assertEqual(payload["fallback_tier"], "thinking")
            self.assertEqual(payload["model"], "gpt-5.6-terra")
            self.assertNotIn(payload["model"], {"opus", "sonnet", "haiku"})


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
            # opus heads the thinking tier but is in the down set, so the floor
            # lands on the tier's next live entry.
            self.assertEqual(payload["model"], "gpt-5.6-terra")

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
            self.assertEqual(payload["model"], "gpt-5.6-terra")


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

    def test_detected_codex_host_via_env_default(self) -> None:
        # Codex Desktop supplies host markers but commonly no OPENAI_API_KEY.
        # With Anthropic frontier entries unavailable, the default dispatch path
        # must stay on the OpenAI provider and select Sol.
        with tempfile.TemporaryDirectory() as td:
            result = run_resolver(
                "--workdir", td, "--tier", "frontier",
                "--unavailable", "opus,fable", "--plain",
                env={
                    "BUILD_LOOP_HOST_PROVIDERS": "",
                    "CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "Codex Desktop",
                    "CODEX_THREAD_ID": "thread-test",
                    "CODEX_SHELL": "1",
                    "CODEX_CI": "1",
                    "CLAUDECODE": "", "CLAUDE_CODE": "",
                    "CLAUDE_CODE_SESSION_ID": "", "ANTHROPIC_API_KEY": "",
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "gpt-5.6-sol")

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

    def test_host_neutral_still_picks_by_rank_not_recency(self) -> None:
        """With the host filter off, rank still decides — not release date.

        gpt-5.6-terra (2026-07-09) is newer than sonnet (2026-06-01) and used to
        win here. Removing the host filter must not smuggle recency back in: the
        preferred-list order is the capability rank, and it is the only key.
        """
        with tempfile.TemporaryDirectory() as td:
            r = self.mr.resolve_role(
                segment="generative_reasoning", tier="code",
                workdir=Path(td), host_providers=self.mr.HOST_FILTER_DISABLED,
            )
            self.assertEqual(r["model"], "sonnet")

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

    def test_cli_segment_flag_agrees_with_the_legacy_path(self) -> None:
        """--segment and no --segment must return the SAME model for a tier.

        This test previously asserted they DIVERGED, and documented the `code`
        tier as the discriminator: the two-axis path date-sorted to
        gpt-5.6-terra while the legacy in-tier walk kept rank order and gave
        sonnet. That divergence was the defect, not the contract — which model
        ran your work depended on which flag the caller passed. The test
        encoded the bug as spec, so it could never have caught it.

        Now both paths are rank-ordered and must agree. Compare with
        test_cli_no_segment_is_unchanged_legacy below, which pins the same
        answer from the other entry point.
        """
        with tempfile.TemporaryDirectory() as td:
            two_axis = run_resolver(
                "--workdir", td, "--tier", "code",
                "--segment", "generative_reasoning",
                "--host-providers", "any", "--plain",
            )
            self.assertEqual(two_axis.returncode, 0, two_axis.stderr)
            self.assertEqual(two_axis.stdout.strip(), "sonnet")

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


"""Program run out-of-process to exercise the sibling-module bootstrap.

Loads model_resolver.py BY FILE PATH with scripts/ scrubbed from sys.path, which
is the ONLY context that makes the module-level `import model_overrides` fail and
the path-insert branch fire. It then drives resolve() + resolve_role() end to end
so an incomplete import surfaces as the NameError it would cause in production
rather than as a silent import-time pass.
"""
_IMPORT_SHIM_PROGRAM = '''
import importlib.util, sys, tempfile
from pathlib import Path

RESOLVER = Path(sys.argv[1]).resolve()
SCRIPTS = RESOLVER.parent
# Scrub scripts/ so the module-level sibling import cannot resolve on entry.
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != SCRIPTS]
assert "model_overrides" not in sys.modules

spec = importlib.util.spec_from_file_location("model_resolver_shim", RESOLVER)
mod = importlib.util.module_from_spec(spec)
sys.modules["model_resolver_shim"] = mod
spec.loader.exec_module(mod)          # runs the bootstrap
assert str(SCRIPTS) in sys.path, "path-insert branch did not fire"

# Every name resolve()/resolve_role() reference unqualified must be bound.
for name in (
    "MODEL_REGISTRY", "TIERS", "expand_unavailable", "is_registered",
    "resolve_with_tier_fallback", "tier_of_model", "availability_store",
    "model_overrides",
):
    assert hasattr(mod, name), "missing symbol: " + name

with tempfile.TemporaryDirectory() as td:
    r = mod.resolve(tier="frontier", workdir=Path(td))
    rr = mod.resolve_role(
        segment="generative_reasoning", tier="thinking", workdir=Path(td)
    )
print(r["model"], rr["model"])
'''


class ImportShimTests(unittest.TestCase):
    """Regression: the sibling-module bootstrap must bind the FULL symbol set.

    The bootstrap used to be a try/except pair that duplicated the
    `from model_overrides import (...)` list, and the except copy omitted
    `expand_unavailable` — called unconditionally by both resolve() and
    resolve_role(). Loading the module by file path (a host that never puts
    scripts/ on sys.path) therefore raised NameError on EVERY resolve instead of
    degrading. Import-time success is NOT enough to catch that, so this test
    drives both entry points after forcing the path-insert branch.
    """

    def test_file_path_load_without_scripts_on_syspath_resolves(self) -> None:
        import os

        env = dict(os.environ)
        env.pop("PYTHONPATH", None)          # keep scripts/ off the child's path
        env["BUILD_LOOP_HOST_PROVIDERS"] = "anthropic"
        with tempfile.TemporaryDirectory() as cwd:
            proc = subprocess.run(
                [sys.executable, "-c", _IMPORT_SHIM_PROGRAM, str(RESOLVER)],
                capture_output=True, text=True, cwd=cwd, env=env, timeout=60,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("NameError", proc.stderr)
        # Anthropic host, nothing recorded unavailable -> both resolve to opus.
        self.assertEqual(proc.stdout.split(), ["opus", "opus"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class HostRamFitTests(unittest.TestCase):
    """A local model the machine cannot hold must never be routed to.

    build-loop ships to other people's laptops. Provider reachability answers
    "is there an inference runtime", not "will 19GB of weights load", and the
    taxonomy carried no resource metadata at all — so a 16GB host was being
    offered an 18-19GB coding model from the agentic_execution chain.
    """

    def test_heavy_local_models_filtered_on_a_small_host(self) -> None:
        with mock.patch.dict(os.environ, {"BUILD_LOOP_HOST_RAM_GB": "16"}):
            too_big = model_resolver._too_large_for_host()
        self.assertIn("qwen2.5-coder-32b", too_big)
        self.assertIn("qwen3-coder-30b", too_big)

    def test_nothing_filtered_on_a_large_host(self) -> None:
        with mock.patch.dict(os.environ, {"BUILD_LOOP_HOST_RAM_GB": "512"}):
            self.assertEqual(model_resolver._too_large_for_host(), set())

    def test_unknown_ram_never_filters(self) -> None:
        """Guessing low would silently strip every local model on a platform we
        merely failed to read. Absence of a reading is not evidence of a small host."""
        with mock.patch.object(model_resolver, "host_ram_gb", lambda: None):
            self.assertEqual(model_resolver._too_large_for_host(), set())

    def _resolve_agentic_t3(self, ram_gb: str) -> dict:
        env = dict(os.environ, BUILD_LOOP_HOST_RAM_GB=ram_gb)
        with tempfile.TemporaryDirectory() as td:
            out = subprocess.run(
                [sys.executable, str(HERE / "model_resolver.py"),
                 "--workdir", td, "--segment", "agentic_execution", "--tier", "T3",
                 # A local model is only ever REACHABLE on a host that dispatches
                 # local inference; on a Claude host the provider filter already
                 # excludes it. Declaring local reachable is what isolates the
                 # RAM-fit question this test is about.
                 "--host-providers", "anthropic,openai,local",
                 "--unavailable", "sonnet,gpt-5.6-terra,gpt-5.4", "--json"],
                capture_output=True, text=True, env=env, check=False,
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            return json.loads(out.stdout)

    def test_host_ram_changes_the_resolution_differentially(self) -> None:
        """DIFFERENTIAL, and wired-in by construction.

        An earlier version of this test asserted only that a small host does not
        resolve TO the heavy model — which passed even with the filter unwired,
        because the chain exhausted to null and null satisfies "not the heavy
        model". Comparing the two hosts is what makes the assertion convict:
        the same query must skip qwen3-coder-30b at 16GB and reach it at 512GB.
        """
        def skipped(payload: dict) -> set[str]:
            return {p["model"] for p in payload["resolution_path"] if p.get("skipped")}

        small, large = self._resolve_agentic_t3("16"), self._resolve_agentic_t3("512")
        self.assertIn("qwen3-coder-30b", skipped(small),
                      f"16GB host must skip an 18GB model: {small}")
        self.assertNotIn("qwen3-coder-30b", skipped(large),
                         f"512GB host must reach it: {large}")
        self.assertEqual(large["model"], "qwen3-coder-30b")

    def test_rows_without_a_requirement_are_kept(self) -> None:
        with mock.patch.dict(os.environ, {"BUILD_LOOP_HOST_RAM_GB": "1"}):
            too_big = model_resolver._too_large_for_host()
        self.assertNotIn("sonnet", too_big, "a hosted model declares no min_ram_gb")
        self.assertNotIn("opus", too_big)
