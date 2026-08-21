#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Adversarial tests for cli_dispatch_consent.py.

Contract: references/cli-dispatch-consent-contract.md. Implementation under
attack: scripts/cli_dispatch_consent.py.

This suite does NOT re-test the happy path (test_preauthorization.py-style
convention already covers "does a legitimate auto grant allow"). It runs the
documented attacks from the contract's own "What this gate is, and is not"
section and asserts each is DETECTED or REFUSED:

  A. forged grant by direct file write (bad/absent hash)
  B. in-place edit of an existing entry (mode flipped, hash left stale)
  C. truncation / rollback (valid prefix — chain alone cannot catch this;
     only the head hash, held externally by the operator, can)
  D. full chain recomputation by a same-UID attacker (chain alone cannot
     catch this either — same-uid attacker can always recompute)
  E. store-path env-var redirection outside a test process
  F. depth-guard bypass attempts (garbage, empty, negative, huge, and
     depth-beats-a-recorded-auto)
  G. key isolation (a grant for one key must never leak to another)
  H. absence/malformation of the store (never consent)
  I. mode-string smuggling (near-miss strings/types never grant)

Every test sets BUILD_LOOP_CONSENT_SELFTEST=1 and BUILD_LOOP_CLI_CONSENT_PATH
to a per-test tmp file, and every direct-manipulation test additionally passes
an explicit `path=` to the library calls so the real
~/.build-loop/cli-dispatch-consent.json is never in the call path at all.
setUpModule/tearDownModule stat the real store before and after the whole
run and fail loudly if it changed.

Run: python3 scripts/test_cli_dispatch_consent_attacks.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import cli_dispatch_consent as cdc  # noqa: E402

REAL_STORE = Path.home() / ".build-loop" / "cli-dispatch-consent.json"

# ---------------------------------------------------------------------------
# Module-level guarantee: the real per-operator store is never read or
# written by this suite. We stat it before any test runs and again after the
# whole module finishes; any change fails the run loudly rather than quietly.
# ---------------------------------------------------------------------------
_real_store_existed_before: bool = False
_real_store_stat_before: tuple[int, int] | None = None


def setUpModule() -> None:
    global _real_store_existed_before, _real_store_stat_before
    _real_store_existed_before = REAL_STORE.exists()
    if _real_store_existed_before:
        st = REAL_STORE.stat()
        _real_store_stat_before = (st.st_mtime_ns, st.st_size)
    else:
        _real_store_stat_before = None


def tearDownModule() -> None:
    existed_after = REAL_STORE.exists()
    if existed_after != _real_store_existed_before:
        raise RuntimeError(
            "REAL ~/.build-loop/cli-dispatch-consent.json existence changed "
            f"during this test run: before={_real_store_existed_before} "
            f"after={existed_after}. This suite must never touch the real store."
        )
    if existed_after:
        st = REAL_STORE.stat()
        stat_after = (st.st_mtime_ns, st.st_size)
        if stat_after != _real_store_stat_before:
            raise RuntimeError(
                "REAL ~/.build-loop/cli-dispatch-consent.json was MODIFIED "
                f"during this test run: before={_real_store_stat_before} "
                f"after={stat_after}."
            )
    print(
        f"[real-store-check] untouched: existed_before={_real_store_existed_before} "
        f"existed_after={existed_after} stat={_real_store_stat_before}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_store(path: Path, log: list[dict[str, Any]], version: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": version, "log": log}, indent=2))


def _valid_entry(seq: int, key: str, mode: Any, prev_sha256: str | None,
                  **field_overrides: Any) -> dict[str, Any]:
    """Build one wire-format entry with a CORRECTLY computed entry_sha256.

    `field_overrides` lets a caller build a deliberately weird entry (e.g. a
    non-string mode, or a missing "mode" key with `drop_mode=True`) while
    still hashing it correctly — because case H/I attacks are about the mode
    value or key being wrong, not about a broken hash chain. Keeping the hash
    correct isolates what's actually being tested.
    """
    entry: dict[str, Any] = {
        "seq": seq,
        "key": key,
        "mode": mode,
        "decided_at": "2026-01-01T00:00:00Z",
        "decided_by": "user",
        "decided_via": "test",
        "decided_in_repo": "/tmp/attack-test-repo",
        "prev_sha256": prev_sha256,
    }
    if field_overrides.pop("drop_mode", False):
        del entry["mode"]
    entry.update(field_overrides)
    entry["entry_sha256"] = cdc.entry_hash(entry)
    return entry


