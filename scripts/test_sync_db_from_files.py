#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Test sync_db_from_files.py against a temporary test schema.

- Write 3 canonical MADR files via write_decision.py (--no-db).
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

from _test_helpers import MemIsolationMixin  # noqa: E402

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


def _frontmatter_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip().strip("'").strip('"')
    raise AssertionError(f"{key!r} missing from {path}")


def write_decision(workdir: Path, title: str, entity: str, tag: str = "process") -> tuple[str, Path, str]:
    """Write a canonical decision through the production writer."""
    before = set((Path(os.environ["AGENT_MEMORY_ROOT"]) / "projects" / "test-default" / "decisions").glob("*.md"))
    cp = subprocess.run(
        [
            sys.executable,
            str(WRITE),
            "--workdir", str(workdir),
            "--title", title,
            "--decision", f"Decision: {title}",
            "--context", "Sync test fixture",
            "--tags", tag,
            "--primary-tag", tag,
            "--entity", entity,
            "--confidence", "explicit",
            "--source", "manual",
            "--project", "test-default",
            "--no-db",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if cp.returncode != 0:
        raise AssertionError(cp.stderr)
    decisions_dir = Path(os.environ["AGENT_MEMORY_ROOT"]) / "projects" / "test-default" / "decisions"
    after = {
        p for p in decisions_dir.glob("*.md")
        if p.name != "INDEX.md" and not p.name.startswith("_")
    }
    new_files = sorted(after - before)
    if len(new_files) != 1:
        raise AssertionError(f"expected one new canonical decision file, got {new_files}")
    path = new_files[0]
    return cp.stdout.strip(), path, _frontmatter_value(path, "canonical_id")


def sync(workdir: Path, rebuild: bool = False, project: str = "test-default") -> subprocess.CompletedProcess:
    args = [
        sys.executable, str(SYNC),
        "--workdir", str(workdir),
        "--schema", TEST_SCHEMA,
        "--project", project,
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
        (self.workdir / ".build-loop").mkdir(parents=True)
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
        _, path, canonical_id = write_decision(self.workdir, "Initial Title", "ent-x", "tooling")
        cp = sync(self.workdir, rebuild=True)
        self.assertEqual(cp.returncode, 0, msg=cp.stderr)
        # Sanity: row present
        subject = f"decision:test-default:{canonical_id}"
        old_obj = psql_exec(
            f"SELECT object FROM {TEST_SCHEMA}.semantic_facts WHERE subject = '{subject}';"
        )
        self.assertEqual(old_obj, "Initial Title")
        # Modify the file's title (rewrite it directly)
        text = path.read_text()
        text = text.replace("title: Initial Title", "title: Updated Title")
        text = text.replace("# Initial Title", "# Updated Title")
        path.write_text(text)
        # Re-sync without --rebuild
        cp = sync(self.workdir, rebuild=False)
        self.assertEqual(cp.returncode, 0, msg=cp.stderr)
        # Row count still 1 (DELETE+INSERT keeps it stable)
        count = psql_exec(f"SELECT count(*) FROM {TEST_SCHEMA}.semantic_facts;")
        self.assertEqual(count, "1")
        new_obj = psql_exec(
            f"SELECT object FROM {TEST_SCHEMA}.semantic_facts WHERE subject = '{subject}';"
        )
        self.assertEqual(new_obj, "Updated Title")


if __name__ == "__main__":
    unittest.main()
