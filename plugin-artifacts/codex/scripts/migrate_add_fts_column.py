#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Add `search_vector tsvector` GENERATED column + GIN index to semantic_facts.

Phase A chunk 2: the sparse leg of hybrid retrieval needs a stable
tsvector that always reflects the current subject/predicate/object.
A `GENERATED ALWAYS AS (... ) STORED` column gives us that for free —
no triggers, no application-side maintenance, no race conditions.

Migration is idempotent. Re-running on an already-migrated schema is a
no-op (`ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`).

Usage:
    uv run --extra db python3 scripts/migrate_add_fts_column.py
    uv run --extra db python3 scripts/migrate_add_fts_column.py --schemas build_loop_memory
    uv run --extra db python3 scripts/migrate_add_fts_column.py --dry-run

Why this shape (not separate trigger):
  - `STORED` keeps reads fast (no per-query tsvector recomputation).
  - English config matches Example App's `pipeline-rag.ts` keyword leg.
  - `coalesce(.., '')` tolerates NULL subject/predicate/object (none today,
    but defensive against future writers).
  - GIN index is the standard pg recommendation for tsvector @@ queries.

Exit:
  0 success (or no-op when already migrated)
  1 validation error
  2 DB error
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    from db import execute, query  # type: ignore
except Exception as _e:  # noqa: BLE001
    execute = None  # type: ignore
    query = None  # type: ignore
    _DB_IMPORT_ERR: Exception | None = _e
else:
    _DB_IMPORT_ERR = None

SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_SCHEMAS = ("build_loop_memory", "personal_memory")


def _safe_schema(s: str) -> str:
    if not SCHEMA_RE.match(s):
        raise ValueError(f"unsafe schema name: {s!r}")
    return s


def _column_exists(schema: str, table: str, column: str) -> bool:
    rows = query(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
        (schema, table, column),
    )
    return bool(rows)


def _index_exists(schema: str, index_name: str) -> bool:
    rows = query(
        "SELECT 1 FROM pg_indexes "
        "WHERE schemaname = %s AND indexname = %s",
        (schema, index_name),
    )
    return bool(rows)


def migrate_schema(schema: str, *, dry_run: bool) -> dict:
    """Apply the migration to one schema. Returns a status dict."""
    schema = _safe_schema(schema)
    status: dict = {"schema": schema}

    has_col = _column_exists(schema, "semantic_facts", "search_vector")
    idx_name = "semantic_facts_search_vector_gin_idx"
    has_idx = _index_exists(schema, idx_name)

    status["had_column"] = has_col
    status["had_index"] = has_idx

    if has_col and has_idx:
        status["action"] = "noop"
        return status

    if dry_run:
        status["action"] = (
            ("add-column " if not has_col else "")
            + ("add-index" if not has_idx else "")
        ).strip()
        return status

    if not has_col:
        # GENERATED ALWAYS AS (expression) STORED — Postgres 12+.
        # english config is the most universally compatible tsvector
        # config; covers stemming and stopword removal for the language
        # of build-loop's decisions.
        execute(
            f"ALTER TABLE {schema}.semantic_facts "  # nosec: schema is a validated identifier (^[a-z][a-z0-9_]*$); values bound as params
            "ADD COLUMN search_vector tsvector "
            "GENERATED ALWAYS AS ("
            "  to_tsvector('english', "
            "    coalesce(subject,'') || ' ' || "
            "    coalesce(predicate,'') || ' ' || "
            "    coalesce(object,'')"
            "  )"
            ") STORED"
        )
    if not has_idx:
        execute(
            f"CREATE INDEX IF NOT EXISTS {idx_name} "
            f"ON {schema}.semantic_facts USING GIN (search_vector)"
        )

    status["action"] = (
        ("added-column " if not has_col else "")
        + ("added-index" if not has_idx else "")
    ).strip()
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schemas",
        default=",".join(DEFAULT_SCHEMAS),
        help=f"Comma-separated schemas to migrate (default: {','.join(DEFAULT_SCHEMAS)})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change; no DDL executed",
    )
    args = parser.parse_args(argv)

    if _DB_IMPORT_ERR:
        print(f"db import failed: {_DB_IMPORT_ERR}", file=sys.stderr)
        return 2

    schemas = [s.strip() for s in args.schemas.split(",") if s.strip()]
    try:
        for s in schemas:
            _safe_schema(s)
    except ValueError as e:
        print(f"invalid schema: {e}", file=sys.stderr)
        return 1

    for s in schemas:
        try:
            status = migrate_schema(s, dry_run=args.dry_run)
        except Exception as e:  # noqa: BLE001
            print(f"[{s}] migration failed: {e}", file=sys.stderr)
            return 2
        prefix = "DRY-RUN" if args.dry_run else "OK"
        print(
            f"[{prefix}][{s}] action={status['action'] or 'noop'} "
            f"had_column={status['had_column']} had_index={status['had_index']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
