#!/usr/bin/env python3
"""Extend the `search_vector` GENERATED column to include `chunk_context`.

Phase D — sparse-leg side. The Phase A migration created
`search_vector` as `to_tsvector('english', subj || ' ' || pred || ' '
|| obj)`. With chunk_context now persisted, the FTS leg should also
match against the prepended summary so paraphrased queries hit the
sparse leg as well as the vector leg.

Postgres can't `ALTER` a GENERATED column's expression in place — we
have to drop + recreate. The GIN index goes with it; we recreate that
too. Because the column is `STORED`, the new expression is materialised
for every row at column-recreate time, so no separate backfill pass is
needed.

Idempotent: a probe checks the current column expression; if it
already mentions `chunk_context`, the migration is a no-op.

Usage:
    uv run --extra db python3 scripts/migrate_extend_search_vector_with_context.py
    uv run --extra db python3 scripts/migrate_extend_search_vector_with_context.py \
         --schemas build_loop_memory --dry-run

Exit:
  0 success (or no-op)
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
except Exception as _db_import_err:  # noqa: BLE001
    execute = None  # type: ignore
    query = None  # type: ignore
    _DB_IMPORT_ERR: Exception | None = _db_import_err
else:
    _DB_IMPORT_ERR = None

SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_SCHEMAS = ("build_loop_memory", "personal_memory")
INDEX_NAME = "semantic_facts_search_vector_gin_idx"

NEW_EXPRESSION = (
    "to_tsvector('english', "
    "  coalesce(subject,'') || ' ' || "
    "  coalesce(predicate,'') || ' ' || "
    "  coalesce(object,'') || ' ' || "
    "  coalesce(chunk_context,'')"
    ")"
)


def _safe_schema(s: str) -> str:
    if not SCHEMA_RE.match(s):
        raise ValueError(f"unsafe schema name: {s!r}")
    return s


def _column_definition(schema: str, column: str) -> str | None:
    """Return the GENERATED expression of the column, or None if absent.

    Postgres exposes the expression in `information_schema.columns`
    via `generation_expression` (PG12+).
    """
    rows = query(
        "SELECT generation_expression FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = 'semantic_facts' "
        "  AND column_name = %s",
        (schema, column),
    )
    if not rows:
        return None
    return (rows[0].get("generation_expression") or "").strip()


def _column_exists(schema: str, column: str) -> bool:
    return _column_definition(schema, column) is not None


def migrate_schema(schema: str, *, dry_run: bool) -> dict:
    schema = _safe_schema(schema)
    status: dict = {"schema": schema}

    if not _column_exists(schema, "chunk_context"):
        status["action"] = "skip-no-chunk-context-column"
        status["reason"] = (
            "Run scripts/migrate_add_chunk_context_column.py first; "
            "the search_vector extension references chunk_context."
        )
        return status

    expr = _column_definition(schema, "search_vector") or ""
    status["had_search_vector"] = bool(expr)
    if "chunk_context" in expr:
        status["action"] = "noop"
        return status

    if dry_run:
        status["action"] = "would-recreate-search-vector"
        return status

    # Drop + recreate. The dependent GIN index drops with it via CASCADE
    # (we recreate the index right after). We do NOT use CASCADE
    # implicitly — explicit index drop avoids surprising teardowns of
    # other dependents in foreign installs.
    execute(f"DROP INDEX IF EXISTS {schema}.{INDEX_NAME}")
    execute(f"ALTER TABLE {schema}.semantic_facts DROP COLUMN IF EXISTS search_vector")
    execute(
        f"ALTER TABLE {schema}.semantic_facts "
        f"ADD COLUMN search_vector tsvector "
        f"GENERATED ALWAYS AS ({NEW_EXPRESSION}) STORED"
    )
    execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
        f"ON {schema}.semantic_facts USING GIN (search_vector)"
    )
    status["action"] = "recreated-with-chunk-context"
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schemas", default=",".join(DEFAULT_SCHEMAS))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if _DB_IMPORT_ERR:
        print(f"db import failed: {_DB_IMPORT_ERR}", file=sys.stderr)
        return 2

    schemas = [s.strip() for s in args.schemas.split(",") if s.strip()]
    try:
        for s in schemas:
            _safe_schema(s)
    except ValueError as e:
        print(f"validation: {e}", file=sys.stderr)
        return 1

    overall_rc = 0
    for s in schemas:
        try:
            status = migrate_schema(s, dry_run=args.dry_run)
            print(f"{status}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"failed for {s}: {e}", file=sys.stderr)
            overall_rc = 2
    return overall_rc


if __name__ == "__main__":
    sys.exit(main())