class ConsentAttackTestCase(unittest.TestCase):
    """Common scaffolding: per-test tmp store + the two required env vars."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store_path = Path(self._tmp.name) / "cli-dispatch-consent.json"
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("BUILD_LOOP_CONSENT_SELFTEST", "BUILD_LOOP_CLI_CONSENT_PATH")
        }
        os.environ["BUILD_LOOP_CONSENT_SELFTEST"] = "1"
        os.environ["BUILD_LOOP_CLI_CONSENT_PATH"] = str(self.store_path)

    def tearDown(self) -> None:
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# A. Forged grant by direct file write
# ---------------------------------------------------------------------------

class TestA_ForgedGrant(ConsentAttackTestCase):
    """An attacker who can write the store hand-writes an `auto` entry
    without going through record(). Both a hand-made bad hash and an
    entirely absent hash must be caught — verify_chain must not just be
    "close enough", it must reject any content/hash mismatch."""

    def test_forged_grant_with_bad_hash_is_refused(self) -> None:
        entry = {
            "seq": 0,
            "key": "build-loop:codex",
            "mode": "auto",
            "decided_at": "2026-01-01T00:00:00Z",
            "decided_by": "user",
            "decided_via": "forged",
            "decided_in_repo": "/tmp/attacker",
            "prev_sha256": None,
            "entry_sha256": "deadbeef" * 8,  # hand-made, wrong
        }
        _write_store(self.store_path, [entry])

        chain = cdc.verify_chain(path=self.store_path)
        self.assertFalse(chain["ok"], msg=chain)
        self.assertEqual(chain["broken_at"], 0, msg=chain)

        result = cdc.check("build-loop", "codex", path=self.store_path)
        self.assertFalse(result["allowed"], msg=result)
        self.assertEqual(result["exit"], cdc.EXIT_CHAIN_BROKEN, msg=result)

    def test_forged_grant_with_absent_hash_is_refused(self) -> None:
        entry = {
            "seq": 0,
            "key": "build-loop:codex",
            "mode": "auto",
            "decided_at": "2026-01-01T00:00:00Z",
            "decided_by": "user",
            "decided_via": "forged",
            "decided_in_repo": "/tmp/attacker",
            "prev_sha256": None,
            # entry_sha256 omitted entirely
        }
        _write_store(self.store_path, [entry])

        chain = cdc.verify_chain(path=self.store_path)
        self.assertFalse(chain["ok"], msg=chain)
        self.assertEqual(chain["broken_at"], 0, msg=chain)

        result = cdc.check("build-loop", "codex", path=self.store_path)
        self.assertFalse(result["allowed"], msg=result)
        self.assertEqual(result["exit"], cdc.EXIT_CHAIN_BROKEN, msg=result)


# ---------------------------------------------------------------------------
# B. In-place edit of an existing entry
# ---------------------------------------------------------------------------

class TestB_InPlaceEdit(ConsentAttackTestCase):
    """A legitimate `denied` decision exists. The attacker edits that
    entry's mode to `auto` on disk but leaves its stored entry_sha256
    untouched (the hash the attacker didn't bother, or wasn't able, to
    recompute). verify_chain must localize the break to that exact entry,
    and check() must refuse."""

    def test_edited_entry_breaks_chain_at_that_index_and_check_refuses(self) -> None:
        cdc.record("build-loop", "codex", "denied", path=self.store_path,
                    now="2026-01-01T00:00:00Z", repo="/tmp/legit")

        data = json.loads(self.store_path.read_text())
        self.assertEqual(data["log"][0]["mode"], "denied")
        data["log"][0]["mode"] = "auto"  # flipped; entry_sha256 left stale
        self.store_path.write_text(json.dumps(data, indent=2))

        chain = cdc.verify_chain(path=self.store_path)
        self.assertFalse(chain["ok"], msg=chain)
        self.assertEqual(chain["broken_at"], 0, msg=chain)
        self.assertIn("edited in place", chain["reason"], msg=chain)

        result = cdc.check("build-loop", "codex", path=self.store_path)
        self.assertFalse(result["allowed"], msg=result)
        self.assertEqual(result["exit"], cdc.EXIT_CHAIN_BROKEN, msg=result)


# ---------------------------------------------------------------------------
# C. Truncation / rollback — the case the chain alone cannot catch
# ---------------------------------------------------------------------------

class TestC_TruncationRollback(ConsentAttackTestCase):
    """Record `auto`, then `denied` (the operator revoked the grant). An
    attacker rolls back the revocation by deleting the last log element,
    restoring the log to the state where `auto` was still the last entry
    for that key.

    The truncated log is a valid PREFIX of the original chain: every
    remaining entry's seq/prev_sha256/entry_sha256 still lines up, so
    verify_chain reports ok=True. verify_chain CANNOT see that anything is
    missing — a prefix of a valid chain is itself a valid chain. The only
    thing that reveals the rollback is that the head hash the operator was
    last shown is no longer the head hash after the rollback. This test
    proves exactly that: chain verifies fine, check() actually flips from
    denied back to allowed (the exploit works), and the head hash is the
    one place the discrepancy shows up.
    """

    def test_truncation_defeats_verify_chain_but_changes_head_and_flips_check(self) -> None:
        cdc.record("build-loop", "codex", "auto", path=self.store_path,
                    now="2026-01-01T00:00:00Z", repo="/tmp/legit")
        cdc.record("build-loop", "codex", "denied", path=self.store_path,
                    now="2026-01-02T00:00:00Z", repo="/tmp/legit")

        pre_attack = cdc.check("build-loop", "codex", path=self.store_path)
        self.assertFalse(pre_attack["allowed"], msg=pre_attack)
        self.assertEqual(pre_attack["exit"], cdc.EXIT_DENIED, msg=pre_attack)
        head_before_truncation = cdc.verify_chain(path=self.store_path)["head"]

        # The attack: drop the last log entry (the revocation).
        data = json.loads(self.store_path.read_text())
        self.assertEqual(len(data["log"]), 2)
        data["log"] = data["log"][:-1]
        self.store_path.write_text(json.dumps(data, indent=2))

        chain_after = cdc.verify_chain(path=self.store_path)
        # THE POINT: the chain alone does NOT catch this. A valid prefix of
        # a valid chain verifies as ok=True.
        self.assertTrue(chain_after["ok"], msg=chain_after)

        head_after_truncation = chain_after["head"]
        # THE POINT: the head hash is the only thing that reveals it. It
        # MUST differ from what the operator was shown before the rollback.
        self.assertNotEqual(head_before_truncation, head_after_truncation)

        # THE POINT, sharpest form: the actual security decision flips.
        post_attack = cdc.check("build-loop", "codex", path=self.store_path)
        self.assertTrue(post_attack["allowed"], msg=post_attack)
        self.assertEqual(post_attack["mode"], "auto", msg=post_attack)
        self.assertEqual(post_attack["exit"], cdc.EXIT_ALLOWED, msg=post_attack)


# ---------------------------------------------------------------------------
# D. Full chain recomputation — the strongest same-UID attacker
# ---------------------------------------------------------------------------

class TestD_FullChainRecomputation(ConsentAttackTestCase):
    """The chain is worthless against an attacker who can recompute the
    whole thing from scratch (contract's own words). Simulate that: rebuild
    the entire log with one freshly, correctly hashed `auto` entry.
    verify_chain returns ok=True — it CANNOT catch a wholesale, internally
    self-consistent rebuild, because there is nothing internal to check
    against. Only an external anchor (the head hash the operator was
    previously shown, held outside the log itself) can reveal that anything
    changed. This is why the head hash must be surfaced to the operator on
    every session start and every grant, per the contract's Hashing section.
    """

    def test_full_rebuild_defeats_verify_chain_but_changes_head(self) -> None:
        cdc.record("build-loop", "codex", "denied", path=self.store_path,
                    now="2026-01-01T00:00:00Z", repo="/tmp/legit")
        head_before_attack = cdc.verify_chain(path=self.store_path)["head"]
        pre_attack = cdc.check("build-loop", "codex", path=self.store_path)
        self.assertFalse(pre_attack["allowed"], msg=pre_attack)

        # The attack: same-UID attacker discards the real log and writes an
        # entirely new, internally-consistent one from scratch.
        forged_entry = _valid_entry(0, "build-loop:codex", "auto", None,
                                     decided_via="attacker-rebuilt-from-scratch")
        _write_store(self.store_path, [forged_entry])

        chain_after = cdc.verify_chain(path=self.store_path)
        # THE POINT: verify_chain does NOT catch this. It is a fully valid
        # chain — just not the same chain.
        self.assertTrue(chain_after["ok"], msg=chain_after)

        head_after_attack = chain_after["head"]
        self.assertNotEqual(head_before_attack, head_after_attack)

        post_attack = cdc.check("build-loop", "codex", path=self.store_path)
        self.assertTrue(post_attack["allowed"], msg=post_attack)
        self.assertEqual(post_attack["mode"], "auto", msg=post_attack)


# ---------------------------------------------------------------------------
# E. Store-path redirection
# ---------------------------------------------------------------------------

class TestE_StorePathRedirection(unittest.TestCase):
    """With PYTEST_CURRENT_TEST and BUILD_LOOP_CONSENT_SELFTEST both UNSET,
    store_path() must ignore BUILD_LOOP_CLI_CONSENT_PATH entirely and return
    the fixed ~/.build-loop path. We never read or create the attacker
    file — we only assert on the Path object store_path() returns."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._attacker_path = Path(self._tmp.name) / "attacker-store.json"
        # An attacker-controlled file containing a granted `auto` entry —
        # note we never read this file; its mere existence/content must not
        # matter, because store_path() must never look at the override.
        _write_store(
            self._attacker_path,
            [_valid_entry(0, "build-loop:codex", "auto", None)],
        )
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("PYTEST_CURRENT_TEST", "BUILD_LOOP_CONSENT_SELFTEST",
                      "BUILD_LOOP_CLI_CONSENT_PATH")
        }
        os.environ.pop("PYTEST_CURRENT_TEST", None)
        os.environ.pop("BUILD_LOOP_CONSENT_SELFTEST", None)
        os.environ["BUILD_LOOP_CLI_CONSENT_PATH"] = str(self._attacker_path)

    def tearDown(self) -> None:
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_store_path_ignores_override_outside_test_process(self) -> None:
        self.assertIsNone(os.environ.get("PYTEST_CURRENT_TEST"))
        self.assertIsNone(os.environ.get("BUILD_LOOP_CONSENT_SELFTEST"))
        resolved = cdc.store_path()
        self.assertEqual(resolved, Path.home() / ".build-loop" / "cli-dispatch-consent.json")
        self.assertNotEqual(resolved, self._attacker_path)


