#!/usr/bin/env python3
"""Test the cosine-similarity dedup path against real ollama embeddings.

Seeds a known fact directly via the live psycopg connection (so we
control exactly what string is embedded), then calls `is_duplicate(...)`
with:
  1. A near-paraphrase of the seeded text → expect True (≥ 0.85 cosine)
  2. An unrelated string                  → expect False

This exercises the live nomic-embed-text pipeline end to end without
mocking. Test schema is dropped on teardown to avoid polluting the
production `build_loop_memory` schema.

Note on embedding seed:
  We embed and insert the same short text we test paraphrases against.
  This makes the threshold predictable. (The production
  `write_decision.py` flow embeds the full MADR body, which has
  different — and more variable — distance to short paraphrases.)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_SQL = HERE / "init_agent_memory_schema.sql"
TEST_SCHEMA = "test_schema_dedup"

sys.path.insert(0, str(HERE))


def _ollama_ready() -> bool:
    if not shutil.which("ollama"):
        return False
    cp = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
    return cp.returncode == 0 and "nomic-embed-text" in cp.stdout


def _db_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for line in (Path.home() / ".config" / "agent-memory" / "connection.env").read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not configured")


def _psql_apply(sql_text: str) -> None:
    """One-shot psql call for schema setup/teardown only.

    Uses psql here (not psycopg) so the fixture is decoupled from the
    cached connection in db.py — we want schema DDL to run cleanly even
    if the test's psycopg connection is mid-transaction.
    """
    psql_bin = shutil.which("psql") or "/opt/homebrew/opt/postgresql@15/bin/psql"
    cp = subprocess.run(
        [psql_bin, "-d", _db_url(), "-v", "ON_ERROR_STOP=1", "-q"],
        input=sql_text,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr)


def setup_schema() -> None:
    _psql_apply(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE;")
    text = SCHEMA_SQL.read_text().replace("build_loop_memory", TEST_SCHEMA)
    _psql_apply(text)


def teardown_schema() -> None:
    _psql_apply(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE;")


def _embed_via_http(text: str) -> list[float]:
    """Minimal HTTP embedding call. Fails loud if ollama is unreachable."""
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/embeddings",
        data=json.dumps({"model": "nomic-embed-text", "prompt": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return [float(x) for x in json.loads(r.read().decode())["embedding"]]


KNOWN_TEXT = "Postgres with pgvector is the memory substrate"
NEAR_PARAPHRASE = "Use Postgres with pgvector as the memory substrate"  # ~0.95
UNRELATED = "Schedule the marketing offsite for next week"              # ~0.36


class DedupPathTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        if not _ollama_ready():
            raise RuntimeError(
                "ollama daemon must be running with nomic-embed-text pulled. "
                "Run `ollama pull nomic-embed-text` and ensure `ollama serve` is up."
            )
        setup_schema()

    @classmethod
    def tearDownClass(cls) -> None:
        # Make sure the cached connection in db.py releases its lock on
        # the test schema before the DROP SCHEMA fires.
        try:
            from db import close_connection
            close_connection()
        except Exception:
            pass
        teardown_schema()

    def setUp(self) -> None:
        # Close any cached psycopg connection so TRUNCATE doesn't deadlock
        # against an idle-in-tx state held by the prior test.
        from db import close_connection
        close_connection()
        _psql_apply(f"TRUNCATE TABLE {TEST_SCHEMA}.semantic_facts CASCADE;")
        self._seed()

    def tearDown(self) -> None:
        from db import close_connection
        close_connection()

    def _seed(self) -> None:
        """Insert a single fact whose embedding is the embedding of KNOWN_TEXT."""
        from db import execute, vector_literal

        emb = _embed_via_http(KNOWN_TEXT)
        execute(
            (
                f"INSERT INTO {TEST_SCHEMA}.semantic_facts "
                "(subject, predicate, object, confidence, status, embedding) "
                "VALUES (%s, %s, %s, 1.0, 'active', %s::vector)"
            ),
            ("decision:0001", "architecture", KNOWN_TEXT, vector_literal(emb)),
        )

    def test_near_paraphrase_classified_as_duplicate(self) -> None:
        """Cosine ≥ 0.85 → is_duplicate returns True."""
        from scan_transcript_for_decisions import is_duplicate

        result = is_duplicate(NEAR_PARAPHRASE, "nomic-embed-text", schema=TEST_SCHEMA)
        self.assertTrue(
            result,
            msg=f"Near-paraphrase should be classified as duplicate (≥ 0.85 cosine), got {result}",
        )

    def test_unrelated_string_not_duplicate(self) -> None:
        """Unrelated subject → cosine < 0.85 → is_duplicate returns False."""
        from scan_transcript_for_decisions import is_duplicate

        result = is_duplicate(UNRELATED, "nomic-embed-text", schema=TEST_SCHEMA)
        self.assertFalse(
            result,
            msg=f"Unrelated string should NOT be duplicate (< 0.85 cosine), got {result}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
