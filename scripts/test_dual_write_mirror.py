#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Test suite: β1.2 dual-write mirror in post.py + inbox.py.

Covers the migration-window dual-write contract: while the discovery
bridge reports ``policy: "migration"`` with a populated
``legacy_channel_dir``, every canonical write is mirrored to the legacy
channel so non-upgraded peers (Codex's LaunchAgent poller still on the
legacy channel) stay visible.

Acceptance criteria (matches β1.2 brief):
  AC-B1.2-1: post(channel_dir=canonical, workdir=W, kind=handoff, ...)
             during migration writes the record to BOTH canonical AND
             legacy.
  AC-B1.2-2: When policy != "migration", no mirror occurs.
  AC-B1.2-3: Mirror failure does NOT raise; canonical write succeeds
             independently and returns its valid revision.
  AC-B1.2-4: inbox.write_message(channel_dir=canonical, workdir=W, ...)
             mirrors to legacy during migration.

Run under ``env -u PYTHONPATH`` per smoke-test-rigging memory.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Allow `python3 scripts/test_dual_write_mirror.py` from repo root.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rally_point import changes as _changes  # noqa: E402
from rally_point import discovery_bridge as bridge  # noqa: E402
from rally_point import inbox  # noqa: E402
from rally_point.post import post  # noqa: E402


def _read_changes(channel_dir: Path) -> list[dict]:
    # Read through the canonical normalize chokepoint. The canonical channel
    # now stores the agent-rally.fact.v1 shape (top-level revision/kind/run_id
    # live under bl_revision/bl_kind/thread_id until normalized); the legacy
    # mirror stores the already-legacy reader shape (normalize_record is a
    # pass-through for it). Reading raw json here would assert an obsolete
    # pre-fact.v1 canonical shape — normalize is the contract every real reader
    # (rally.py, leadership.py, checkpoint.py) uses.
    p = channel_dir / "changes.jsonl"
    if not p.exists():
        return []
    return [
        _changes.normalize_record(json.loads(l))
        for l in p.read_text().splitlines()
        if l.strip()
    ]


def _read_changes_raw(channel_dir: Path) -> list[dict]:
    # Raw on-disk reader — NO normalize. Guards the literal record shape each
    # channel persists, so the legacy-mirror assertions catch a regression that
    # reverts the mirror to a fact.v1 line (which normalize would silently
    # launder back into the legacy reader shape, masking the f3 contract break).
    p = channel_dir / "changes.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _read_inbox_raw(channel_dir: Path, tool: str) -> list[dict]:
    # Raw on-disk inbox reader (same contract as _read_inbox, named for
    # parity with _read_changes_raw / intent at the call site).
    p = inbox.inbox_path(channel_dir, tool)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _read_revision(channel_dir: Path) -> int:
    p = channel_dir / "revision"
    if not p.exists():
        return 0
    try:
        return int(p.read_text().strip())
    except ValueError:
        return 0


def _read_inbox(channel_dir: Path, tool: str) -> list[dict]:
    p = inbox.inbox_path(channel_dir, tool)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _good_handoff_payload() -> dict:
    return {
        "session_id": "s-test",
        "ownership": {
            "owns": ["scripts/rally_point/post.py"],
            "does_not_own": ["scripts/rally_point/rally.py"],
            "allowed_tools": [],
            "denied_tools": [],
            "interface_contract": "post() mirrors to legacy during migration",
            "integration_checkpoint": "test_dual_write_mirror.py exit 0",
        },
    }