# ---------------------------------------------------------------------------
# F. Depth-guard bypass attempts
# ---------------------------------------------------------------------------

class TestF_DepthGuard(ConsentAttackTestCase):

    def test_depth_values_behave_per_contract(self) -> None:
        cases = [
            # (env dict, expected exceeded, note)
            ({"BUILD_LOOP_DISPATCH_DEPTH": "3"}, True, "3 > cap(2): exceeded"),
            ({"BUILD_LOOP_DISPATCH_DEPTH": "abc"}, True,
             "garbage must read as EXCEEDED, not 0 — a garbage value is the "
             "shape a bypass attempt takes"),
            ({"BUILD_LOOP_DISPATCH_DEPTH": ""}, False, "explicit empty -> unset -> 0"),
            ({}, False, "key absent entirely -> unset -> 0"),
            ({"BUILD_LOOP_DISPATCH_DEPTH": "-1"}, True,
             "negative depth buys recursion headroom above the cap (-1 permits four "
             "levels, not two), so it must read as exceeded; this suite found the gap "
             "and the contract was amended to close it rather than assert it away"),
            ({"BUILD_LOOP_DISPATCH_DEPTH": "-99"}, True,
             "any negative value, not just -1"),
            ({"BUILD_LOOP_DISPATCH_DEPTH": "999999999999999999999"}, True,
             "absurdly large integer must still compare > cap"),
        ]
        for env, expected_exceeded, note in cases:
            with self.subTest(env=env, note=note):
                status = cdc.depth_status(env)
                self.assertEqual(status["exceeded"], expected_exceeded, msg=(status, note))

    def test_depth_exceeded_overrides_a_recorded_auto_grant(self) -> None:
        cdc.record("build-loop", "codex", "auto", path=self.store_path,
                    now="2026-01-01T00:00:00Z", repo="/tmp/legit")

        # Sanity: without the depth attack, this key is a clean allow.
        clean = cdc.check("build-loop", "codex", path=self.store_path, env={})
        self.assertTrue(clean["allowed"], msg=clean)

        # THE POINT: depth is checked FIRST and beats a recorded `auto` —
        # no consent answer the operator gave was an answer about recursion.
        attacked = cdc.check(
            "build-loop", "codex", path=self.store_path,
            env={"BUILD_LOOP_DISPATCH_DEPTH": "3"},
        )
        self.assertFalse(attacked["allowed"], msg=attacked)
        self.assertEqual(attacked["exit"], cdc.EXIT_DENIED, msg=attacked)

        # Also true for the garbage-depth bypass attempt.
        attacked_garbage = cdc.check(
            "build-loop", "codex", path=self.store_path,
            env={"BUILD_LOOP_DISPATCH_DEPTH": "not-a-number"},
        )
        self.assertFalse(attacked_garbage["allowed"], msg=attacked_garbage)
        self.assertEqual(attacked_garbage["exit"], cdc.EXIT_DENIED, msg=attacked_garbage)


