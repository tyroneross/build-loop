#!/usr/bin/env python3
"""psycopg-based DB helper for repo-local episodic memory scripts.

Replaces the prior `psql` subprocess pattern. One persistent connection
per script invocation; closed on process exit via atexit.

Connection resolution order:
  1. $DATABASE_URL env var
  2. ~/.config/agent-memory/connection.env -> DATABASE_URL=...
  3. RuntimeError

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
import os
from pathlib import Path
from typing import Any, Iterable, Sequence

import psycopg
from psycopg.rows import dict_row

_CONN: psycopg.Connection | None = None


def _read_db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    conn_env = Path.home() / ".config" / "agent-memory" / "connection.env"
    if conn_env.exists():
        for line in conn_env.read_text().splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError(
        "DATABASE_URL not configured. Set $DATABASE_URL or write "
        "~/.config/agent-memory/connection.env with DATABASE_URL=..."
    )


def get_connection() -> psycopg.Connection:
    """Return a process-local connection, opening it on first call."""
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
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        return list(cur.fetchall())


def query_one(sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    """Run a SELECT and return the first row, or None."""
    conn = get_connection()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row


def vector_literal(embedding: Sequence[float]) -> str:
    """Render an embedding as a `pgvector` text literal: `[0.1,0.2,...]`.

    Use with an explicit `::vector` cast in the SQL, e.g.:
        execute("INSERT ... VALUES (%s::vector)", (vector_literal(emb),))
    """
    return "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"
