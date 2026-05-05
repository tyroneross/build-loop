#!/usr/bin/env python3
"""Tests for migrate_schema_v3.py (file + events tier; DB tier opt-in).

- v2 fixture migrates: all 7 v3 fields populated with defaults.
- Idempotent: second run produces no changes (same hash).
- events.jsonl gains v3 fields mirrored from migrated MADRs.
- domain heuristic from primary_tag works (testing→test, infra→infra,
  process→meta, etc.)
- Migrated files validate clean against `validate_knowledge.py`.
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
MIGRATE_V3 = HERE / "migrate_schema_v3.py"
VALIDATE = HERE / "validate_knowledge.py"


# v2-shaped MADR (already has the 9 v2 fields, no v3 fields yet).
def _v2_madr(decision_id: str, primary_tag: str, source: str = "migration") -> str:
    return f"""---
id: '{decision_id}'
slug: legacy-v2
title: Legacy v2 decision {decision_id}
type: decision
status: accepted
confidence: confirmed
date: '2026-04-13'
tags: [{primary_tag}]
primary_tag: {primary_tag}
entity: 'build-loop:legacy-{decision_id}'
project: build-loop
tool: migration
model: unknown
task_category: unknown
author: t
source: {source}
related_runs: []
related_decisions: []
supersedes: null
superseded_by: null
bookmark_snapshot_id: null
captured_turn_excerpt: null
last_validated: null
last_accessed: null
files_touched: []
closing_commit: null
---

# Legacy v2 decision {decision_id}
"""


def _v2_event(decision_id: str, primary_tag: str) -> dict:
    return {
        "ts": "2026-04-13T10:00:00Z",
        "kind": "decision_accepted",
        "decision_id": decision_id,
        "title": f"Legacy v2 decision {decision_id}",
        "primary_tag": primary_tag,
        "entity": f"build-loop:legacy-{decision_id}",
        "project": "build-loop",
        "tool": "migration",
        "model": "unknown",
        "task_category": "unknown",
        "author": "t",
        "confidence": "confirmed",
        "source": "migration",
        "supersedes": None,
        "dedup_key": f"decision:{decision_id}:decision_accepted",
    }


TAXONOMY = """---
type: taxonomy
---

## 1. Decision tags

- `tooling`
- `process`
- `testing`
- `infra`
- `data`
- `architecture`

## 6. Source attribution

- `manual`
- `migration`
"""


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True)


def _hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _seed_v2_fixture(workdir: Path) -> None:
    (workdir / ".semantic").mkdir(parents=True)
    (workdir / ".semantic" / "TAXONOMY.md").write_text(TAXONOMY)
    decisions = workdir / ".episodic" / "decisions"
    decisions.mkdir(parents=True)
    (decisions / "_history").mkdir()
    # Three decisions with different primary_tags to test domain heuristic.
    (decisions / "0001-2026-04-13-legacy-tooling.md").write_text(_v2_madr("0001", "tooling"))
    (decisions / "0002-2026-04-13-legacy-testing.md").write_text(_v2_madr("0002", "testing"))
    (decisions / "0003-2026-04-13-legacy-process.md").write_text(_v2_madr("0003", "process"))
    events_path = workdir / ".episodic" / "events.jsonl"
    events_path.write_text(
        "\n".join(
            json.dumps(_v2_event(did, pt))
            for did, pt in [("0001", "tooling"), ("0002", "testing"), ("0003", "process")]
        )
        + "\n"
    )


def parse_fm(text: str) -> dict:
    from write_decision import parse_frontmatter  # type: ignore  # noqa: PLC0415

    return parse_frontmatter(text) or {}


class MigrateV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        _seed_v2_fixture(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_v2_files_migrate_with_defaults(self) -> None:
        r = _run([sys.executable, str(MIGRATE_V3), "--workdir", str(self.workdir), "--no-db"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        decisions = self.workdir / ".episodic" / "decisions"
        for f in sorted(decisions.glob("0[0-9][0-9][0-9]-*.md")):
            fm = parse_fm(f.read_text())
            for k in (
                "confidence_source", "confirmation_count", "valid_until",
                "causal_parent_id", "embedding_model_version", "domain", "goal",
            ):
                self.assertIn(k, fm, msg=f"{f.name} missing v3 field {k}")
            # Defaults.
            self.assertEqual(fm["confidence_source"], "external_import")  # source=migration
            self.assertEqual(fm["confirmation_count"], 0)
            self.assertIsNone(fm["valid_until"])
            self.assertIsNone(fm["causal_parent_id"])
            self.assertEqual(fm["embedding_model_version"], "mxbai-embed-large-v1")
            self.assertEqual(fm["goal"], "unknown")

    def test_domain_heuristic_from_primary_tag(self) -> None:
        r = _run([sys.executable, str(MIGRATE_V3), "--workdir", str(self.workdir), "--no-db"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        decisions = self.workdir / ".episodic" / "decisions"
        # tooling → tooling
        fm1 = parse_fm((decisions / "0001-2026-04-13-legacy-tooling.md").read_text())
        self.assertEqual(fm1["domain"], "tooling")
        # testing → test
        fm2 = parse_fm((decisions / "0002-2026-04-13-legacy-testing.md").read_text())
        self.assertEqual(fm2["domain"], "test")
        # process → meta
        fm3 = parse_fm((decisions / "0003-2026-04-13-legacy-process.md").read_text())
        self.assertEqual(fm3["domain"], "meta")

    def test_migration_is_idempotent(self) -> None:
        r1 = _run([sys.executable, str(MIGRATE_V3), "--workdir", str(self.workdir), "--no-db"])
        self.assertEqual(r1.returncode, 0, msg=r1.stderr)
        decisions = self.workdir / ".episodic" / "decisions"
        events_path = self.workdir / ".episodic" / "events.jsonl"
        files = sorted(decisions.glob("0[0-9][0-9][0-9]-*.md"))
        h1 = {f.name: _hash(f) for f in files}
        h1_events = _hash(events_path)

        r2 = _run([sys.executable, str(MIGRATE_V3), "--workdir", str(self.workdir), "--no-db"])
        self.assertEqual(r2.returncode, 0, msg=r2.stderr)
        h2 = {f.name: _hash(f) for f in files}
        h2_events = _hash(events_path)
        self.assertEqual(h1, h2, "decision files changed on second migration run")
        self.assertEqual(h1_events, h2_events, "events.jsonl changed on second migration run")
        self.assertIn('"updated": 0', r2.stderr)

    def test_events_jsonl_gains_v3_fields(self) -> None:
        r = _run([sys.executable, str(MIGRATE_V3), "--workdir", str(self.workdir), "--no-db"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        events = [
            json.loads(line)
            for line in (self.workdir / ".episodic" / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        self.assertEqual(len(events), 3)
        for ev in events:
            for k in (
                "confidence_source", "confirmation_count", "valid_until",
                "causal_parent_id", "embedding_model_version", "domain", "goal",
            ):
                self.assertIn(k, ev, msg=f"event missing v3 field {k}")

    def test_validator_passes_after_migration(self) -> None:
        r = _run([sys.executable, str(MIGRATE_V3), "--workdir", str(self.workdir), "--no-db"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        rv = _run([sys.executable, str(VALIDATE), "--workdir", str(self.workdir), "--quiet"])
        self.assertEqual(rv.returncode, 0, msg=rv.stderr)


if __name__ == "__main__":
    unittest.main()
