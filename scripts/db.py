#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""psycopg-based DB helper for repo-local episodic memory scripts.

Replaces the prior `psql` subprocess pattern. One persistent connection
per script invocation; closed on process exit via atexit.

Connection resolution order (delegated to ``scripts/_db_url.py``):
  1. $BUILD_LOOP_DATABASE_URL env var
  2. $DATABASE_URL env var
  3. ~/.config/agent-memory/connection.env -> DATABASE_URL=...
  4. RuntimeError

Public API:
  get_connection()             -> psycopg.Connection (cached, reused)
  execute(sql, params=None)    -> rowcount; for INSERT/UPDATE/DELETE/DDL
  execute_many(sql, seq)       -> rowcount; for batched parameterized writes
  query(sql, params=None)      -> list[dict]; for SELECT
  query_one(sql, params=None)  -> dict | None
  close_connection()           -> idempotent; auto-called via atexit

All write helpers commit on success and rollback on exception. The
connection is opened with autocommit=False so multi-statement helpers
keep their transaction semantics.

Why psycopg (not psql subprocess):
  - ~5-10ms/query persistent vs ~50-100ms/query subprocess fork
  - Real parameterized queries (no SQL-string interpolation)
  - Native vector type adapter (pgvector-python optional; we serialize
    embeddings as text-form `[1.23,...]::vector` casts which works on
    bare psycopg without extra deps)

Exit-code contract for callers:
  Connection / DB errors raise psycopg.Error subclasses. Callers should
  treat these as exit code 2 (filesystem/DB error) per the script-wide
  contract.
"""
from __future__ import annotations

import atexit
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

# psycopg is an optional extra (.[db]).  Import lazily so this module (and
# recall.py, which imports `from db import ...` at module level) collect and
# import cleanly even when psycopg is absent.  Any function that actually
# opens a connection calls _require_psycopg() first, which raises a clear
# actionable error at call time — not at import time.
try:
    import psycopg
    from psycopg.rows import dict_row as _dict_row
    _PSYCOPG_AVAILABLE = True
except ImportError:
    psycopg = None  # type: ignore[assignment]
    _dict_row = None  # type: ignore[assignment]
    _PSYCOPG_AVAILABLE = False

# Make scripts/ importable as a sibling so `_db_url` resolves whether this
# module is imported as `scripts.db` or run with scripts/ on sys.path.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _db_url import resolve_db_url  # noqa: E402

_CONN: Any = None  # psycopg.Connection when psycopg is available


def _require_psycopg() -> None:
    """Raise a clear error when psycopg is absent.

    Called at the top of every function that needs a live DB connection so
    the ImportError surfaces at the actual call site rather than at module
    import time.  This lets tests that mock the DB helpers collect and run
    without psycopg installed (.[db] extra).
    """
    if not _PSYCOPG_AVAILABLE:
        raise ImportError(
            "psycopg is not installed. "
            "Install the optional extra: uv pip install -e '.[db]'"
        )


def _read_db_url() -> str:
    """Resolve the DSN via the shared resolver; raise if nothing is set.

    Preserves this module's historical raise-on-missing public contract.
    Resolution order (BUILD_LOOP_DATABASE_URL → DATABASE_URL →
    connection.env) is owned by ``scripts/_db_url.py``.
    """
    url = resolve_db_url()
    if url:
        return url
    raise RuntimeError(
        "DATABASE_URL not configured. Set $BUILD_LOOP_DATABASE_URL or "
        "$DATABASE_URL, or write ~/.config/agent-memory/connection.env "
        "with DATABASE_URL=..."
    )


def get_connection() -> Any:
    """Return a process-local connection, opening it on first call."""
    _require_psycopg()
    global _CONN
    if _CONN is None or _CONN.closed:
        _CONN = psycopg.connect(_read_db_url(), autocommit=False)
        atexit.register(close_connection)
    return _CONN


def close_connection() -> None:
    """Idempotent close. Safe to call multiple times."""
    global _CONN
    if _CONN is not None and not _CONN.closed:
        try:
            _CONN.close()
        except Exception:  # noqa: BLE001
            pass
    _CONN = None


def execute(sql: str, params: Sequence[Any] | None = None) -> int:
    """Run a single non-SELECT statement. Commits on success.

    Returns rowcount. Raises psycopg.Error on failure (after rollback).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            rc = cur.rowcount
        conn.commit()
        return rc
    except Exception:
        conn.rollback()
        raise


def execute_many(sql: str, seq: Iterable[Sequence[Any]]) -> int:
    """Run executemany over `seq` of param-tuples. Commits on success."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, list(seq))
            rc = cur.rowcount
        conn.commit()
        return rc
    except Exception:
        conn.rollback()
        raise


def execute_script(sql: str) -> None:
    """Run a multi-statement DDL/DML block (no params). Commits on success.

    Used by sync_db_from_files DELETE+INSERT pairs and schema setup.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def query(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    """Run a SELECT and return list of dict rows. No commit (read-only)."""
    conn = get_connection()
    with conn.cursor(row_factory=_dict_row) as cur:
        cur.execute(sql, params or ())
        return list(cur.fetchall())


def query_one(sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    """Run a SELECT and return the first row, or None."""
    conn = get_connection()
    with conn.cursor(row_factory=_dict_row) as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row


def vector_literal(embedding: Sequence[float]) -> str:
    """Render an embedding as a `pgvector` text literal: `[0.1,0.2,...]`.

    Use with an explicit `::vector` cast in the SQL, e.g.:
        execute("INSERT ... VALUES (%s::vector)", (vector_literal(emb),))
    """
    return "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"
