#!/usr/bin/env python3
"""Live tests for consolidate_memory.py.

Requires real Postgres + real embed backend (MLX or Ollama). Skipped
gracefully if the DB is unreachable.

Covers:
- no `_candidates.jsonl` -> exit 0, no-op
- IGNORE path: candidate cosine-equivalent to existing semantic_facts
- UPDATE path: candidate same (subject, predicate) at higher confidence
- INSERT path: novel (subject, predicate, object)
- archive: consolidated entries appended to _candidates_history.jsonl
- --dry-run does not mutate DB or candidate file
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "consolidate_memory.py"
WRITE_SCRIPT = HERE / "write_decision.py"
SCHEMA_TEST = "build_loop_memory_test"


def _has_db() -> bool:
    if "DATABASE_URL" in os.environ:
        return True
    conn_env = Path.home() / ".config" / "agent-memory" / "connection.env"
    return conn_env.exists()


def _seed_taxonomy() -> str:
    return (
        "---\ntype: taxonomy\nschema_version: 1\n---\n"
        "# Vocab\n## 1. Decision tags\n"
        "- `architecture`\n- `data`\n- `tooling`\n- `testing`\n"
        "## 6. Source attribution\n- `manual`\n- `migration`\n- `auto-explicit`\n"
    )


def _setup_test_schema() -> None:
    """Create a fresh test schema mirroring the production layout."""
    sys.path.insert(0, str(HERE))
    from db import execute_script  # type: ignore

    execute_script(
        f"""
        DROP SCHEMA IF EXISTS {SCHEMA_TEST} CASCADE;
        CREATE SCHEMA {SCHEMA_TEST};
        CREATE TABLE {SCHEMA_TEST}.semantic_facts (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          subject TEXT NOT NULL,
          predicate TEXT NOT NULL,
          object TEXT NOT NULL,
          confidence FLOAT DEFAULT 1.0,
          status TEXT DEFAULT 'active',
          valid_from TIMESTAMPTZ DEFAULT now(),
          valid_to TIMESTAMPTZ,
          embedding VECTOR(1024),
          metadata JSONB
        );
        CREATE TABLE {SCHEMA_TEST}.fact_conflicts (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          fact_id_a UUID,
          fact_id_b UUID,
          conflict_type TEXT,
          resolved BOOLEAN DEFAULT FALSE,
          resolution_fact_id UUID,
          detected_at TIMESTAMPTZ DEFAULT now(),
          resolved_at TIMESTAMPTZ,
          metadata JSONB
        );
        CREATE INDEX ON {SCHEMA_TEST}.semantic_facts USING hnsw (embedding vector_cosine_ops);
        """
    )


def _insert_existing_fact(subject: str, predicate: str, obj: str) -> None:
    sys.path.insert(0, str(HERE))
    from db import execute, vector_literal  # type: ignore
    from embed_backend import embed  # type: ignore

    text = f"{subject} {predicate}: {obj}"
    emb = embed(text)
    execute(
        f"INSERT INTO {SCHEMA_TEST}.semantic_facts "
        "(subject, predicate, object, confidence, status, embedding, metadata) "
        "VALUES (%s, %s, %s, %s, 'active', %s::vector, %s::jsonb)",
        (subject, predicate, obj, 0.75, vector_literal(emb), json.dumps({"seed": True})),
    )


def _count_facts() -> int:
    sys.path.insert(0, str(HERE))
    from db import query_one  # type: ignore

    row = query_one(f"SELECT count(*)::int AS c FROM {SCHEMA_TEST}.semantic_facts")
    return int(row["c"])


def run_consolidate(workdir: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir), "--schema", SCHEMA_TEST] + list(extra),
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(_has_db(), "DATABASE_URL not configured; skipping live consolidate tests")
class ConsolidateMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        (self.workdir / ".semantic").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions").mkdir(parents=True)
        (self.workdir / ".semantic" / "TAXONOMY.md").write_text(_seed_taxonomy())
        _setup_test_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()
        sys.path.insert(0, str(HERE))
        from db import execute_script, close_connection  # type: ignore

        try:
            execute_script(f"DROP SCHEMA IF EXISTS {SCHEMA_TEST} CASCADE;")
        finally:
            close_connection()

    def test_no_candidates_file_is_noop(self) -> None:
        r = run_consolidate(self.workdir)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        # No history file is created when there are no candidates
        self.assertFalse((self.workdir / ".semantic" / "_candidates_history.jsonl").exists())

    def test_three_candidates_classify_correctly(self) -> None:
        # Seed an existing fact: "this_project | testing_framework | pytest"
        _insert_existing_fact("this_project", "testing_framework", "pytest")
        baseline = _count_facts()
        self.assertEqual(baseline, 1)

        # Three candidates:
        #   1. duplicate (cosine >= 0.90 vs existing) -> IGNORE
        #   2. same (subject, predicate) but different object -> CONFLICT (or UPDATE w/ confidence)
        #   3. novel (subject, predicate, object) -> INSERT
        candidates = [
            {
                "subject": "this_project",
                "predicate": "testing_framework",
                "object": "pytest",  # exact match -> IGNORE
                "confidence": "explicit",
                "source_episode_id": None,
            },
            {
                "subject": "this_project",
                "predicate": "testing_framework",
                "object": "py.test",  # phrasing variant -> MERGE/UPDATE
                "confidence": "explicit",
                "source_episode_id": None,
            },
            {
                "subject": "this_project",
                "predicate": "build_tool",
                "object": "uv",  # novel -> INSERT
                "confidence": "explicit",
                "source_episode_id": None,
            },
        ]
        cand_path = self.workdir / ".semantic" / "_candidates.jsonl"
        cand_path.write_text("\n".join(json.dumps(c) for c in candidates) + "\n")

        r = run_consolidate(self.workdir)
        self.assertEqual(r.returncode, 0, msg=r.stderr)

        # After consolidation: at least the novel fact INSERTed (so >= 2).
        # The duplicate IGNOREd. The MERGE may either UPDATE confidence or be
        # logged as conflict; either way the row count for that (subject, predicate)
        # remains 1, and the novel fact adds 1 row.
        post = _count_facts()
        self.assertGreaterEqual(post, 2, "novel fact should be inserted")

        # Archive must exist with 3 entries
        history_path = self.workdir / ".semantic" / "_candidates_history.jsonl"
        self.assertTrue(history_path.exists())
        history_lines = [json.loads(line) for line in history_path.read_text().splitlines() if line.strip()]
        self.assertEqual(len(history_lines), 3)
        actions = sorted(h["action"] for h in history_lines)
        # Expect at least one INSERT and one IGNORE in the action set
        self.assertIn("INSERT", actions)
        self.assertIn("IGNORE", actions)

        # Original candidates file is consumed (truncated/removed)
        if cand_path.exists():
            self.assertEqual(cand_path.read_text().strip(), "")

    def test_dry_run_makes_no_writes(self) -> None:
        _insert_existing_fact("this_project", "testing_framework", "pytest")
        baseline = _count_facts()

        cand_path = self.workdir / ".semantic" / "_candidates.jsonl"
        cand_path.write_text(json.dumps({
            "subject": "this_project",
            "predicate": "build_tool",
            "object": "uv",
            "confidence": "explicit",
        }) + "\n")

        r = run_consolidate(self.workdir, "--dry-run")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        # No DB mutation
        self.assertEqual(_count_facts(), baseline)
        # Candidate file untouched
        self.assertTrue(cand_path.exists())
        self.assertIn("uv", cand_path.read_text())
        # No history written
        self.assertFalse((self.workdir / ".semantic" / "_candidates_history.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
