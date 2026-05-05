#!/usr/bin/env python3
"""Test recall.py end-to-end against a temporary test schema.

- Inserts 5 known facts via the schema's semantic_facts table.
- Embeds each via Ollama nomic-embed-text.
- Runs recall.py for one of the known queries.
- Asserts the matching fact appears in the top 3 results.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_SQL = HERE / "init_agent_memory_schema.sql"
RECALL = HERE / "recall.py"
TEST_SCHEMA = "test_schema_recall"


def _db_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for line in (Path.home() / ".config" / "agent-memory" / "connection.env").read_text().splitlines():
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


def setup_test_schema() -> None:
    psql_exec(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE;")
    text = SCHEMA_SQL.read_text().replace("build_loop_memory", TEST_SCHEMA)
    psql_exec(text)


def teardown_test_schema() -> None:
    psql_exec(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE;")


def ollama_embed_via_http(text: str) -> list[float]:
    import urllib.request
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/embeddings",
        data=json.dumps({"model": "nomic-embed-text", "prompt": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return [float(x) for x in json.loads(resp.read().decode("utf-8"))["embedding"]]


KNOWN_FACTS = [
    ("project:build-loop", "test-framework", "We use pytest for all Python testing"),
    ("project:build-loop", "deploy-target", "TestFlight is the iOS distribution channel"),
    ("project:build-loop", "memory-substrate", "Postgres with pgvector extension powers retrieval"),
    ("project:build-loop", "ui-style", "Calm Precision principles guide all interfaces"),
    ("project:build-loop", "branching", "Trunk-based development; feature branches are short-lived"),
]


def insert_known_facts() -> None:
    for subject, predicate, obj in KNOWN_FACTS:
        emb = ollama_embed_via_http(obj)
        emb_lit = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
        sql = (
            f"INSERT INTO {TEST_SCHEMA}.semantic_facts "
            f"(subject, predicate, object, confidence, status, embedding) "
            f"VALUES ($STX${subject}$STX$, $STX${predicate}$STX$, "
            f"$STX${obj}$STX$, 1.0, 'active', '{emb_lit}'::vector);"
        )
        psql_exec(sql)


def run_recall(query: str) -> str:
    cp = subprocess.run(
        [
            sys.executable, str(RECALL),
            "--query", query,
            "--limit", "3",
            "--schema", TEST_SCHEMA,
            "--no-episodes",
            "--confidence-floor", "explicit",
        ],
        capture_output=True, text=True, timeout=60,
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr)
    return cp.stdout


class RecallTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        setup_test_schema()
        insert_known_facts()

    @classmethod
    def tearDownClass(cls) -> None:
        teardown_test_schema()

    def test_recall_finds_pgvector_fact_for_postgres_query(self) -> None:
        out = run_recall("Postgres extension")
        # Must include the memory-substrate fact in the top results
        self.assertIn("memory-substrate", out, msg=f"output:\n{out}")
        # Top fact should be the pgvector one (or at minimum among results)
        # parse the first scored line
        m = re.search(r"score=([\d.]+) \[([^]]+)\]", out)
        self.assertIsNotNone(m, msg=out)
        first_label = m.group(2)
        self.assertIn("memory-substrate", first_label, msg=f"top result was {first_label!r}; expected memory-substrate")

    def test_recall_finds_test_framework_fact(self) -> None:
        out = run_recall("which test framework do we use")
        self.assertIn("test-framework", out, msg=f"output:\n{out}")

    def test_recall_finds_ui_fact(self) -> None:
        out = run_recall("design principles")
        self.assertIn("ui-style", out, msg=f"output:\n{out}")


if __name__ == "__main__":
    unittest.main()
