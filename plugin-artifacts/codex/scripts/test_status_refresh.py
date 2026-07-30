#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for status_refresh.py."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "status_refresh.py"

CURRENT_TEMPLATE = """# Sample — Current Status (canonical)

- **as_of_commit:** `{sha}` (main)
- **last_verified_at:** 2026-01-01T00:00Z

## Current open work (ranked)
1. **Async thing** — do it
2. Reconcile docs

## Validation evidence (re-run to re-verify)
```bash
cd repo
npm test
```

## Links
- whatever
"""


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], check=False, capture_output=True, text=True)


class StatusRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workdir = self.root / "sample-repo"
        self.workdir.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workdir)], check=True)
        subprocess.run(["git", "-C", str(self.workdir), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(self.workdir), "config", "user.name", "t"], check=True)
        (self.workdir / "f.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.workdir), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-qm", "init"], check=True)
        self.head = subprocess.run(
            ["git", "-C", str(self.workdir), "rev-parse", "--short", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.memory = self.root / "memory"
        status_dir = self.memory / "projects" / "sample-repo" / "status"
        status_dir.mkdir(parents=True)
        self.current = status_dir / "CURRENT.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, sha: str) -> None:
        self.current.write_text(CURRENT_TEMPLATE.format(sha=sha), encoding="utf-8")

    def test_detects_stale_when_as_of_behind_head(self) -> None:
        self._write("deadbee")
        r = run("--workdir", str(self.workdir), "--memory-root", str(self.memory), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        p = json.loads(r.stdout)
        self.assertTrue(p["ok"])
        self.assertTrue(p["stale"])
        self.assertEqual(p["head"], self.head)
        self.assertEqual(p["as_of_commit"], "deadbee")
        self.assertIn("npm test", p["validation_commands"])

    def test_stamp_updates_as_of_to_head(self) -> None:
        self._write("deadbee")
        r = run(
            "--workdir", str(self.workdir), "--memory-root", str(self.memory),
            "--stamp", "--today", "2026-09-09T09:09Z", "--json",
        )
        p = json.loads(r.stdout)
        self.assertTrue(p["stamped"])
        self.assertFalse(p["stale"])
        self.assertEqual(p["as_of_commit"], self.head)
        text = self.current.read_text(encoding="utf-8")
        self.assertIn(f"`{self.head}`", text)
        self.assertIn("2026-09-09T09:09Z", text)
        self.assertNotIn("deadbee", text)

    def test_current_matches_head_is_not_stale(self) -> None:
        self._write(self.head)
        p = json.loads(run("--workdir", str(self.workdir), "--memory-root", str(self.memory), "--json").stdout)
        self.assertFalse(p["stale"])

    def test_missing_status_file_fails_soft(self) -> None:
        p = json.loads(run("--workdir", str(self.workdir), "--memory-root", str(self.memory), "--json").stdout)
        self.assertFalse(p["ok"])
        self.assertEqual(p["reason"], "no_status_file")


if __name__ == "__main__":
    unittest.main(verbosity=2)
