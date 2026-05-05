#!/usr/bin/env python3
"""Tests for revoke_decision.py. Zero deps.

Run: python3 test_revoke_decision.py

Covers:
- File at .episodic/decisions/0001-... moves to .episodic/decisions/_history/0001-revoked.md
- Frontmatter gets `revoked: true` and `status: rejected`
- decision_revoked event emitted with reason
- Missing decision id rejected (exit 1)
- DB status update is best-effort (no Postgres in unit test → swallowed)
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
sys.path.insert(0, str(HERE))
SCRIPT = HERE / "revoke_decision.py"
WRITE_DECISION = HERE / "write_decision.py"

from _test_helpers import MemIsolationMixin, write_legacy_madr  # noqa: E402


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
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


class RevokeTests(MemIsolationMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        (self.workdir / ".semantic").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions" / "_history").mkdir(parents=True)
        (self.workdir / ".semantic" / "TAXONOMY.md").write_text(_seed_taxonomy())

        # Seed a decision directly in the legacy path so revoke_decision.py
        # (which reads from workdir/.episodic/decisions/) can find it.
        write_legacy_madr(
            self.workdir,
            "0001",
            "2026-05-05",
            "Use pytest for testing",
            "build-loop",
            "testing",
            confidence="inferred",
        )
        self.decision_id = "0001"

    def tearDown(self) -> None:
        self.tmp.cleanup()
        super().tearDown()

    def test_revoke_happy_path(self) -> None:
        cp = run([
            "--workdir", str(self.workdir),
            "--id", self.decision_id,
            "--reason", "user clarified this was venting, not a decision",
            "--no-db",
        ])
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")

        # Original file gone from decisions/
        original = list((self.workdir / ".episodic" / "decisions").glob(f"{self.decision_id}-*.md"))
        self.assertEqual(len(original), 0, msg=f"original should be moved, found: {original}")

        # Now in _history/<id>-revoked.md
        revoked = self.workdir / ".episodic" / "decisions" / "_history" / f"{self.decision_id}-revoked.md"
        self.assertTrue(revoked.exists(), msg=f"expected revoked file at {revoked}")

        text = revoked.read_text()
        self.assertIn("revoked: true", text)
        self.assertIn("status: rejected", text)
        self.assertIn("user clarified", text)

        # decision_revoked event emitted
        events = (self.workdir / ".episodic" / "events.jsonl").read_text().splitlines()
        revokes = [json.loads(l) for l in events if json.loads(l).get("kind") == "decision_revoked"]
        self.assertEqual(len(revokes), 1)
        self.assertEqual(revokes[0]["decision_id"], self.decision_id)
        self.assertIn("user clarified", revokes[0]["reason"])

    def test_missing_id_rejected(self) -> None:
        cp = run([
            "--workdir", str(self.workdir),
            "--id", "9999",
            "--reason", "test",
            "--no-db",
        ])
        self.assertEqual(cp.returncode, 1, msg=f"expected validation error, stderr: {cp.stderr}")
        self.assertIn("9999", cp.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
