#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for revoke_decision.py. Zero deps.

Run: python3 test_revoke_decision.py

Covers:
- File at build-loop-memory/projects/<project>/decisions/0001-... moves to
  build-loop-memory/projects/<project>/decisions/_history/0001-revoked.md
- Frontmatter gets `revoked: true` and `status: rejected`
- decision_revoked event emitted with reason
- Missing decision id rejected (exit 1)
- DB status update is best-effort (no Postgres in unit test → swallowed)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SCRIPT = HERE / "revoke_decision.py"

from _test_helpers import MemIsolationMixin  # noqa: E402
from write_decision import slugify  # type: ignore  # noqa: E402


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
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
        (self.workdir / ".build-loop").mkdir(parents=True)
        (self.workdir / ".semantic" / "TAXONOMY.md").write_text(_seed_taxonomy())

        # Seed a decision directly in the canonical memory-store path so
        # revoke_decision.py can find it without touching the deleted legacy tree.
        decisions_dir = self._decisions_dir("test-default")
        decisions_dir.mkdir(parents=True)
        (decisions_dir / "_history").mkdir(parents=True)
        slug = slugify("Use pytest for testing")
        (decisions_dir / f"0001-2026-05-05-{slug}.md").write_text(
            "\n".join([
                "---",
                "id: '0001'",
                f"slug: {slug}",
                "title: Use pytest for testing",
                "type: decision",
                "status: accepted",
                "confidence: inferred",
                "date: '2026-05-05'",
                "tags: [testing]",
                "primary_tag: testing",
                "entity: build-loop",
                "source: manual",
                "project: test-default",
                "---",
                "",
                "# Use pytest for testing",
                "",
                "body.",
                "",
            ]),
            encoding="utf-8",
        )
        self.decision_id = "0001"

    def tearDown(self) -> None:
        self.tmp.cleanup()
        super().tearDown()

    def test_revoke_happy_path(self) -> None:
        cp = run([
            "--workdir", str(self.workdir),
            "--project", "test-default",
            "--id", self.decision_id,
            "--reason", "user clarified this was venting, not a decision",
            "--no-db",
        ])
        self.assertEqual(cp.returncode, 0, msg=f"stderr: {cp.stderr}")

        # Original file gone from decisions/
        decisions_dir = self._decisions_dir("test-default")
        original = list(decisions_dir.glob(f"{self.decision_id}-*.md"))
        self.assertEqual(len(original), 0, msg=f"original should be moved, found: {original}")

        # Now in _history/<id>-revoked.md
        revoked = decisions_dir / "_history" / f"{self.decision_id}-revoked.md"
        self.assertTrue(revoked.exists(), msg=f"expected revoked file at {revoked}")

        text = revoked.read_text()
        self.assertIn("revoked: true", text)
        self.assertIn("status: rejected", text)
        self.assertIn("user clarified", text)

        # decision_revoked event emitted
        events = self._events_path().read_text().splitlines()
        revokes = [json.loads(l) for l in events if json.loads(l).get("kind") == "decision_revoked"]
        self.assertEqual(len(revokes), 1)
        self.assertEqual(revokes[0]["decision_id"], self.decision_id)
        self.assertIn("user clarified", revokes[0]["reason"])

    def test_missing_id_rejected(self) -> None:
        cp = run([
            "--workdir", str(self.workdir),
            "--project", "test-default",
            "--id", "9999",
            "--reason", "test",
            "--no-db",
        ])
        self.assertEqual(cp.returncode, 1, msg=f"expected validation error, stderr: {cp.stderr}")
        self.assertIn("9999", cp.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
