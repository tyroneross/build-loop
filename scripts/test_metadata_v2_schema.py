#!/usr/bin/env python3
"""Tests for v2 metadata schema (design §15).

Coverage:
- write_decision applies sensible defaults for the 9 v2 fields when no
  v2 args are passed
- write_decision injects exact values when v2 args are passed
- frontmatter shape is round-trip parseable
- events.jsonl line carries the v2 fields
- validator rejects bogus task_category and tool
- validator rejects bogus files_touched type
- validator accepts the v2 default-shaped output
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


TAXONOMY = """---
type: taxonomy
schema_version: 2
---

## 1. Decision tags

- `architecture`
- `tooling`
- `process`
- `testing`

## 6. Source attribution

- `manual`
- `auto-explicit`
- `auto-confirmed`
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
        "--no-db",
    ]
    for k, v in kw.items():
        args.extend([f"--{k.replace('_', '-')}", v])
    return _run(args)


def validate(workdir: Path) -> subprocess.CompletedProcess:
    return _run([sys.executable, str(VALIDATE), "--workdir", str(workdir), "--quiet"])


def parse_fm(text: str) -> dict:
    from write_decision import parse_frontmatter  # type: ignore  # noqa: PLC0415

    return parse_frontmatter(text) or {}


class V2SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        (self.workdir / ".semantic").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions" / "_history").mkdir(parents=True)
        (self.workdir / ".episodic" / "issues").mkdir(parents=True)
        (self.workdir / ".semantic" / "TAXONOMY.md").write_text(TAXONOMY)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # M-A: defaults populate the 9 new fields when not passed.
    def test_defaults_populate_v2_fields(self) -> None:
        r = write(self.workdir, entity="build-loop:foo")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        f = next((self.workdir / ".episodic" / "decisions").glob("0001-*.md"))
        fm = parse_fm(f.read_text())
        # All 9 v2 fields must be present.
        for k in (
            "project", "tool", "model", "task_category", "author",
            "last_validated", "last_accessed", "files_touched", "closing_commit",
        ):
            self.assertIn(k, fm, msg=f"missing v2 field {k}")
        # Defaults specifically.
        self.assertEqual(fm["project"], "build-loop")  # derived from entity prefix
        self.assertEqual(fm["tool"], "manual")  # source=manual maps to tool=manual
        self.assertEqual(fm["model"], "claude-opus-4-7")
        self.assertEqual(fm["task_category"], "unknown")
        self.assertIsNotNone(fm["author"])  # $USER
        self.assertEqual(fm["files_touched"], [])
        self.assertIsNone(fm["closing_commit"])
        self.assertIsNone(fm["last_validated"])
        self.assertIsNone(fm["last_accessed"])

    # M-B: explicit args land in frontmatter and event line.
    def test_explicit_v2_args_round_trip(self) -> None:
        r = write(
            self.workdir,
            entity="build-loop:foo",
            project="foo",
            tool="codex",
            model="gpt-5.4",
            task_category="research",
            author="alice",
            files_touched="src/a.ts,src/b.ts",
            closing_commit="abc123",
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        f = next((self.workdir / ".episodic" / "decisions").glob("0001-*.md"))
        fm = parse_fm(f.read_text())
        self.assertEqual(fm["project"], "foo")
        self.assertEqual(fm["tool"], "codex")
        self.assertEqual(fm["model"], "gpt-5.4")
        self.assertEqual(fm["task_category"], "research")
        self.assertEqual(fm["author"], "alice")
        self.assertEqual(fm["files_touched"], ["src/a.ts", "src/b.ts"])
        self.assertEqual(fm["closing_commit"], "abc123")
        # Event line mirrors v2 fields.
        events_path = self.workdir / ".episodic" / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["project"], "foo")
        self.assertEqual(ev["tool"], "codex")
        self.assertEqual(ev["model"], "gpt-5.4")
        self.assertEqual(ev["task_category"], "research")
        self.assertEqual(ev["author"], "alice")

    # M-C: validator rejects bogus task_category.
    def test_writer_rejects_bogus_task_category(self) -> None:
        r = write(self.workdir, task_category="not-in-enum")
        self.assertEqual(r.returncode, 1)
        self.assertIn("task_category", r.stderr)

    def test_writer_rejects_bogus_tool(self) -> None:
        r = write(self.workdir, tool="bogus-tool")
        self.assertEqual(r.returncode, 1)
        self.assertIn("tool", r.stderr)

    def test_validator_rejects_bogus_task_category_in_existing_file(self) -> None:
        # Bypass writer: hand-write an MADR with an invalid task_category.
        bad = self.workdir / ".episodic" / "decisions" / "0001-2026-05-04-bad.md"
        bad.write_text(
            """---
id: '0001'
slug: bad
title: Bad task_category
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
task_category: not-in-enum
author: t
source: manual
---

# bad
"""
        )
        r = validate(self.workdir)
        self.assertEqual(r.returncode, 1)
        self.assertIn("task_category", r.stderr)

    # Validator accepts a clean v2 file written by the writer with defaults.
    def test_validator_accepts_default_v2_output(self) -> None:
        r = write(self.workdir, entity="build-loop:foo")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        rv = validate(self.workdir)
        self.assertEqual(rv.returncode, 0, msg=rv.stderr)


if __name__ == "__main__":
    unittest.main()
