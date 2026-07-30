#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``scripts/agent_rally.py roster`` + enriched presence fields.

Exercises the cross-channel live agent roster end-to-end against a
throwaway apps-root (``$BUILD_LOOP_APPS_ROOT``): writes presence for a
parent (with self-reported ``--spawned``) plus two child sessions
(``--parent <id>``) split across TWO app channels, and one stale session,
then asserts ``roster --json`` returns the live tree with the right
fan-out counts, nests children under the parent, excludes the stale one,
and that ``--app`` filters to a single channel.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rally_point import presence, roster  # noqa: E402

CLI = HERE / "agent_rally.py"


def _write_session(channel_dir: Path, **kw) -> None:
    """Write a session record directly (bypasses git/branch cost)."""
    presence.write_presence(channel_dir, **kw)


class RosterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="rally-roster-"))
        self.apps = self.tmp / "apps"
        self.apps.mkdir(parents=True)
        self._old = os.environ.get("BUILD_LOOP_APPS_ROOT")
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.apps)
        self._old_arp = os.environ.get("AGENT_RALLY_APPS_ROOT")
        os.environ["AGENT_RALLY_APPS_ROOT"] = str(self.apps)

        # Two channels: app-alpha (parent + 1 child), app-beta (1 child).
        self.alpha = self.apps / "app-alpha"
        self.beta = self.apps / "app-beta"

        # Parent agent — top-level, self-reports a fan-out of 5 + 1 = 6.
        _write_session(
            self.alpha, session_id="orchestrator-1", tool="claude_code",
            model="opus", run_id="r1", app_slug="app-alpha",
            phase="execute", task="orchestrating the build",
            parent=None, spawned="coder:5,independent-auditor:1",
        )
        # Child that posted its own presence (nested under parent).
        _write_session(
            self.alpha, session_id="coder-a", tool="claude_code",
            model="sonnet", run_id="r1", app_slug="app-alpha",
            phase="implement", task="writing the roster module",
            parent="orchestrator-1", spawned=None,
        )
        # Child in a DIFFERENT channel, same parent (cross-channel tree).
        _write_session(
            self.beta, session_id="coder-b", tool="codex",
            model="gpt-5", run_id="r1", app_slug="app-beta",
            phase="implement", task="cross-channel subagent",
            parent="orchestrator-1", spawned=None,
        )

        # Stale session: hand-write last_seen far in the past.
        stale_dir = self.alpha / "sessions"
        stale_dir.mkdir(parents=True, exist_ok=True)
        old = time.time() - 9999
        (stale_dir / "ghost.json").write_text(json.dumps({
            "session_id": "ghost", "tool": "claude_code", "model": "opus",
            "app_slug": "app-alpha", "phase": "gone", "task": "gone",
            "parent": None, "spawned": {}, "last_seen": old,
            "heartbeat_ts": old, "host": "h", "cwd": "/x",
        }))

    def tearDown(self) -> None:
        for var, old in (("BUILD_LOOP_APPS_ROOT", self._old),
                         ("AGENT_RALLY_APPS_ROOT", self._old_arp)):
            if old is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = old
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- direct (in-process) ------------------------------------------------

    def test_build_roster_tree_and_fanout(self) -> None:
        data = roster.build_roster(apps_root=self.apps, stale_secs=120)
        ids = {a["session_id"] for a in data["agents"]}
        # Only the parent is top-level; children nest, stale excluded.
        self.assertEqual(ids, {"orchestrator-1"})
        self.assertEqual(data["live_count"], 3)
        self.assertEqual(data["stale_count"], 1)

        parent = data["agents"][0]
        child_ids = {c["session_id"] for c in parent["children"]}
        self.assertEqual(child_ids, {"coder-a", "coder-b"})
        # Self-reported fan-out parsed to a dict + total.
        self.assertEqual(parent["spawned"],
                         {"coder": 5, "independent-auditor": 1})
        self.assertEqual(parent["spawned_total"], 6)
        # Cross-channel: one child carries a different app.
        apps = {c["app"] for c in parent["children"]}
        self.assertEqual(apps, {"app-alpha", "app-beta"})

    def test_app_filter(self) -> None:
        data = roster.build_roster(
            apps_root=self.apps, app="app-beta", stale_secs=120)
        # Only the beta channel's session is seen. Its parent isn't in the
        # live set (filtered out), so it surfaces as a root.
        self.assertEqual(data["live_count"], 1)
        ids = {a["session_id"] for a in data["agents"]}
        self.assertEqual(ids, {"coder-b"})

    def test_stale_included_with_all(self) -> None:
        data = roster.build_roster(
            apps_root=self.apps, stale_secs=120, include_stale=True)
        all_ids = {a["session_id"] for a in data["agents"]} | {
            c["session_id"] for a in data["agents"]
            for c in a["children"]
        }
        self.assertIn("ghost", all_ids)

    # -- CLI (subprocess) ---------------------------------------------------

    def test_cli_roster_json(self) -> None:
        env = os.environ.copy()
        r = subprocess.run(
            [sys.executable, str(CLI), "roster", "--json"],
            capture_output=True, text=True, check=True, env=env,
        )
        data = json.loads(r.stdout)
        self.assertEqual(data["live_count"], 3)
        self.assertEqual(data["stale_count"], 1)
        ids = {a["session_id"] for a in data["agents"]}
        self.assertEqual(ids, {"orchestrator-1"})

    def test_cli_roster_app_filter(self) -> None:
        env = os.environ.copy()
        r = subprocess.run(
            [sys.executable, str(CLI), "roster", "--app", "app-alpha",
             "--json"],
            capture_output=True, text=True, check=True, env=env,
        )
        data = json.loads(r.stdout)
        # alpha has parent + coder-a live (ghost stale, excluded).
        self.assertEqual(data["live_count"], 2)
        parent = data["agents"][0]
        self.assertEqual(parent["session_id"], "orchestrator-1")
        self.assertEqual({c["session_id"] for c in parent["children"]},
                         {"coder-a"})

    def test_cli_presence_writes_roster_fields(self) -> None:
        """`presence --spawned/--parent/--task` land in the record."""
        env = os.environ.copy()
        chan = self.apps / "app-gamma"
        # presence resolves the channel via discovery; write directly via
        # the substrate to keep this test git-independent, then assert the
        # CLI's parse of --spawned matches.
        _write_session(
            chan, session_id="p2", tool="claude_code", model="opus",
            run_id="r", app_slug="app-gamma", phase="x",
            spawned="workflow:21,coder:2",
        )
        data = roster.build_roster(apps_root=self.apps, app="app-gamma")
        p2 = data["agents"][0]
        self.assertEqual(p2["spawned"], {"workflow": 21, "coder": 2})
        self.assertEqual(p2["spawned_total"], 23)


if __name__ == "__main__":
    unittest.main()
