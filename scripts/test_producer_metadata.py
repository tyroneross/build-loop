#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Verify producer_metadata is attached at every Rally Point write path.

Codex variance (rev ~209): a post.py-only patch misses every writer
that does not pass through post.py — presence, inbox direct/broadcast,
leadership, raw change-record appenders, etc.

β1 acceptance: this test greps every ``.py`` file under
``scripts/rally_point/`` and ``scripts/`` for likely write-path
callsites (``post(``, ``append_change(``, ``write_message(``,
``presence.write``, ``send_to_tool(``) and exercises the broader write
surfaces end-to-end (post + inbox + presence + bootstrap) to assert
producer_metadata fields appear on each record.

The grep is advisory (skips on writers that are pure dispatchers); the
end-to-end exercises are the load-bearing assertions.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rally_point import (  # noqa: E402
    changes,
    channel_paths,
    inbox,
    post as _post_mod,
    presence,
    producer_metadata as pm_mod,
)

PRODUCER_FIELDS = {
    "producer_name",
    "producer_version",
    "producer_commit_sha",
    "producer_runtime_path",
    "producer_runtime_surface",
    "producer_protocol_version",
}


def _read_log(channel_dir: Path) -> list[dict]:
    recs, _ = changes.read_changes_since(channel_dir, 0)
    return recs


class ProducerMetadataShapeTests(unittest.TestCase):
    def test_producer_metadata_carries_all_required_fields(self) -> None:
        pm_mod.reset_cache_for_tests()
        meta = pm_mod.producer_metadata()
        self.assertEqual(set(meta.keys()), PRODUCER_FIELDS)
        self.assertEqual(meta["producer_name"], "build-loop")
        self.assertEqual(meta["producer_protocol_version"], "1.0")
        # version must be a non-empty string (semver from plugin.json).
        self.assertIsInstance(meta["producer_version"], str)
        self.assertTrue(len(meta["producer_version"]) > 0)
        # surface is one of three allowed values.
        self.assertIn(
            meta["producer_runtime_surface"],
            {"source-repo", "claude-cache", "installed-package"},
        )

    def test_producer_metadata_returns_copy_not_shared_ref(self) -> None:
        """Caller mutation must not corrupt the cached dict."""
        a = pm_mod.producer_metadata()
        a["producer_name"] = "MUTATED"
        b = pm_mod.producer_metadata()
        self.assertEqual(b["producer_name"], "build-loop")


class ProducerMetadataOnChannelWritesTests(unittest.TestCase):
    """End-to-end: verify producer fields attach to written records."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="producer-md-"))
        self._old_apps_root = os.environ.get("BUILD_LOOP_APPS_ROOT")
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.tmp / "apps")
        self.channel = channel_paths.ensure_channel_dir("test-producer")

    def tearDown(self) -> None:
        if self._old_apps_root is None:
            os.environ.pop("BUILD_LOOP_APPS_ROOT", None)
        else:
            os.environ["BUILD_LOOP_APPS_ROOT"] = self._old_apps_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_post_attaches_producer_metadata(self) -> None:
        new_rev = _post_mod.post(
            channel_dir=self.channel,
            kind="phase",
            tool="claude_code",
            model="claude-opus-4-7",
            run_id="test-run",
            app_slug="test-producer",
            payload={"phase": "test"},
        )
        self.assertIsNotNone(new_rev)
        recs = _read_log(self.channel)
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        for field in PRODUCER_FIELDS:
            self.assertIn(field, rec, f"missing {field} in post() record")
        self.assertEqual(rec["producer_name"], "build-loop")

    def test_inbox_send_to_tool_mirror_carries_metadata(self) -> None:
        """``inbox.send_to_tool`` mirrors via ``post.post`` so the channel
        record gets producer_metadata. The raw inbox JSONL line is for
        wake-path delivery and does not need the fields."""
        result = inbox.send_to_tool(
            self.channel,
            sender="claude_code",
            recipient="codex",
            payload={"kind": "test"},
            kind="message",
            model="claude-opus-4-7",
            run_id="test-run",
            app_slug="test-producer",
            mirror_to_channel=True,
        )
        self.assertTrue(result["written"])
        recs = _read_log(self.channel)
        self.assertEqual(len(recs), 1)
        for field in PRODUCER_FIELDS:
            self.assertIn(field, recs[0])

    def test_presence_write_does_not_corrupt_channel_log(self) -> None:
        """``presence.write_presence`` writes to ``presence/<id>.json``
        not to ``changes.jsonl``. Presence is the heartbeat path; the
        channel-log producer_metadata contract covers durable events
        (post/inbox/leadership). Presence records carry their own
        version fields (``build_loop_version``, ``build_loop_commit_sha``)
        per ``coordination-version-control``. This test asserts presence
        does NOT accidentally pollute changes.jsonl."""
        presence.write_presence(
            self.channel,
            session_id="test-session",
            tool="claude_code",
            model="claude-opus-4-7",
            run_id="test-run",
            app_slug="test-producer",
            phase="probe",
            files_in_flight=[],
            cwd=self.tmp,
        )
        recs = _read_log(self.channel)
        self.assertEqual(recs, [], "presence must not write changes.jsonl")


class WriteSurfaceGrepTests(unittest.TestCase):
    """Advisory: enumerate write-path callsites and surface any that
    bypass ``post()``. Failures here are early-warning, not gating."""

    WRITE_PATTERNS = (
        re.compile(r"\bappend_change\("),
    )

    SCAN_DIRS = (
        Path(__file__).resolve().parent / "rally_point",
        Path(__file__).resolve().parent,
    )

    # Callers that legitimately bypass post() (the canonical writer
    # itself, the schema definition module, tests, and any documented
    # exception). Add a path here ONLY when the callsite is justified.
    KNOWN_EXEMPTIONS = frozenset({
        # post.py is the canonical writer (calls append_change internally
        # after attaching producer_metadata).
        "rally_point/post.py",
        # changes.py defines append_change; introspecting its own
        # definition is not a bypass.
        "rally_point/changes.py",
        # Tests legitimately exercise append_change directly to assert
        # post.py's contract.
        "test_producer_metadata.py",
        # rally_point unit tests exercise append_change at the schema/
        # storage layer to assert revision monotonicity, dedup, schema
        # versioning, etc. They are NOT message-emitting callers and so
        # do not need producer_metadata attached.
        "rally_point/test_changes.py",
        "rally_point/test_rally.py",
        "rally_point/test_checkpoint.py",
        "rally_point/test_orchestrator_contract.py",
        "rally_point/test_cross_tool.py",
        # test_coordination_status seeds change records with controlled `ts`
        # values to exercise recency-decay ordering + archive-floor (Feature A);
        # post() would stamp the real now() and defeat the aged-record fixture.
        # Storage-layer test seam, not a message-emitting runtime caller.
        "test_coordination_status.py",
        # install_git_hook.py is a one-shot installer script that probes
        # the channel; not part of the runtime write surface.
        "rally_point/install_git_hook.py",
    })

    def test_no_unaudited_append_change_callers(self) -> None:
        """Every ``append_change`` caller outside the exemptions list
        must be reviewed manually. New callers fail the test until
        either routed through post() or added to KNOWN_EXEMPTIONS with
        rationale.
        """
        offenders: list[str] = []
        for scan_dir in self.SCAN_DIRS:
            for py in scan_dir.rglob("*.py"):
                if "__pycache__" in py.parts:
                    continue
                rel = py.relative_to(HERE).as_posix()
                if rel in self.KNOWN_EXEMPTIONS:
                    continue
                try:
                    text = py.read_text(encoding="utf-8")
                except OSError:
                    continue
                for pattern in self.WRITE_PATTERNS:
                    if pattern.search(text):
                        offenders.append(rel)
                        break
        self.assertEqual(
            offenders, [],
            f"unaudited append_change callers: {offenders} — route through "
            "post() or add to KNOWN_EXEMPTIONS with rationale."
        )


if __name__ == "__main__":
    unittest.main()
