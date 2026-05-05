#!/usr/bin/env python3
"""Tests for v3 metadata schema (design §16).

V3 adds 7 new fields on top of v2:

  Research-driven (5):
    - confidence_source       (enum: user_statement | ai_inference |
                                tool_extraction | external_import | unknown)
    - confirmation_count      (int >= 0; default 0)
    - valid_until             (ISO date or null; default null)
    - causal_parent_id        (decision_id or null; default null)
    - embedding_model_version (string; default mxbai-embed-large-v1)

  MECE-axis (2):
    - domain                  (enum: ui|api|data|search|auth|build|infra|
                                tooling|docs|test|meta|unknown)
    - goal                    (enum: user-value|reliability|performance|
                                security|dev-velocity|maintainability|
                                compliance|learning|unknown)

Coverage:
- Default writes populate all 7 v3 fields with sensible defaults
- Explicit args round-trip into frontmatter + events.jsonl
- Validator rejects each invalid enum value (domain, goal, confidence_source)
- Validator rejects negative confirmation_count
- Validator rejects malformed valid_until
- Validator accepts default-shaped writer output
- confidence_source default depends on `source` (manual → user_statement,
  auto-* → ai_inference, migration → external_import)
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
WRITE = HERE / "write_decision.py"
VALIDATE = HERE / "validate_knowledge.py"

from _test_helpers import MemIsolationMixin  # noqa: E402


TAXONOMY = """---
type: taxonomy
schema_version: 3
---

## 1. Decision tags

- `architecture`
- `tooling`
- `process`
- `testing`
- `data`
- `infra`

## 6. Source attribution

- `manual`
- `auto-explicit`
- `auto-confirmed`
- `auto-inferred`
- `auto-assumed`
- `migration`
"""


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True)


def write(workdir: Path, **kw) -> subprocess.CompletedProcess:
    args = [
        sys.executable, str(WRITE),
        "--workdir", str(workdir),
        "--title", kw.pop("title", "T"),
        "--decision", kw.pop("decision", "D"),
        "--tags", kw.pop("tags", "tooling"),
        "--primary-tag", kw.pop("primary_tag", "tooling"),
        "--entity", kw.pop("entity", "build-loop:t"),
        "--confidence", kw.pop("confidence", "explicit"),
        "--source", kw.pop("source", "manual"),
        "--project", kw.pop("project", "test-v3"),
        "--no-db",
    ]
    for k, v in kw.items():
        args.extend([f"--{k.replace('_', '-')}", str(v)])
    return _run(args)


def validate(workdir: Path) -> subprocess.CompletedProcess:
    return _run([sys.executable, str(VALIDATE), "--workdir", str(workdir), "--quiet"])


def parse_fm(text: str) -> dict:
    from write_decision import parse_frontmatter  # type: ignore  # noqa: PLC0415

    return parse_frontmatter(text) or {}


def _seed(workdir: Path) -> None:
    (workdir / ".semantic").mkdir(parents=True)
    (workdir / ".episodic" / "decisions" / "_history").mkdir(parents=True)
    (workdir / ".semantic" / "TAXONOMY.md").write_text(TAXONOMY)


class V3SchemaTests(MemIsolationMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        _seed(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        super().tearDown()

    # --- defaults ---

    def test_defaults_populate_v3_fields_manual_source(self) -> None:
        r = write(self.workdir, entity="build-loop:foo", source="manual")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        f = next(self._decisions_dir("test-v3").glob("0001-*.md"))
        fm = parse_fm(f.read_text())
        # All 7 v3 fields present.
        for k in (
            "confidence_source", "confirmation_count", "valid_until",
            "causal_parent_id", "embedding_model_version", "domain", "goal",
        ):
            self.assertIn(k, fm, msg=f"missing v3 field {k}")
        # Specific defaults.
        self.assertEqual(fm["confidence_source"], "user_statement")
        self.assertEqual(fm["confirmation_count"], 0)
        self.assertIsNone(fm["valid_until"])
        self.assertIsNone(fm["causal_parent_id"])
        self.assertEqual(fm["embedding_model_version"], "mxbai-embed-large-v1")
        self.assertEqual(fm["domain"], "unknown")
        self.assertEqual(fm["goal"], "unknown")

    def test_defaults_confidence_source_for_auto_source(self) -> None:
        r = write(self.workdir, entity="build-loop:foo", source="auto-explicit")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        f = next(self._decisions_dir("test-v3").glob("0001-*.md"))
        fm = parse_fm(f.read_text())
        self.assertEqual(fm["confidence_source"], "ai_inference")

    def test_defaults_confidence_source_for_migration_source(self) -> None:
        r = write(self.workdir, entity="build-loop:foo", source="migration")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        f = next(self._decisions_dir("test-v3").glob("0001-*.md"))
        fm = parse_fm(f.read_text())
        self.assertEqual(fm["confidence_source"], "external_import")

    # --- explicit args round-trip ---

    def test_explicit_v3_args_round_trip(self) -> None:
        r = write(
            self.workdir,
            entity="build-loop:foo",
            domain="search",
            goal="reliability",
            confidence_source="user_statement",
            confirmation_count="0",
            valid_until="2026-12-31",
            embedding_model_version="mxbai-embed-large-v1",
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        f = next(self._decisions_dir("test-v3").glob("0001-*.md"))
        fm = parse_fm(f.read_text())
        self.assertEqual(fm["domain"], "search")
        self.assertEqual(fm["goal"], "reliability")
        self.assertEqual(fm["confidence_source"], "user_statement")
        self.assertEqual(fm["confirmation_count"], 0)
        self.assertEqual(fm["valid_until"], "2026-12-31")
        self.assertEqual(fm["embedding_model_version"], "mxbai-embed-large-v1")
        self.assertIsNone(fm["causal_parent_id"])
        # Event line carries v3 fields too.
        events_path = self.workdir / ".episodic" / "events.jsonl"
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["domain"], "search")
        self.assertEqual(ev["goal"], "reliability")
        self.assertEqual(ev["confidence_source"], "user_statement")

    def test_explicit_causal_parent_id(self) -> None:
        # First decision...
        r1 = write(self.workdir, entity="build-loop:a", title="A")
        self.assertEqual(r1.returncode, 0, msg=r1.stderr)
        first_id = r1.stdout.strip()
        # ...then a child decision pointing at it.
        r2 = write(
            self.workdir,
            entity="build-loop:b",
            title="B",
            causal_parent_id=first_id,
        )
        self.assertEqual(r2.returncode, 0, msg=r2.stderr)
        f = next(self._decisions_dir("test-v3").glob(f"{r2.stdout.strip()}-*.md"))
        fm = parse_fm(f.read_text())
        self.assertEqual(fm["causal_parent_id"], first_id)

    # --- writer-level enum rejection (validate_v3 fires before write) ---

    def test_writer_rejects_bogus_domain(self) -> None:
        r = write(self.workdir, domain="not-a-domain")
        self.assertEqual(r.returncode, 1)
        self.assertIn("domain", r.stderr)

    def test_writer_rejects_bogus_goal(self) -> None:
        r = write(self.workdir, goal="not-a-goal")
        self.assertEqual(r.returncode, 1)
        self.assertIn("goal", r.stderr)

    def test_writer_rejects_bogus_confidence_source(self) -> None:
        r = write(self.workdir, confidence_source="bogus")
        self.assertEqual(r.returncode, 1)
        self.assertIn("confidence_source", r.stderr)

    def test_writer_rejects_negative_confirmation_count(self) -> None:
        r = write(self.workdir, confirmation_count="-1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("confirmation_count", r.stderr)

    def test_writer_rejects_invalid_valid_until(self) -> None:
        r = write(self.workdir, valid_until="not-a-date")
        self.assertEqual(r.returncode, 1)
        self.assertIn("valid_until", r.stderr)

    # --- validator rejects bogus existing files ---

    def test_validator_rejects_bogus_domain_in_existing_file(self) -> None:
        bad = self.workdir / ".episodic" / "decisions" / "0001-2026-05-04-bad.md"
        bad.write_text(
            """---
