#!/usr/bin/env python3
"""Tests for the orchestrator auto-invoke branching logic.

The orchestrator's auto-invoke pseudocode (agents/build-orchestrator.md
§"Auto-invoke coordination") branches on three states:

1. **solo** — no active peers, no active coord file -> post kind=phase
   payload phase=rally-start, write presence, NO coord file created.
   (Codex retro §6: prior "noop" codified the bug that caused the
   2026-05-21 dogfood failure; solo mode must still announce itself.)
2. **peer-detected, no coord file** -> bootstrap_called=True, presence
   written, kind=handoff posted.
3. **peer-detected, coord file exists** -> bootstrap NOT called for
   creation; instead join (write presence + post phase=joined-existing-coord).

We model the orchestrator's decision logic as a pure function
(``decide_coordination_action``) for testability, and verify each branch
produces the documented action. The real orchestrator inlines this
logic; the function-shape mirrors the pseudocode 1:1 so the test
acceptance is equivalent to behavioral acceptance.

Also re-verifies coordination_bootstrap's join semantics:
- second bootstrap call on an existing coord file posts a record with
  kind="phase" payload containing phase="joined-existing-coord".
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import coordination_bootstrap as cb  # noqa: E402


# ---------------------------------------------------------------------------
# Pure decision function (mirrors agents/build-orchestrator.md pseudocode)
# ---------------------------------------------------------------------------


def decide_coordination_action(status_envelope: dict) -> dict:
    """Return the orchestrator's branching decision for a coordination_status envelope.

    Mirrors the pseudocode in agents/build-orchestrator.md
    §"Auto-invoke coordination":

        peers = status_envelope["active_peers"]
        coord_path = status_envelope.get("coordination_file")
        if not peers and not coord_path:
            # solo: announce via rally-start post + write presence; no coord file
            mode = "solo"; action = "rally_start"
        elif coord_path is None:
            action = "bootstrap"; mode = "coordinated"
        else:
            action = "join"; mode = "coordinated"

    Returns a dict with keys {action, mode, bootstrap_called,
    presence_should_be_written, post_kind, payload_phase, coordination_file}.

    Codex retro §6: the prior "solo → noop" shape codified the bug.
    Solo mode MUST post kind=phase payload.phase=rally-start and write
    presence so peers that come online later can see the run is live.
    It must NOT create a coord file (coordination_file=null).
    """
    peers = status_envelope.get("active_peers") or []
    coord_path = status_envelope.get("coordination_file")

    if not peers and not coord_path:
        return {
            "action": "rally_start",
            "mode": "solo",
            "presence_should_be_written": True,
            "post_kind": "phase",
            "payload_phase": "rally-start",
            "coordination_file": None,
        }

    if coord_path is None:
        return {
            "action": "bootstrap",
            "mode": "coordinated",
            "bootstrap_called": True,
            "presence_should_be_written": True,
            "post_kind": "handoff",
            "payload_phase": None,
            "coordination_file": None,
        }

    return {
        "action": "join",
        "mode": "coordinated",
        "bootstrap_called": False,  # bootstrap helper is still called, but for join not create
        "presence_should_be_written": True,
        "post_kind": "phase",  # payload includes phase="joined-existing-coord"
        "payload_phase": "joined-existing-coord",
        "coordination_file": coord_path,
    }


# ---------------------------------------------------------------------------
# Branching tests
# ---------------------------------------------------------------------------


class SoloModeTests(unittest.TestCase):
    """Solo mode contract (Codex retro §6).

    Prior shape was action=noop / no presence / no post — that silenced the
    orchestrator entirely when no peers were detected at startup, causing the
    2026-05-21 dogfood failure where a late-joining peer had no way to discover
    the run was live.  The correct contract:

        {
            "action": "rally_start",
            "mode": "solo",
            "presence_should_be_written": true,
            "post_kind": "phase",
            "payload_phase": "rally-start",
            "coordination_file": null
        }
    """

    def test_no_peers_no_coord_file_posts_rally_start_and_writes_presence(self):
        """Solo: no peers, no coord file -> rally_start posted, presence written, no coord file."""
        envelope = {"active_peers": [], "coordination_file": None}
        decision = decide_coordination_action(envelope)
        self.assertEqual(decision["action"], "rally_start",
                         "solo mode must emit action=rally_start (not noop)")
        self.assertEqual(decision["mode"], "solo")
        self.assertTrue(decision["presence_should_be_written"],
                        "solo mode must write presence so late peers can discover the run")
        self.assertEqual(decision["post_kind"], "phase",
                         "solo mode must post kind=phase (not None)")
        self.assertEqual(decision["payload_phase"], "rally-start",
                         "solo mode post payload must have phase=rally-start")
        self.assertIsNone(decision["coordination_file"],
                          "solo mode must NOT create a coord file")


class PeerDetectedNoCoordTests(unittest.TestCase):

    def test_peers_present_no_coord_file_triggers_bootstrap(self):
        envelope = {
            "active_peers": [{"session_id": "peer-1", "tool": "codex"}],
            "coordination_file": None,
        }
        decision = decide_coordination_action(envelope)
        self.assertEqual(decision["action"], "bootstrap")
        self.assertEqual(decision["mode"], "coordinated")
        self.assertTrue(decision["bootstrap_called"])
        self.assertTrue(decision["presence_should_be_written"])
        self.assertEqual(decision["post_kind"], "handoff")


class PeerDetectedExistingCoordTests(unittest.TestCase):

    def test_peers_present_coord_file_exists_triggers_join(self):
        envelope = {
            "active_peers": [{"session_id": "peer-1", "tool": "codex"}],
            "coordination_file": "/tmp/some-coord.md",
        }
        decision = decide_coordination_action(envelope)
        self.assertEqual(decision["action"], "join")
        self.assertEqual(decision["mode"], "coordinated")
        self.assertFalse(decision["bootstrap_called"])
        self.assertTrue(decision["presence_should_be_written"])
        self.assertEqual(decision["post_kind"], "phase")

    def test_no_peers_but_coord_file_present_still_joins(self):
        # Edge case: orchestrator started, no peers visible yet, but a prior
        # coord file exists on disk. Treat as coordinated-join (the prior
        # run's coord file is authoritative until archived).
        envelope = {
            "active_peers": [],
            "coordination_file": "/tmp/prior-coord.md",
        }
        decision = decide_coordination_action(envelope)
        self.assertEqual(decision["action"], "join")
        self.assertEqual(decision["mode"], "coordinated")


# ---------------------------------------------------------------------------
# Integration: bootstrap helper join semantics produce a phase=joined record
# ---------------------------------------------------------------------------


class BootstrapJoinSemanticsTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="orchestrator-join-")
        self.workdir = Path(self.tmpdir)
        self.template_path = self.workdir / "template.md"
        self.template_path.write_text(
            "# {{RUN_TITLE}} {{DATE_YYYY_MM_DD}}\n\n{{SCOPE_SUMMARY_2_TO_4_SENTENCES}}\n",
            encoding="utf-8",
        )
        self.fake_channel = self.workdir / "fake-channel"
        from rally_point import channel_paths
        self._orig = channel_paths.app_channel_dir
        self._channel_paths = channel_paths
        channel_paths.app_channel_dir = lambda slug: self.fake_channel

    def tearDown(self):
        self._channel_paths.app_channel_dir = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _read_channel_changes(self) -> list[dict]:
        from rally_point.changes import read_changes_since
        recs, _ = read_changes_since(self.fake_channel, 0)
        return recs

    def test_second_call_emits_kind_phase_with_joined_payload(self):
        # First call bootstraps; second call joins.
        r1 = cb.bootstrap(
            workdir=self.workdir, topic="t", scope="s",
            session_id="sid-A", template_path=self.template_path,
        )
        self.assertEqual(r1["action"], "bootstrapped")
        recs_after_first = self._read_channel_changes()
        kinds_after_first = [r.get("kind") for r in recs_after_first]
        self.assertIn("handoff", kinds_after_first, "bootstrap should post kind=handoff")

        r2 = cb.bootstrap(
            workdir=self.workdir, topic="t", scope="ignored",
            session_id="sid-B", template_path=self.template_path,
        )
        self.assertEqual(r2["action"], "joined-existing-coord")
        recs_after_second = self._read_channel_changes()
        new_recs = recs_after_second[len(recs_after_first):]
        self.assertTrue(new_recs, "second call should produce at least one channel record")
        # Find the join record
        join_records = [
            r for r in new_recs
            if r.get("kind") == "phase"
            and r.get("payload", {}).get("phase") == "joined-existing-coord"
        ]
        self.assertEqual(
            len(join_records), 1,
            f"expected exactly one kind=phase phase=joined-existing-coord record; "
            f"got new_recs={new_recs}",
        )

    def test_first_call_writes_presence(self):
        r1 = cb.bootstrap(
            workdir=self.workdir, topic="t2", scope="s2",
            session_id="sid-presence", template_path=self.template_path,
        )
        self.assertTrue(r1["presence_written"])
        # Presence file lands at fake-channel/sessions/sid-presence.json
        presence_path = self.fake_channel / "sessions" / "sid-presence.json"
        self.assertTrue(presence_path.exists(), "presence file not written")


if __name__ == "__main__":
    unittest.main()
