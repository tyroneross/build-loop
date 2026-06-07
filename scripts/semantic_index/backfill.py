#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Backfill embeddings for semantic_facts rows where ``embedding_json IS NULL``.

The P1 auto-embed-on-write change (``upsert_fact(auto_embed=True)``)
only covers rows written AFTER the upgrade. Pre-upgrade rows — and rows
written while the embed backend was down — sit with ``embedding_json
NULL`` and skip the cosine rerank in ``hybrid.rerank_candidates``.

This module embeds those NULL rows in place. Idempotent: a second run
finds zero NULL rows and is a no-op. Resumable: batched commits mean a
killed process loses at most one batch.

Public API:
    backfill_embeddings(...)  → BackfillResult     library entry point
    main(argv=None)           → int                CLI: report + exit code

Stdlib-only. Defers ``embed_backend.embed`` import so the script remains
importable in environments without MLX/Ollama (it will just report zero
work done and the underlying error).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from semantic_index import (  # noqa: E402
    _db_path,
    _embed_text_for_fact,
    connect,
)


@dataclass
class BackfillResult:
    """Outcome of a backfill run. Reportable without re-querying."""

    db_path: str = ""
    scanned: int = 0
    embedded: int = 0
    skipped_empty_text: int = 0
    failed: int = 0
    backend_unavailable: bool = False
    error: Optional[str] = None
    sample_failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_path": self.db_path,
            "scanned": self.scanned,
            "embedded": self.embedded,
            "skipped_empty_text": self.skipped_empty_text,
            "failed": self.failed,
            "backend_unavailable": self.backend_unavailable,
            "error": self.error,
            "sample_failures": self.sample_failures[:5],
        }


def _resolve_default_embed_fn() -> Optional[Callable[[str], list[float]]]:
    """Resolve ``embed_backend.embed`` lazily. Returns None on import error.

    Kept out of module-import so the backfill module is importable in
    environments without optional MLX/Ollama dependencies — the CLI
    surfaces backend-unavailable as a clean error, not a stack trace.
    """
    try:
        from embed_backend import embed as _embed  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    return _embed


def _iter_null_rows(
    conn: sqlite3.Connection,
    batch_size: int,
    after_rowid: int = 0,
) -> list[tuple[int, str, str, str]]:
    """Fetch up to ``batch_size`` rows with NULL embedding_json AND
    ``rowid > after_rowid``.

    The ``after_rowid`` cursor prevents the inner loop from re-fetching
    a row that failed to embed (which would leave it NULL and re-pick it
    on the next iteration — an infinite loop). The outer call passes 0
    on first batch and advances to the highest rowid seen so far.
    Successfully-embedded rows are no longer NULL, so they fall out of
    the predicate naturally — no separate exclusion set needed.

    Returns (rowid, subject, predicate, object) tuples — exactly the
    fields ``_embed_text_for_fact`` consumes. Bounded by ``batch_size``
    so memory and commit-window scale predictably with large DBs.
    """
    cur = conn.execute(
        """
        SELECT rowid, subject, predicate, object
        FROM semantic_facts
        WHERE embedding_json IS NULL AND rowid > ?
        ORDER BY rowid ASC
        LIMIT ?
        """,
        (after_rowid, batch_size),
    )
    return [
        (int(r[0]), str(r[1] or ""), str(r[2] or ""), str(r[3] or ""))
        for r in cur.fetchall()
    ]