class DualWriteMirrorBase(unittest.TestCase):
    """Shared fixture: tmp canonical + legacy channels, fake discover envelope.

    Each test patches ``discovery_bridge.resolve`` so the bridge returns a
    chosen ``DiscoveryEnvelope`` (migration vs canonical vs no-legacy)
    without needing a real ``agent-rally-discover`` binary on PATH.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.canonical = self.tmp / "canonical"
        self.legacy = self.tmp / "legacy"
        self.canonical.mkdir(parents=True, exist_ok=True)
        self.legacy.mkdir(parents=True, exist_ok=True)
        self.workdir = self.tmp / "workdir"
        self.workdir.mkdir(parents=True, exist_ok=True)
        bridge.clear_cache()

    def tearDown(self) -> None:
        self._tmp.cleanup()
        bridge.clear_cache()

    # Sentinel: distinguishes "caller didn't supply legacy_channel_dir" (default
    # to self.legacy) from "caller explicitly wants legacy_channel_dir=None"
    # (e.g. test_post_no_mirror_when_legacy_dir_missing).
    _LEGACY_DEFAULT = object()

    def _envelope(
        self,
        *,
        policy: str = "migration",
        legacy_channel_dir: object = _LEGACY_DEFAULT,
        coordination_unavailable: str | None = None,
    ) -> bridge.DiscoveryEnvelope:
        if legacy_channel_dir is self._LEGACY_DEFAULT:
            resolved_legacy: str | None = str(self.legacy)
        elif legacy_channel_dir is None:
            resolved_legacy = None
        else:
            resolved_legacy = str(legacy_channel_dir)
        return bridge.DiscoveryEnvelope(
            channel_dir=str(self.canonical),
            app_slug="test-app",
            repo_id="test-app",
            channel_layout="canonical",
            policy=policy,
            protocol_version="1.0",
            last_resolved_at="2026-05-25T00:00:00Z",
            resolved_via="python-import",
            legacy_channel_dir=resolved_legacy,
            merged_view=(policy == "migration"),
            coordination_unavailable=coordination_unavailable,
            raw={},
        )


class TestPostDualWrite(DualWriteMirrorBase):
    # ------------------------------------------------------------------
    # AC-B1.2-1: migration policy → mirror to legacy
    # ------------------------------------------------------------------
    def test_post_mirrors_to_legacy_during_migration(self) -> None:
        env = self._envelope(policy="migration")
        with patch("rally_point.discovery_bridge.resolve", return_value=env):
            rev = post(
                channel_dir=self.canonical,
                kind="handoff",
                tool="claude_code",
                model="opus-4-7",
                run_id="r-test-1",
                app_slug="test-app",
                payload=_good_handoff_payload(),
                workdir=self.workdir,
            )
        self.assertIsNotNone(rev, "Canonical post must succeed and return a revision")
        canonical_changes = _read_changes(self.canonical)
        legacy_changes = _read_changes(self.legacy)
        self.assertEqual(len(canonical_changes), 1, "Canonical channel has 1 record")
        self.assertEqual(len(legacy_changes), 1, "Legacy channel mirror has 1 record")
        self.assertEqual(canonical_changes[0]["revision"], rev)
        # Legacy revision file also bumped so its readers see a fresh signal.
        self.assertEqual(_read_revision(self.legacy), 1)
        # Records carry the same kind + run_id (mirror is bit-identical).
        self.assertEqual(canonical_changes[0]["kind"], legacy_changes[0]["kind"])
        self.assertEqual(canonical_changes[0]["run_id"], legacy_changes[0]["run_id"])

        # f3 contract guard (raw, NO normalize): raw legacy peers cannot read a
        # fact.v1 line — they KeyError on a missing top-level revision/kind/run_id.
        # The mirror MUST therefore write the down-converted legacy reader shape
        # directly. Asserting through normalize would pass either way (normalize
        # reconstructs these from a fact.v1 line too), so this reads RAW.
        legacy_raw = _read_changes_raw(self.legacy)
        self.assertEqual(len(legacy_raw), 1, "Raw legacy mirror has 1 on-disk record")
        lr = legacy_raw[0]
        # Legacy shape carries the contract fields at the TOP LEVEL, on disk.
        self.assertEqual(lr["revision"], rev, "raw legacy record has top-level revision")
        self.assertEqual(lr["kind"], "handoff", "raw legacy record has top-level kind")
        self.assertEqual(lr["run_id"], "r-test-1", "raw legacy record has top-level run_id")
        # NOT a fact.v1 line: a regression to write_fact_v1_line would carry the
        # FACT_V1_SCHEMA marker and stash the contract fields under bl_revision/
        # bl_kind/thread_id instead. Assert the fact.v1 carriers are ABSENT so a
        # fact.v1-shaped mirror FAILS this test.
        self.assertNotIn("schema", lr, "raw legacy record is NOT a fact.v1 line")
        self.assertNotIn("bl_revision", lr, "revision is top-level, not a fact.v1 carrier")
        self.assertNotIn("thread_id", lr, "run_id is top-level, not a fact.v1 carrier")
        # Sanity: the CANONICAL channel intentionally IS fact.v1 on disk — proves
        # the assertions above target the down-conversion, not a no-op equality.
        canonical_raw = _read_changes_raw(self.canonical)
        self.assertEqual(
            canonical_raw[0].get("schema"),
            _changes.FACT_V1_SCHEMA,
            "canonical channel stores the fact.v1 shape on disk",
        )

    # ------------------------------------------------------------------
    # AC-B1.2-2: non-migration policy → no mirror
    # ------------------------------------------------------------------
    def test_post_no_mirror_when_policy_canonical(self) -> None:
        env = self._envelope(policy="canonical")
        with patch("rally_point.discovery_bridge.resolve", return_value=env):
            rev = post(
                channel_dir=self.canonical,
                kind="handoff",
                tool="claude_code",
                model="opus-4-7",
                run_id="r-test-2",
                app_slug="test-app",
                payload=_good_handoff_payload(),
                workdir=self.workdir,
            )
        self.assertIsNotNone(rev)
        self.assertEqual(len(_read_changes(self.canonical)), 1)
        # Legacy must remain empty — no mirror under canonical policy.
        self.assertEqual(len(_read_changes(self.legacy)), 0)
        self.assertEqual(_read_revision(self.legacy), 0)

    # ------------------------------------------------------------------
    # Edge: migration policy but legacy_channel_dir missing → no crash, no mirror
    # ------------------------------------------------------------------
    def test_post_no_mirror_when_legacy_dir_missing(self) -> None:
        env = self._envelope(policy="migration", legacy_channel_dir=None)
        with patch("rally_point.discovery_bridge.resolve", return_value=env):
            rev = post(
                channel_dir=self.canonical,
                kind="handoff",
                tool="claude_code",
                model="opus-4-7",
                run_id="r-test-3",
                app_slug="test-app",
                payload=_good_handoff_payload(),
                workdir=self.workdir,
            )
        self.assertIsNotNone(rev, "Canonical write still succeeds when no legacy dir")
        self.assertEqual(len(_read_changes(self.canonical)), 1)
        # Legacy must remain empty — no dir to mirror to.
        # Note: self.legacy still exists as an empty tmpdir; the bridge
        # returned legacy_channel_dir=None so the mirror branch is skipped.
        legacy_records = _read_changes(self.legacy)
        self.assertEqual(
            len(legacy_records), 0,
            f"Legacy must remain empty when bridge returns None legacy_channel_dir; "
            f"got: {legacy_records}",
        )

    # ------------------------------------------------------------------
    # AC-B1.2-3: mirror failure does NOT block canonical write
    # ------------------------------------------------------------------
    def test_mirror_failure_does_not_block_canonical(self) -> None:
        # Point legacy at a path the writer cannot use — a regular file
        # where it expects a directory. The mkdir-then-write path will
        # raise, but the canonical write above must still succeed.
        broken_legacy = self.tmp / "broken-as-file"
        broken_legacy.write_text("not a directory")
        env = self._envelope(policy="migration", legacy_channel_dir=broken_legacy)
        with patch("rally_point.discovery_bridge.resolve", return_value=env):
            rev = post(
                channel_dir=self.canonical,
                kind="handoff",
                tool="claude_code",
                model="opus-4-7",
                run_id="r-test-4",
                app_slug="test-app",
                payload=_good_handoff_payload(),
                workdir=self.workdir,
            )
        # Canonical succeeded — mirror failure swallowed per fire-and-forget.
        self.assertIsNotNone(rev, "Canonical write succeeds despite mirror failure")
        self.assertEqual(len(_read_changes(self.canonical)), 1)
        self.assertEqual(_read_changes(self.canonical)[0]["revision"], rev)

    # ------------------------------------------------------------------
    # Edge: workdir omitted entirely → bridge never consulted, no mirror
    # ------------------------------------------------------------------
    def test_post_without_workdir_is_canonical_only(self) -> None:
        with patch("rally_point.discovery_bridge.resolve") as mock_resolve:
            rev = post(
                channel_dir=self.canonical,
                kind="handoff",
                tool="claude_code",
                model="opus-4-7",
                run_id="r-test-5",
                app_slug="test-app",
                payload=_good_handoff_payload(),
            )
        self.assertIsNotNone(rev)
        # Bridge never invoked when workdir is None — proves no implicit resolve.
        mock_resolve.assert_not_called()
        self.assertEqual(len(_read_changes(self.canonical)), 1)
        self.assertEqual(len(_read_changes(self.legacy)), 0)


class TestInboxDualWrite(DualWriteMirrorBase):
    # ------------------------------------------------------------------
    # AC-B1.2-4: inbox.write_message mirrors to legacy during migration
    # ------------------------------------------------------------------
    def test_inbox_write_mirrors_to_legacy(self) -> None:
        env = self._envelope(policy="migration")
        with patch("rally_point.discovery_bridge.resolve", return_value=env):
            inbox.write_message(
                self.canonical,
                sender="claude_code",
                recipient="codex",
                payload={"checkpoint_id": "B1_2_TEST"},
                kind="phase",
                workdir=self.workdir,
            )
        canonical_inbox = _read_inbox(self.canonical, "codex")
        legacy_inbox = _read_inbox(self.legacy, "codex")
        self.assertEqual(len(canonical_inbox), 1, "Canonical inbox has 1 message")
        self.assertEqual(len(legacy_inbox), 1, "Legacy inbox mirror has 1 message")
        self.assertEqual(canonical_inbox[0]["payload"], legacy_inbox[0]["payload"])
        self.assertEqual(canonical_inbox[0]["id"], legacy_inbox[0]["id"])

        # f3 contract guard (raw, NO normalize): the inbox mirror writes the
        # SAME message bytes to both channels, so the legacy inbox record MUST be
        # the legacy inbox message shape on disk — top-level id/payload/kind, NOT
        # a fact.v1 line. _read_inbox already reads raw json; _read_inbox_raw is
        # named at the call site to make the on-disk-shape intent explicit.
        legacy_raw = _read_inbox_raw(self.legacy, "codex")
        self.assertEqual(len(legacy_raw), 1, "Raw legacy inbox mirror has 1 on-disk record")
        lr = legacy_raw[0]
        self.assertIn("id", lr, "raw legacy inbox record has top-level id")
        self.assertIn("payload", lr, "raw legacy inbox record has top-level payload")
        self.assertEqual(lr["kind"], "phase", "raw legacy inbox record has top-level kind")
        self.assertEqual(lr["payload"], {"checkpoint_id": "B1_2_TEST"})
        # NOT a fact.v1 line: a regression that routed the inbox mirror through
        # the fact.v1 emitter would carry FACT_V1_SCHEMA and stash fields under
        # bl_*; assert those carriers are absent so such a regression FAILS here.
        self.assertNotIn("schema", lr, "raw legacy inbox record is NOT a fact.v1 line")
        self.assertNotIn("bl_revision", lr, "inbox fields are top-level, not fact.v1 carriers")

    def test_inbox_write_no_mirror_when_canonical(self) -> None:
        env = self._envelope(policy="canonical")
        with patch("rally_point.discovery_bridge.resolve", return_value=env):
            inbox.write_message(
                self.canonical,
                sender="claude_code",
                recipient="codex",
                payload={"checkpoint_id": "no-mirror"},
                kind="phase",
                workdir=self.workdir,
            )
        self.assertEqual(len(_read_inbox(self.canonical, "codex")), 1)
        self.assertEqual(len(_read_inbox(self.legacy, "codex")), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
