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

    def test_ack_inbox_hides_seen_messages_and_surfaces_new_ones(self) -> None:
        slug = channel_paths.app_slug(self.workdir)
        channel = channel_paths.ensure_channel_dir(slug)
        inbox.write_message(
            channel,
            sender="claude_code",
            recipient="codex",
            payload={"summary": "old note"},
            message_id="old-msg",
        )

        ack = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "ack-inbox",
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
        ack_status = json.loads(ack.stdout)
        self.assertEqual(ack_status["action"], "inbox-ack-written")
        self.assertEqual(ack_status["direct_line_count"], 1)

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
        self.assertEqual(status["direct_inbox_unread_count"], 0)
        self.assertEqual(status["inbox_latest_messages"], [])

        inbox.write_message(
            channel,
            sender="claude_code",
            recipient="codex",
            payload={"summary": "new note"},
            message_id="new-msg",
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
        self.assertEqual(status["inbox_latest_messages"][0]["id"], "new-msg")
        self.assertEqual(status["inbox_latest_messages"][0]["preview"], "new note")

    def test_heartbeat_command_writes_status_visible_task_health(self) -> None:
        heartbeat = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "heartbeat",
                "--workdir",
                str(self.workdir),
                "--session-id",
                "me",
                "--tool",
                "codex",
                "--task-ref",
                "claim-1",
                "--progress",
                "ran tests",
                "--evidence",
                "pytest,git diff",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        heartbeat_payload = json.loads(heartbeat.stdout)
        self.assertEqual(heartbeat_payload["action"], "task-heartbeat-written")
        self.assertEqual(heartbeat_payload["task_ref"], "claim-1")

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
                "--task-ref",
                "claim-1",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        status = json.loads(result.stdout)

        self.assertEqual(status["task_heartbeat"]["health"], "current")
        self.assertEqual(
            status["task_heartbeat"]["latest"]["progress_since_last"],
            "ran tests",
        )
        self.assertEqual(
            status["task_heartbeat"]["latest"]["evidence_refs"],
            ["pytest", "git diff"],
        )

    def test_status_post_then_read_roundtrips_the_canonical_pointer(self) -> None:
        posted = subprocess.run(
            [
                sys.executable, str(CLI), "status-post",
                "--workdir", str(self.workdir),
                "--session-id", "me", "--tool", "claude_code",
                "--file", "/abs/path/CURRENT.md",
                "--committed-sha", "abc1234",
                "--summary", "spectra status refreshed",
                "--json",
            ],
            check=True, capture_output=True, text=True, env=os.environ.copy(),
        )
        post_payload = json.loads(posted.stdout)
        self.assertEqual(post_payload["action"], "status-posted")
        self.assertTrue(post_payload["accepted"])

        read = subprocess.run(
            [
                sys.executable, str(CLI), "status-read",
                "--workdir", str(self.workdir), "--json",
            ],
            check=True, capture_output=True, text=True, env=os.environ.copy(),
        )
        status = json.loads(read.stdout)
        self.assertTrue(status["found"])
        self.assertEqual(status["pointer"]["file"], "/abs/path/CURRENT.md")
        self.assertEqual(status["pointer"]["committed_sha"], "abc1234")
        self.assertIn("spectra status refreshed", status["pointer"]["summary"])
        # file+sha are also recoverable from the encoded summary text alone
        self.assertIn("[file=/abs/path/CURRENT.md sha=abc1234]", status["pointer"]["summary"])

    def test_status_read_reports_not_found_when_none_posted(self) -> None:
        read = subprocess.run(
            [
                sys.executable, str(CLI), "status-read",
                "--workdir", str(self.workdir), "--json",
            ],
            check=True, capture_output=True, text=True, env=os.environ.copy(),
        )
        status = json.loads(read.stdout)
        self.assertFalse(status["found"])
        self.assertIsNone(status["latest"])


if __name__ == "__main__":
    unittest.main()
