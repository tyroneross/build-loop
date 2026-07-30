#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Assert channel-consistency invariant in ``coordination_status``.

The v0.12.16 bug: ``coordination_status`` resolved peers/changes via
``discover()`` (canonical path) but read inbox + rejections via
``channel_paths.app_channel_dir(slug)`` (legacy path). Status reported
a channel split silently.

β1 fix: ``_read_inbox_unread_counts`` and ``_read_rejection_count``
now take the resolved ``channel_dir`` directly. Every subcount in the
envelope sources from the SAME ``channel_dir`` value the envelope
advertises.

These tests assert that invariant by writing presence + inbox +
rejection records at distinct fake paths and confirming the status
envelope reports them all from one root.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rally_point import channel_paths, discovery_bridge, inbox  # noqa: E402


class ChannelConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="channel-consistency-"))
        self.workdir = self.tmp / "repo"
        self.workdir.mkdir()
        self._old_apps_root = os.environ.get("BUILD_LOOP_APPS_ROOT")
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.tmp / "apps")
        subprocess.run(
            ["git", "init"], cwd=self.workdir, check=True, capture_output=True
        )
        discovery_bridge.clear_cache()

    def tearDown(self) -> None:
        if self._old_apps_root is None:
            os.environ.pop("BUILD_LOOP_APPS_ROOT", None)
        else:
            os.environ["BUILD_LOOP_APPS_ROOT"] = self._old_apps_root
        shutil.rmtree(self.tmp, ignore_errors=True)
        discovery_bridge.clear_cache()

    def _run_status(self) -> dict:
        """Run ``coordination_status.py --json`` in a stripped env so the
        canonical-vs-legacy resolution is deterministic.

        We strip ``PYTHONPATH`` and ``AGENT_RALLY_DISCOVER`` so the
        bridge falls back to PATH binary or internal — whichever the
        real environment provides. The test asserts consistency
        regardless of which source wins.
        """
        env = {
            k: v for k, v in os.environ.items()
            if k not in {"PYTHONPATH", "AGENT_RALLY_DISCOVER"}
        }
        cmd = [
            "env", "-u", "PYTHONPATH", "-u", "AGENT_RALLY_DISCOVER",
            sys.executable, str(HERE / "coordination_status.py"),
            "--workdir", str(self.workdir),
            "--session-id", "test-session",
            "--tool", "claude_code",
            "--json",
        ]
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=15
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        return json.loads(proc.stdout)

    def test_status_envelope_advertises_one_channel_dir(self) -> None:
        """Envelope MUST carry exactly one ``channel_dir`` field — not
        a list, not a per-subcount path."""
        envelope = self._run_status()
        self.assertIn("channel_dir", envelope)
        self.assertIsInstance(envelope["channel_dir"], str)
        # No legacy split keys should leak into the envelope.
        for forbidden in ("inbox_channel_dir", "rejection_channel_dir",
                          "peers_channel_dir"):
            self.assertNotIn(forbidden, envelope)

    def test_inbox_counts_source_from_advertised_channel(self) -> None:
        """Write an inbox message at the envelope's ``channel_dir`` and
        confirm the count reflects it."""
        envelope_before = self._run_status()
        channel_dir = Path(envelope_before["channel_dir"])
        before = int(envelope_before.get("inbox_unread_count", 0))

        inbox.write_message(
            channel_dir,
            sender="codex",
            recipient="claude_code",
            payload={"test": True},
            kind="message",
        )

        envelope_after = self._run_status()
        # Same channel_dir advertised both times — the status helper is
        # deterministic for a given workdir + env.
        self.assertEqual(
            envelope_after["channel_dir"],
            envelope_before["channel_dir"],
            "channel_dir advertised by status must not flip between polls",
        )
        after = int(envelope_after.get("inbox_unread_count", 0))
        self.assertEqual(after, before + 1)

    def test_rejection_count_sources_from_advertised_channel(self) -> None:
        """Write a fake rejection record at the advertised channel_dir
        and confirm the count picks it up."""
        envelope_before = self._run_status()
        channel_dir = Path(envelope_before["channel_dir"])
        before = int(envelope_before.get("rejection_count", 0))

        rej_file = channel_dir / "rejections.jsonl"
        rej_file.parent.mkdir(parents=True, exist_ok=True)
        with open(rej_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "handoff", "reason": "test"}) + "\n")

        envelope_after = self._run_status()
        after = int(envelope_after.get("rejection_count", 0))
        self.assertEqual(after, before + 1)

    def test_resolved_via_field_present(self) -> None:
        """Status envelope MUST surface ``resolved_via`` so callers can
        tell whether they're on canonical or fallback."""
        envelope = self._run_status()
        self.assertIn("resolved_via", envelope)
        self.assertIn(
            envelope["resolved_via"],
            {"agent-rally-point", "build-loop-internal"},
        )


if __name__ == "__main__":
    unittest.main()
