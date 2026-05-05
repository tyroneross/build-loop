#!/usr/bin/env python3
"""Tests for supersede_decision.py. Zero deps.

Run: python3 test_supersede_decision.py

Covers:
- Same-topic supersession: 0001 → 0002 with --supersedes 0001
- Old file moves to _history/0001-v1.md with status: superseded
- New file present in decisions/ with supersedes: 0001
- INDEX regenerated
- decision_superseded event emitted to events.jsonl
- Missing --old-id rejected (exit 1)
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "supersede_decision.py"
WRITE_DECISION = HERE / "write_decision.py"


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def run_write(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(WRITE_DECISION)] + args,
        capture_output=True,
        text=True,
    )


def _seed_taxonomy() -> str:
    return """---
type: taxonomy
schema_version: 1
---

# Vocab

## 1. Decision tags

- `architecture`
- `data`
- `ui`
- `infra`
- `tooling`
- `process`
- `security`
- `performance`
- `testing`

## 6. Source attribution

- `manual`
- `auto-explicit`
- `auto-confirmed`
- `auto-inferred`
- `auto-assumed`
- `migration`
- `orchestrator`
"""


class SupersedeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        (self.workdir / ".semantic").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions" / "_history").mkdir(parents=True)
        (self.workdir / ".semantic" / "TAXONOMY.md").write_text(_seed_taxonomy())

        # Seed an initial decision (0001).
        cp = run_write([
            "--workdir", str(self.workdir),
            "--title", "Use pytest for testing",
            "--decision", "Adopt pytest",
            "--tags", "tooling,testing",
            "--primary-tag", "testing",
            "--entity", "build-loop",
            "--confidence", "explicit",
            "--no-db",
        ])
        self.assertEqual(cp.returncode, 0, msg=f"seed write failed: {cp.stderr}")
        self.first_id = cp.stdout.strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_supersede_happy_path(self) -> None:
        cp = run([
            "--workdir", str(self.workdir),
            "--old-id", self.first_id,
            "--new-decision", "Switch to pytest 8.x with new fixtures pattern",
            "--new-title", "Switch pytest fixture pattern",
            "--tags", "tooling,testing",
            "--primary-tag", "testing",
            "--entity", "build-loop",
            "--confidence", "explicit",
            "--rationale", "Pytest 8 fixture changes are needed",
            "--no-db",
        ])
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")
        new_id = cp.stdout.strip()
        self.assertNotEqual(new_id, self.first_id)
        self.assertEqual(len(new_id), 4)

        # Old file moved to _history/<id>-v1.md
        history = list((self.workdir / ".episodic" / "decisions" / "_history").glob(f"{self.first_id}-v*.md"))
        self.assertEqual(len(history), 1, msg=f"expected 1 history file, got {history}")
        history_text = history[0].read_text()
        self.assertIn("status: superseded", history_text)
        # YAML emitter quotes leading-digit values; match either form.
        self.assertTrue(
            f"superseded_by: {new_id}" in history_text
            or f"superseded_by: '{new_id}'" in history_text,
            msg=f"superseded_by link missing in: {history_text[:600]}",
        )

        # New decision present
        new_files = list((self.workdir / ".episodic" / "decisions").glob(f"{new_id}-*.md"))
        self.assertEqual(len(new_files), 1)
        new_text = new_files[0].read_text()
        self.assertTrue(
            f"supersedes: {self.first_id}" in new_text
            or f"supersedes: '{self.first_id}'" in new_text,
            msg=f"supersedes link missing in: {new_text[:600]}",
        )

        # INDEX regenerated and references new entry
        index = (self.workdir / ".episodic" / "decisions" / "INDEX.md").read_text()
        self.assertIn(new_id, index)

        # decision_superseded event in events.jsonl
        events = (self.workdir / ".episodic" / "events.jsonl").read_text().splitlines()
        kinds = [json.loads(l)["kind"] for l in events]
        self.assertIn("decision_superseded", kinds)

    def test_missing_old_id_rejected(self) -> None:
        cp = run([
            "--workdir", str(self.workdir),
            "--old-id", "9999",
            "--new-decision", "Whatever",
            "--new-title", "Whatever",
            "--tags", "tooling",
            "--primary-tag", "tooling",
            "--entity", "build-loop",
            "--confidence", "explicit",
            "--rationale", "Should be rejected",
            "--no-db",
        ])
        self.assertEqual(cp.returncode, 1, msg=f"expected validation error, stderr: {cp.stderr}")
        self.assertIn("9999", cp.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