def backfill_embeddings(
    *,
    db_path: str | Path | None = None,
    embed_fn: Optional[Callable[[str], list[float]]] = None,
    batch_size: int = 50,
    max_rows: Optional[int] = None,
) -> BackfillResult:
    """Embed every ``embedding_json IS NULL`` row in place.

    Idempotency: rows with non-NULL ``embedding_json`` are never re-
    embedded. Re-running on a fully-backfilled DB scans zero rows and
    returns ``embedded=0`` — safe to schedule periodically.

    Per-row failure isolation: when ``embed_fn`` raises on a single row,
    that row stays NULL (counted in ``failed``) and the loop continues.
    A row-level failure does not abort the whole backfill — the embed
    backend may genuinely choke on one pathological text (e.g. all
    whitespace) but be fine on every other row.

    ``max_rows``: cap total rows processed in this call. Useful for
    incremental backfills under a time budget; the next call resumes.
    """
    result = BackfillResult(db_path=str(_db_path(db_path)))
    path = _db_path(db_path)
    if not path.exists():
        # Nothing to backfill — fresh installs hit this before the first
        # write. Not an error; not a backend-unavailable state either.
        return result

    if embed_fn is None:
        embed_fn = _resolve_default_embed_fn()
        if embed_fn is None:
            result.backend_unavailable = True
            result.error = "embed_backend import failed"
            return result

    conn = connect(path)
    try:
        rows_left = max_rows
        cursor_rowid = 0  # advances past every row we touch (success or fail)
        while True:
            if rows_left is not None and rows_left <= 0:
                break
            limit = batch_size
            if rows_left is not None:
                limit = min(batch_size, rows_left)
            batch = _iter_null_rows(conn, limit, after_rowid=cursor_rowid)
            if not batch:
                break

            for rowid, subject, predicate, obj in batch:
                cursor_rowid = max(cursor_rowid, rowid)
                result.scanned += 1
                if rows_left is not None:
                    rows_left -= 1
                text = _embed_text_for_fact(subject, predicate, obj)
                if not text:
                    result.skipped_empty_text += 1
                    continue
                try:
                    vec = embed_fn(text)
                except Exception as e:  # noqa: BLE001 - per-row isolation
                    result.failed += 1
                    if len(result.sample_failures) < 5:
                        result.sample_failures.append(
                            f"rowid={rowid}: {type(e).__name__}: {e}"
                        )
                    continue
                if not isinstance(vec, list) or not vec:
                    result.failed += 1
                    if len(result.sample_failures) < 5:
                        result.sample_failures.append(
                            f"rowid={rowid}: embed returned {type(vec).__name__}"
                        )
                    continue
                try:
                    payload = json.dumps(
                        [float(x) for x in vec], ensure_ascii=False, sort_keys=True
                    )
                except (TypeError, ValueError) as e:
                    result.failed += 1
                    if len(result.sample_failures) < 5:
                        result.sample_failures.append(
                            f"rowid={rowid}: non-numeric vector: {e}"
                        )
                    continue
                conn.execute(
                    "UPDATE semantic_facts SET embedding_json = ? WHERE rowid = ?",
                    (payload, rowid),
                )
                result.embedded += 1
            conn.commit()  # per-batch commit → resumable if killed
    finally:
        conn.close()
    return result


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: report scanned/embedded/failed counts as JSON.

    Exit codes:
      0 — completed successfully (including zero-work no-ops).
      2 — embed backend unavailable (MLX broken + Ollama unreachable).
          Distinct from 0 so cron/health checks can alert on degraded
          embedding capability without flagging clean idempotent runs.
    """
    parser = argparse.ArgumentParser(
        description="Backfill NULL semantic_facts.embedding_json rows."
    )
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Cap rows processed this call (for time-bounded incremental runs).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = backfill_embeddings(
        db_path=args.db_path,
        batch_size=args.batch_size,
        max_rows=args.max_rows,
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(
            f"backfill: scanned={result.scanned} embedded={result.embedded} "
            f"failed={result.failed} skipped_empty={result.skipped_empty_text} "
            f"db={result.db_path}"
        )
        if result.backend_unavailable:
            print(f"  backend unavailable: {result.error}", file=sys.stderr)
        for sample in result.sample_failures:
            print(f"  failure: {sample}", file=sys.stderr)
    if result.backend_unavailable:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
