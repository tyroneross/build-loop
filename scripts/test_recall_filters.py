#!/usr/bin/env python3
"""Tests for v2 metadata filters in recall.py (design §15).

Inserts 3 facts with different (project, tool, task_category) tuples;
verifies recall.py honors the metadata-filter-first hybrid pattern.

Requires Postgres + pgvector + an embed backend (skips if either is
missing — same posture as test_recall.py).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SCHEMA_SQL = HERE / "init_agent_memory_schema.sql"
RECALL = HERE / "recall.py"
TEST_SCHEMA = "test_schema_recall_filters"


def _db_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    conn_env = Path.home() / ".config" / "agent-memory" / "connection.env"
    if conn_env.exists():
        for line in conn_env.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not configured")


def psql_exec(sql: str) -> None:
    psql_bin = shutil.which("psql") or "/opt/homebrew/opt/postgresql@15/bin/psql"
    cp = subprocess.run(
        [psql_bin, "-d", _db_url(), "-v", "ON_ERROR_STOP=1", "-q"],
        input=sql, capture_output=True, text=True, timeout=60,
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr)


def _embed_via_backend(text: str) -> list[float]:
    import embed_backend  # type: ignore  # noqa: PLC0415

    return embed_backend.embed(text)


def setup_schema() -> None:
    psql_exec(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE;")
    psql_bin = shutil.which("psql") or "/opt/homebrew/opt/postgresql@15/bin/psql"
    cp = subprocess.run(
        [psql_bin, "-d", _db_url(), "-v", "ON_ERROR_STOP=1", "-v",
         f"schema={TEST_SCHEMA}", "-q", "-f", str(SCHEMA_SQL)],
        capture_output=True, text=True, timeout=60,
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr)


def teardown_schema() -> None:
    psql_exec(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE;")


# 3 facts, varied metadata. The query "performance budgets" should match
# all three semantically; the filter is what distinguishes them.
KNOWN_FACTS = [
    {
        "subject": "decision:0001",
        "predicate": "performance",
        "object": "Frontend bundle budget capped at 200KB gzip",
        "project": "build-loop",
        "tool": "claude-code",
        "model": "claude-opus-4-7",
        "task_category": "feature",
        "author": "alice",
    },
    {
        "subject": "decision:0002",
        "predicate": "performance",
        "object": "API p50 latency budget set at 100ms",
        "project": "speaksavvy",
        "tool": "codex",
        "model": "gpt-5.4",
        "task_category": "bugfix",
        "author": "bob",
    },
    {
        "subject": "decision:0003",
        "predicate": "performance",
        "object": "Background job timeout budget set to 30 seconds",
        "project": "build-loop",
        "tool": "claude-code",
        "model": "claude-opus-4-7",
        "task_category": "research",
        "author": "alice",
    },
]


def insert_facts() -> None:
    for f in KNOWN_FACTS:
        emb = _embed_via_backend(f["object"])
        emb_lit = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
        sql = (
            f"INSERT INTO {TEST_SCHEMA}.semantic_facts "
            "(subject, predicate, object, confidence, status, embedding, metadata, "
            " project, tool, model, task_category, author) "
            "VALUES ($STX${subj}$STX$, $STX${pred}$STX$, $STX${obj}$STX$, "
            "        1.0, 'active', '{emb}'::vector, "
            "        jsonb_build_object('project', '{prj}'), "
            "        '{prj}', '{tool}', '{model}', '{tc}', '{auth}');"
        ).format(
            subj=f["subject"], pred=f["predicate"], obj=f["object"], emb=emb_lit,
            prj=f["project"], tool=f["tool"], model=f["model"], tc=f["task_category"], auth=f["author"],
        )
        psql_exec(sql)


def run_recall(query: str, **filters: str) -> str:
    args = [
        sys.executable, str(RECALL),
        "--query", query,
        "--limit", "5",
        "--schema", TEST_SCHEMA,
        "--no-episodes",
        "--confidence-floor", "explicit",
        "--no-bump-last-accessed",  # avoid mutating rows during read tests
    ]
    # Phase B: when this test's caller did not pass --project explicitly,
    # disable default project-scoping so the test's controlled fixture rows
    # (which include rows tagged 'speaksavvy', 'build-loop', etc.) all stay
    # eligible for ranking. Tests that DO want project filtering pass
    # `project=...` via **filters and we honor that.
    if "project" not in filters:
        args.append("--all-projects")
    for k, v in filters.items():
        args.extend([f"--{k.replace('_', '-')}", v])
    cp = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr)
    return cp.stdout


def _extract_subjects(out: str) -> list[str]:
    """Extract `decision:NNNN` subjects from the recall output ordered top-down."""
    subjects: list[str] = []
    for m in re.finditer(r"\[([^|\]]+) \| ", out):
        subj = m.group(1).strip()
        if subj.startswith("decision:"):
            subjects.append(subj)
    return subjects


class RecallFilterTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        try:
            setup_schema()
            insert_facts()
        except Exception as e:  # noqa: BLE001
            raise unittest.SkipTest(f"DB / embed unavailable: {e}")

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            teardown_schema()
        except Exception:  # noqa: BLE001
            pass

    def test_no_filter_returns_all_three(self) -> None:
        out = run_recall("performance budgets")
        subjects = _extract_subjects(out)
        for s in ("decision:0001", "decision:0002", "decision:0003"):
            self.assertIn(s, subjects, msg=f"missing {s} in {subjects}\noutput:\n{out}")

    def test_project_filter_returns_only_matching(self) -> None:
        out = run_recall("performance budgets", project="build-loop")
        subjects = _extract_subjects(out)
        self.assertIn("decision:0001", subjects)
        self.assertIn("decision:0003", subjects)
        self.assertNotIn("decision:0002", subjects, msg=f"speaksavvy fact leaked: {out}")

    def test_task_category_filter_returns_only_matching(self) -> None:
        out = run_recall("performance budgets", task_category="research")
        subjects = _extract_subjects(out)
        self.assertEqual(subjects, ["decision:0003"], msg=f"output:\n{out}")

    def test_combined_filter_returns_only_intersection(self) -> None:
        out = run_recall("performance budgets", project="build-loop", task_category="feature")
        subjects = _extract_subjects(out)
        self.assertEqual(subjects, ["decision:0001"], msg=f"output:\n{out}")

    def test_tool_filter(self) -> None:
        out = run_recall("performance budgets", tool="codex")
        subjects = _extract_subjects(out)
        self.assertEqual(subjects, ["decision:0002"], msg=f"output:\n{out}")

    def test_author_filter(self) -> None:
        out = run_recall("performance budgets", author="bob")
        subjects = _extract_subjects(out)
        self.assertEqual(subjects, ["decision:0002"], msg=f"output:\n{out}")


if __name__ == "__main__":
    unittest.main()