# ---------------------------------------------------------------------------
# G. Key isolation
# ---------------------------------------------------------------------------

class TestG_KeyIsolation(ConsentAttackTestCase):
    """A grant for build-loop:codex must never leak to any other key —
    not another vendor under the same product, not the same vendor under
    another product."""

    def test_grant_does_not_leak_across_key_boundary(self) -> None:
        cdc.record("build-loop", "codex", "auto", path=self.store_path,
                    now="2026-01-01T00:00:00Z", repo="/tmp/legit")

        granted = cdc.check("build-loop", "codex", path=self.store_path, env={})
        self.assertTrue(granted["allowed"], msg=granted)

        other_keys = [
            ("rally-point", "codex"),
            ("build-loop", "claude"),
            ("build-loop", "cursor"),
            ("build-loop", "ollama"),
        ]
        for product, vendor in other_keys:
            with self.subTest(product=product, vendor=vendor):
                result = cdc.check(product, vendor, path=self.store_path, env={})
                self.assertFalse(result["allowed"], msg=result)
                self.assertEqual(result["exit"], cdc.EXIT_MUST_ASK, msg=result)


# ---------------------------------------------------------------------------
# H. Absence and malformation are never consent
# ---------------------------------------------------------------------------

class TestH_AbsenceAndMalformation(ConsentAttackTestCase):

    KEY = "build-loop:codex"

    def _assert_never_consent(self) -> None:
        result = cdc.check("build-loop", "codex", path=self.store_path, env={})
        self.assertFalse(result["allowed"], msg=result)

    def test_missing_file(self) -> None:
        self.assertFalse(self.store_path.exists())
        self._assert_never_consent()

    def test_empty_file(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text("")
        self._assert_never_consent()

    def test_json_null(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text("null")
        self._assert_never_consent()

    def test_json_empty_list(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text("[]")
        self._assert_never_consent()

    def test_json_log_is_wrong_type(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(json.dumps({"log": "nope"}))
        self._assert_never_consent()

    def test_valid_entry_missing_mode_key_is_never_consent(self) -> None:
        entry = _valid_entry(0, self.KEY, mode="auto", prev_sha256=None, drop_mode=True)
        self.assertNotIn("mode", entry)
        _write_store(self.store_path, [entry])
        # Chain still verifies — the hash covers whatever fields ARE
        # present — but replay() requires mode in MODES to count as a
        # decision at all, so this entry grants nothing.
        chain = cdc.verify_chain(path=self.store_path)
        self.assertTrue(chain["ok"], msg=chain)
        result = cdc.check("build-loop", "codex", path=self.store_path)
        self.assertFalse(result["allowed"], msg=result)
        self.assertEqual(result["exit"], cdc.EXIT_MUST_ASK, msg=result)

    def test_mode_wrong_case_AUTO_is_never_consent(self) -> None:
        entry = _valid_entry(0, self.KEY, mode="AUTO", prev_sha256=None)
        _write_store(self.store_path, [entry])
        self._assert_never_consent()

    def test_mode_trailing_space_is_never_consent(self) -> None:
        entry = _valid_entry(0, self.KEY, mode="auto ", prev_sha256=None)
        _write_store(self.store_path, [entry])
        self._assert_never_consent()


# ---------------------------------------------------------------------------
# I. Mode-string smuggling
# ---------------------------------------------------------------------------

class TestI_ModeStringSmuggling(ConsentAttackTestCase):
    """Entries whose mode is not literally a member of the MODES tuple must
    never grant, whether the near-miss is a string mutation or a non-string
    JSON type. Each entry is correctly hashed so the chain verifies; the
    only thing under test is the MODES membership check in replay()."""

    def test_smuggled_mode_values_never_grant(self) -> None:
        smuggled_modes: list[Any] = ["auto,ask", "aut o", True, 1, None]
        for i, mode in enumerate(smuggled_modes):
            with self.subTest(mode=repr(mode)):
                entry = _valid_entry(0, "build-loop:codex", mode, None)
                _write_store(self.store_path, [entry])

                chain = cdc.verify_chain(path=self.store_path)
                self.assertTrue(chain["ok"], msg=(mode, chain))

                result = cdc.check("build-loop", "codex", path=self.store_path)
                self.assertFalse(result["allowed"], msg=(mode, result))
                self.assertEqual(result["exit"], cdc.EXIT_MUST_ASK, msg=(mode, result))


if __name__ == "__main__":
    unittest.main(verbosity=2)
