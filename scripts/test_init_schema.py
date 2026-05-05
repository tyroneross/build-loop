#!/usr/bin/env python3
"""Tests for init_agent_memory_schema.sql.

- Schema applies cleanly to a fresh test schema.
- Re-applying is idempotent.
- HNSW indexes are present on the three embedding-bearing tables.
- pgvector + pg_trgm extensions are detected.

These tests use a TEMPORARY test schema so they don't disturb the
production `build_loop_memory` schema.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_SQL = HERE / "init_agent_memory_schema.sql"
TEST_SCHEMA = "test_schema_init_check"


def psql(sql: str) -> tuple[int, str, str]:
    psql_bin = shutil.which("psql") or "/opt/homebrew/opt/postgresql@15/bin/psql"
    db_url = os.environ.get("DATABASE_URL") or _read_db_url()
    cmd = [psql_bin, "-d", db_url, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql]
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return cp.returncode, cp.stdout, cp.stderr


def _read_db_url() -> str:
    conn_env = Path.home() / ".config" / "agent-memory" / "connection.env"
    for line in conn_env.read_text().splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not found")


def apply_schema_with_substitution(test_schema: str) -> tuple[int, str, str]:
    """Apply the canonical SQL file with `psql -v schema=<test_schema>`.

    Phase B: the SQL file is now parameterized via the psql `:schema`
    variable, so we no longer string-replace the literal schema name.
    """
    psql_bin = shutil.which("psql") or "/opt/homebrew/opt/postgresql@15/bin/psql"
    db_url = os.environ.get("DATABASE_URL") or _read_db_url()
    cp = subprocess.run(
        [psql_bin, "-d", db_url, "-v", "ON_ERROR_STOP=1", "-v",
         f"schema={test_schema}", "-f", str(SCHEMA_SQL)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return cp.returncode, cp.stdout, cp.stderr


class InitSchemaTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        # Ensure clean slate
        psql(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE;")

    @classmethod
    def tearDownClass(cls) -> None:
        psql(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE;")

    def test_extensions_present(self) -> None:
        rc, out, err = psql(
            "SELECT count(*) FROM pg_extension WHERE extname IN ('vector','pg_trgm');"
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), "2")

    def test_schema_applies_cleanly(self) -> None:
        rc, out, err = apply_schema_with_substitution(TEST_SCHEMA)
        self.assertEqual(rc, 0, msg=err)

    def test_idempotent_reapply(self) -> None:
        rc1, _, err1 = apply_schema_with_substitution(TEST_SCHEMA)
        self.assertEqual(rc1, 0, msg=err1)
        rc2, _, err2 = apply_schema_with_substitution(TEST_SCHEMA)
        self.assertEqual(rc2, 0, msg=err2)

    def test_tables_exist(self) -> None:
        apply_schema_with_substitution(TEST_SCHEMA)
        rc, out, err = psql(
            f"""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = '{TEST_SCHEMA}'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name;
            """
        )
        self.assertEqual(rc, 0, msg=err)
        names = set(out.strip().splitlines())
        self.assertEqual(
            names,
            {"episode_events", "fact_conflicts", "procedures", "semantic_facts", "sessions"},
        )

    def test_hnsw_indexes_present(self) -> None:
        apply_schema_with_substitution(TEST_SCHEMA)
        rc, out, err = psql(
            f"""
            SELECT indexname FROM pg_indexes
            WHERE schemaname = '{TEST_SCHEMA}'
              AND indexname LIKE '%hnsw%'
            ORDER BY indexname;
            """
        )
        self.assertEqual(rc, 0, msg=err)
        names = set(out.strip().splitlines())
        self.assertEqual(
            names,
            {
                "episode_events_embedding_hnsw",
                "procedures_embedding_hnsw",
                "semantic_facts_embedding_hnsw",
            },
        )


if __name__ == "__main__":
    unittest.main()
