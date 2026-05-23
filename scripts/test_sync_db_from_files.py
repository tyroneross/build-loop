#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Test sync_db_from_files.py against a temporary test schema.

- Write 3 MADR files via write_decision.py (--no-db).
- Run sync_db_from_files.py --rebuild against test_schema_sync.
- Verify 3 rows in semantic_facts.
- Modify 1 MADR's title, re-run sync (no --rebuild), verify update.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WRITE = HERE / "write_decision.py"
SYNC = HERE / "sync_db_from_files.py"
SCHEMA_SQL = HERE / "init_agent_memory_schema.sql"
TEST_SCHEMA = "test_schema_sync"

from _test_helpers import MemIsolationMixin, write_legacy_madr  # noqa: E402

TAXONOMY = """---
type: taxonomy
---

## 1. Decision tags

- `architecture`
- `process`
- `tooling`
- `testing`

## 6. Source attribution

- `manual`
- `migration`
"""


def _db_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for line in (Path.home() / ".config" / "agent-memory" / "connection.env").read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not configured")


def psql_exec(sql: str) -> str:
    psql_bin = shutil.which("psql") or "/opt/homebrew/opt/postgresql@15/bin/psql"
    cp = subprocess.run(
        [psql_bin, "-d", _db_url(), "-At", "-v", "ON_ERROR_STOP=1", "-q", "-c", sql],
        capture_output=True, text=True, timeout=60,
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr)
    return cp.stdout.strip()


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


def write_decision(workdir: Path, title: str, entity: str, tag: str = "process", _id: str | None = None) -> str:
    """Write a decision into workdir/.episodic/decisions/ (legacy path).

    Uses write_legacy_madr so files land where sync_db_from_files.py (legacy
    mode) expects them, independent of Phase-C AGENT_MEMORY_ROOT routing.
    Auto-increments _id based on how many *.md files already exist.
    """
    import datetime as _dt
    existing = list((workdir / ".episodic" / "decisions").glob("[0-9][0-9][0-9][0-9]-*.md"))
    if _id is None:
        nxt = (max(int(f.name[:4]) for f in existing) + 1) if existing else 1
        _id = f"{nxt:04d}"
    date = _dt.date.today().isoformat()
    p = write_legacy_madr(workdir, _id, date, title, entity, tag)
    return _id


def sync(workdir: Path, rebuild: bool = False) -> subprocess.CompletedProcess:
    args = [
        sys.executable, str(SYNC),
        "--workdir", str(workdir),
        "--schema", TEST_SCHEMA,
    ]
    if rebuild:
        args.append("--rebuild")
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


class SyncTests(MemIsolationMixin, unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        setup_schema()

    @classmethod
    def tearDownClass(cls) -> None:
        teardown_schema()

    def setUp(self) -> None:
        super().setUp()
        psql_exec(f"TRUNCATE TABLE {TEST_SCHEMA}.semantic_facts CASCADE;")
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        (self.workdir / ".semantic").mkdir(parents=True)
        (self.workdir / ".episodic" / "decisions" / "_history").mkdir(parents=True)
        (self.workdir / ".semantic" / "TAXONOMY.md").write_text(TAXONOMY)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        super().tearDown()

    def test_sync_three_files_yields_three_rows(self) -> None:
        write_decision(self.workdir, "Use pytest", "ent-a", "tooling")
        write_decision(self.workdir, "Use TestFlight", "ent-b", "process")
        write_decision(self.workdir, "Use Postgres", "ent-c", "architecture")
        cp = sync(self.workdir, rebuild=True)
        self.assertEqual(cp.returncode, 0, msg=cp.stderr)
        count = psql_exec(f"SELECT count(*) FROM {TEST_SCHEMA}.semantic_facts;")
        self.assertEqual(count, "3", msg=cp.stderr)

    def test_modified_file_updates_row(self) -> None:
        write_decision(self.workdir, "Initial Title", "ent-x", "tooling")
        sync(self.workdir, rebuild=True)
        # Sanity: row present
        old_obj = psql_exec(
            f"SELECT object FROM {TEST_SCHEMA}.semantic_facts WHERE subject = 'decision:0001';"
        )
        self.assertEqual(old_obj, "Initial Title")
        # Modify the file's title (rewrite it directly)
        f = next((self.workdir / ".episodic" / "decisions").glob("0001-*.md"))
        text = f.read_text()
        text = text.replace("title: Initial Title", "title: Updated Title")
        text = text.replace("# Initial Title", "# Updated Title")
        f.write_text(text)
        # Re-sync without --rebuild
        cp = sync(self.workdir, rebuild=False)
        self.assertEqual(cp.returncode, 0, msg=cp.stderr)
        # Row count still 1 (DELETE+INSERT keeps it stable)
        count = psql_exec(f"SELECT count(*) FROM {TEST_SCHEMA}.semantic_facts;")
        self.assertEqual(count, "1")
        new_obj = psql_exec(
            f"SELECT object FROM {TEST_SCHEMA}.semantic_facts WHERE subject = 'decision:0001';"
        )
        self.assertEqual(new_obj, "Updated Title")


if __name__ == "__main__":
    unittest.main()
