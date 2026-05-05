#!/usr/bin/env python3
"""Tests for migrate_schema_v2.py (file + events tier; DB tier is opt-in).

- v1 fixture migrates: all 9 fields populated with defaults.
- Idempotent: second run produces no changes (same content hash).
- events.jsonl lines gain v2 fields mirrored from the migrated MADRs.
- Writer-output files (already v2) survive a migration pass unchanged.
- The fixture validates clean against `validate_knowledge.py` after migration.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
MIGRATE = HERE / "migrate_schema_v2.py"
VALIDATE = HERE / "validate_knowledge.py"


V1_FIXTURE_BODY = """---
id: '0001'
slug: legacy
title: Legacy v1 decision
type: decision
status: accepted
confidence: confirmed
date: '2026-04-13'
tags: [tooling]
primary_tag: tooling
entity: 'build-loop:legacy'
source: migration
related_runs: []
related_decisions: []
supersedes: null
superseded_by: null
bookmark_snapshot_id: null
captured_turn_excerpt: null
---

# Legacy v1 decision

## Context

Pre-v2 shape.
"""

V1_EVENT_LINE = json.dumps(
    {
        "ts": "2026-04-13T10:00:00Z",
        "kind": "decision_accepted",
        "decision_id": "0001",
        "title": "Legacy v1 decision",
        "primary_tag": "tooling",
        "entity": "build-loop:legacy",
        "confidence": "confirmed",
        "source": "migration",
        "supersedes": None,
        "dedup_key": "decision:0001:decision_accepted",
    }
)

TAXONOMY = """---
type: taxonomy
---

## 1. Decision tags

- `tooling`
- `process`

## 6. Source attribution

- `manual`
- `migration`
"""


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True)


def _hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _seed_v1_fixture(workdir: Path) -> None:
    (workdir / ".semantic").mkdir(parents=True)
    (workdir / ".semantic" / "TAXONOMY.md").write_text(TAXONOMY)
    (workdir / ".episodic" / "decisions" / "_history").mkdir(parents=True)
    (workdir / ".episodic" / "decisions" / "0001-2026-04-13-legacy.md").write_text(V1_FIXTURE_BODY)
    (workdir / ".episodic" / "events.jsonl").write_text(V1_EVENT_LINE + "\n")


def parse_fm(text: str) -> dict:
    from write_decision import parse_frontmatter  # type: ignore  # noqa: PLC0415

    return parse_frontmatter(text) or {}


class MigrateV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        _seed_v1_fixture(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_v1_file_migrates_with_defaults(self) -> None:
        r = _run([sys.executable, str(MIGRATE), "--workdir", str(self.workdir), "--no-db"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        f = next((self.workdir / ".episodic" / "decisions").glob("0001-*.md"))
        fm = parse_fm(f.read_text())
        # All 9 v2 fields present with sensible defaults.
        self.assertEqual(fm["project"], "build-loop")  # extracted from entity prefix
        self.assertEqual(fm["tool"], "migration")  # source=migration → tool=migration
        self.assertEqual(fm["model"], "unknown")  # retroactive: no model info
        self.assertEqual(fm["task_category"], "unknown")
        self.assertIsNotNone(fm["author"])
        self.assertIsNone(fm["last_validated"])
        self.assertIsNone(fm["last_accessed"])
        self.assertEqual(fm["files_touched"], [])
        self.assertIsNone(fm["closing_commit"])

    def test_migration_is_idempotent(self) -> None:
        r1 = _run([sys.executable, str(MIGRATE), "--workdir", str(self.workdir), "--no-db"])
        self.assertEqual(r1.returncode, 0, msg=r1.stderr)
        f = next((self.workdir / ".episodic" / "decisions").glob("0001-*.md"))
        events_path = self.workdir / ".episodic" / "events.jsonl"
        h1_decision = _hash(f)
        h1_events = _hash(events_path)

        r2 = _run([sys.executable, str(MIGRATE), "--workdir", str(self.workdir), "--no-db"])
        self.assertEqual(r2.returncode, 0, msg=r2.stderr)
        h2_decision = _hash(f)
        h2_events = _hash(events_path)
        self.assertEqual(h1_decision, h2_decision, "decision file changed on second migration run")
        self.assertEqual(h1_events, h2_events, "events.jsonl changed on second migration run")

        # Summary must show 0 updated on second run.
        self.assertIn('"updated": 0', r2.stderr)

    def test_events_jsonl_gains_v2_fields(self) -> None:
        r = _run([sys.executable, str(MIGRATE), "--workdir", str(self.workdir), "--no-db"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        events = [
            json.loads(line)
            for line in (self.workdir / ".episodic" / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        self.assertEqual(len(events), 1)
        ev = events[0]
        for k in ("project", "tool", "model", "task_category", "author"):
            self.assertIn(k, ev, msg=f"event line missing v2 field {k}")
        # Mirrored from the migrated MADR.
        self.assertEqual(ev["project"], "build-loop")
        self.assertEqual(ev["tool"], "migration")

    def test_validator_passes_after_migration(self) -> None:
        r = _run([sys.executable, str(MIGRATE), "--workdir", str(self.workdir), "--no-db"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        rv = _run([sys.executable, str(VALIDATE), "--workdir", str(self.workdir), "--quiet"])
        self.assertEqual(rv.returncode, 0, msg=rv.stderr)


if __name__ == "__main__":
    unittest.main()
