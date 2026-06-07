#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``scripts/agent_rally.py status``."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rally_point import channel_paths, inbox  # noqa: E402

CLI = HERE / "agent_rally.py"


class AgentRallyStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-rally-status-"))
        self.apps = self.tmp / "apps"
        self.workdir = self.tmp / "repo"
        self.workdir.mkdir()
        self._old_apps = os.environ.get("BUILD_LOOP_APPS_ROOT")
        self._old_internal = os.environ.get("BUILD_LOOP_BRIDGE_INTERNAL_ONLY")
        os.environ["BUILD_LOOP_APPS_ROOT"] = str(self.apps)
        os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = "1"
        subprocess.run(
            ["git", "init", "-q", str(self.workdir)],
            check=True,
            capture_output=True,
        )
        try:
            from rally_point.discovery_bridge import clear_cache

            clear_cache()
        except Exception:
            pass

    def tearDown(self) -> None:
        if self._old_apps is None:
            os.environ.pop("BUILD_LOOP_APPS_ROOT", None)
        else:
            os.environ["BUILD_LOOP_APPS_ROOT"] = self._old_apps
        if self._old_internal is None:
            os.environ.pop("BUILD_LOOP_BRIDGE_INTERNAL_ONLY", None)
        else:
            os.environ["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = self._old_internal
        try:
            from rally_point.discovery_bridge import clear_cache

            clear_cache()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_status_accepts_tool_and_reads_tool_scoped_inbox(self) -> None:
        slug = channel_paths.app_slug(self.workdir)
        channel = channel_paths.ensure_channel_dir(slug)
        inbox.write_message(
            channel,
            sender="claude_code",
            recipient="codex",
            payload={"summary": "codex-only"},
            message_id="codex-msg",
        )
        inbox.write_message(
            channel,
            sender="codex",
            recipient="claude_code",
            payload={"summary": "claude-only"},
            message_id="claude-msg",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "status",
                "--workdir",
                str(self.workdir),
                "--session-id",
                "me",
                "--tool",
                "codex",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        status = json.loads(result.stdout)

        self.assertEqual(status["direct_inbox_unread_count"], 1)
        self.assertEqual(status["inbox_latest_messages"][0]["id"], "codex-msg")
        self.assertEqual(status["inbox_latest_messages"][0]["preview"], "codex-only")


if __name__ == "__main__":
    unittest.main()
