#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for the package-level build-loop installer."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "bin" / "build-loop-install.js"


class BuildLoopInstallCliTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node is not installed")

    def test_help_lists_host_and_memory_options(self) -> None:
        result = subprocess.run(
            ["node", str(INSTALLER), "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--host <all|claude|codex>", result.stdout)
        self.assertIn("--skip-memory", result.stdout)

    def test_codex_dry_run_json_succeeds_without_memory_write(self) -> None:
        result = subprocess.run(
            [
                "node",
                str(INSTALLER),
                "--host",
                "codex",
                "--dry-run",
                "--skip-memory",
                "--allow-non-mac",
                "--json",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["host"], "codex")
        self.assertEqual(payload["memory"], "skipped")
        self.assertEqual(payload["steps"][0]["label"], "sync codex plugin cache")
        self.assertTrue(payload["steps"][0]["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