id: '0001'
slug: bad
title: Bad domain
type: decision
status: accepted
confidence: explicit
date: '2026-05-04'
tags: [tooling]
primary_tag: tooling
entity: ent
project: foo
tool: claude-code
model: claude-opus-4-7
task_category: unknown
author: t
source: manual
confidence_source: user_statement
confirmation_count: 0
valid_until: null
causal_parent_id: null
embedding_model_version: mxbai-embed-large-v1
domain: not-a-domain
goal: unknown
---

# bad
"""
        )
        r = validate(self.workdir)
        self.assertEqual(r.returncode, 1)
        self.assertIn("domain", r.stderr)

    def test_validator_rejects_bogus_goal_in_existing_file(self) -> None:
        bad = self.workdir / ".episodic" / "decisions" / "0001-2026-05-04-bad.md"
        bad.write_text(
            """---
id: '0001'
slug: bad
title: Bad goal
type: decision
status: accepted
confidence: explicit
date: '2026-05-04'
tags: [tooling]
primary_tag: tooling
entity: ent
project: foo
tool: claude-code
model: claude-opus-4-7
task_category: unknown
author: t
source: manual
confidence_source: user_statement
confirmation_count: 0
valid_until: null
causal_parent_id: null
embedding_model_version: mxbai-embed-large-v1
domain: tooling
goal: not-a-goal
---

# bad
"""
        )
        r = validate(self.workdir)
        self.assertEqual(r.returncode, 1)
        self.assertIn("goal", r.stderr)

    def test_validator_rejects_negative_confirmation_count_in_existing_file(self) -> None:
        bad = self.workdir / ".episodic" / "decisions" / "0001-2026-05-04-bad.md"
        bad.write_text(
            """---
id: '0001'
slug: bad
title: Negative count
type: decision
status: accepted
confidence: explicit
date: '2026-05-04'
tags: [tooling]
primary_tag: tooling
entity: ent
project: foo
tool: claude-code
model: claude-opus-4-7
task_category: unknown
author: t
source: manual
confidence_source: user_statement
confirmation_count: -5
valid_until: null
causal_parent_id: null
embedding_model_version: mxbai-embed-large-v1
domain: tooling
goal: unknown
---

# bad
"""
        )
        r = validate(self.workdir)
        self.assertEqual(r.returncode, 1)
        self.assertIn("confirmation_count", r.stderr)

    def test_validator_accepts_default_v3_output(self) -> None:
        r = write(self.workdir, entity="build-loop:foo")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        rv = validate(self.workdir)
        self.assertEqual(rv.returncode, 0, msg=rv.stderr)


if __name__ == "__main__":
    unittest.main()
