#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for dogfood_reload_checkpoint.py."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "dogfood_reload_checkpoint.py"


def run_checkpoint(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


class DogfoodReloadCheckpointTests(unittest.TestCase):
    def test_detect_runtime_changing_surfaces(self) -> None:
        result = run_checkpoint(
            "detect",
            "--changed-file", "skills/build-loop/SKILL.md",
            "--changed-file", "scripts/rally_point/task_heartbeat.py",
            "--changed-file", "scripts/dogfood_reload_checkpoint.py",
            "--changed-file", "docs/readme.md",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["runtime_change_required"])
        self.assertIn("skill", payload["surfaces"])
        self.assertIn("rally", payload["surfaces"])
        self.assertIn("coordination", payload["surfaces"])
        self.assertNotIn("docs/readme.md", json.dumps(payload["matched_files"]))

    def test_checkpoint_waits_for_expected_acks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            create = run_checkpoint(
                "create",
                "--workdir", str(root),
                "--checkpoint-id", "cp1",
                "--commit", "abc1234",
                "--branch", "main",
                "--changed-file", "agents/build-orchestrator.md",
                "--expect-tool", "codex",
                "--expect-tool", "claude_code",
                "--instructions", "reload before continuing",
            )
            self.assertEqual(create.returncode, 0, create.stderr)
            payload = json.loads(create.stdout)
            self.assertEqual(payload["status"], "waiting_for_ack")

            ack = run_checkpoint(
                "ack",
                "--workdir", str(root),
                "--checkpoint-id", "cp1",
                "--tool", "codex",
                "--session-id", "s1",
                "--runtime-root", str(root),
                "--runtime-commit", "abc1234",
                "--reload-method", "source-cli",
                "--rally-next-status", "proceed_solo",
            )
            self.assertEqual(ack.returncode, 0, ack.stderr)
            ack_payload = json.loads(ack.stdout)
            self.assertFalse(ack_payload["ready"])
            self.assertEqual(ack_payload["missing_tools"], ["claude_code"])

            fallback = run_checkpoint(
                "fallback",
                "--workdir", str(root),
                "--checkpoint-id", "cp1",
                "--tool", "claude_code",
                "--decision", "continue_solo",
                "--reason", "lead lease expired and no active peer",
            )
            self.assertEqual(fallback.returncode, 0, fallback.stderr)
            fallback_payload = json.loads(fallback.stdout)
            self.assertTrue(fallback_payload["ready"])
            self.assertEqual(fallback_payload["fallback_tools"], ["claude_code"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
