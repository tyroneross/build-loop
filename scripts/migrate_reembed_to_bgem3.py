#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Re-embed semantic_facts rows whose vector was produced by a non-bge-m3 model.

Phase A migration helper. Cross-vector-space cosine is meaningless, so
when the Ollama default flips from `mxbai-embed-large` to `bge-m3` we
must re-embed any row that still carries an mxbai vector before the
hybrid pipeline can fuse them with bge-m3-embedded queries.

Idempotent — uses `embedding_model_version` as the gate. Only rows whose
stamp is missing OR not in the target model set get re-embedded. Safe to
re-run.

Usage:
    EMBED_BACKEND=ollama uv run python scripts/migrate_reembed_to_bgem3.py
    EMBED_BACKEND=ollama uv run python scripts/migrate_reembed_to_bgem3.py --dry-run
    uv run python scripts/migrate_reembed_to_bgem3.py --target bge-m3 --schema build_loop_memory

Defaults:
  --target bge-m3
  --schemas build_loop_memory,personal_memory   (both are migrated)
  --batch-size 16

Exit:
  0 success (or no-op when nothing to migrate)
  1 validation error (bad CLI / schema)
  2 backend or DB error
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Imports tolerate missing psycopg / embed deps so --help still works on
# minimal installs; failures are routed through the CLI exit-code path.
try:
    from db import execute, query, vector_literal  # type: ignore
except Exception as _db_import_err:  # noqa: BLE001
    execute = None  # type: ignore
    query = None  # type: ignore
    vector_literal = None  # type: ignore
    _DB_IMPORT_ERR: Exception | None = _db_import_err
else:
    _DB_IMPORT_ERR = None

try:
    from embed_backend import EMBED_DIM, active_model, embed  # type: ignore
except Exception as _emb_import_err:  # noqa: BLE001
    embed = None  # type: ignore
    active_model = None  # type: ignore
    EMBED_DIM = 1024  # type: ignore
    _EMB_IMPORT_ERR: Exception | None = _emb_import_err
else:
    _EMB_IMPORT_ERR = None


SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_SCHEMAS = ("build_loop_memory", "personal_memory")
DEFAULT_TARGET = "bge-m3"
DEFAULT_BATCH = 16


def _safe_schema(s: str) -> str:
    if not SCHEMA_RE.match(s):
        raise ValueError(f"unsafe schema name: {s!r}")
    return s


def _row_text(row: dict) -> str:
    """Compose the embed text the same way write_decision.py does."""
    s = (row.get("subject") or "").strip()
    p = (row.get("predicate") or "").strip()
    o = (row.get("object") or "").strip()
    return f"{s} {p} {o}".strip()


def list_stale(schema: str, target_model: str) -> list[dict]:
    """Return rows whose embedding_model_version != target_model (or NULL)."""
    schema = _safe_schema(schema)
    sql = (
        "SELECT id::text AS id, subject, predicate, object, "
        "       embedding_model_version "
        f"FROM {schema}.semantic_facts "
        "WHERE embedding IS NOT NULL "
        "  AND (embedding_model_version IS NULL "
        "       OR embedding_model_version <> %s)"
    )
    return query(sql, (target_model,))


def reembed_batch(schema: str, rows: list[dict], target_model: str) -> int:
    """Re-embed and UPDATE one batch. Returns the number of rows updated."""
    if not rows:
        return 0
    schema = _safe_schema(schema)
    texts = [_row_text(r) for r in rows]
    vecs = embed(texts)  # batched
    if not isinstance(vecs, list) or len(vecs) != len(rows):
        raise RuntimeError(f"embed batch returned {len(vecs)} vectors for {len(rows)} rows")
    n = 0
    for row, vec in zip(rows, vecs):
        if not isinstance(vec, list) or len(vec) != EMBED_DIM:
            raise RuntimeError(
                f"embed returned dim={len(vec) if isinstance(vec, list) else type(vec).__name__}, "
                f"expected {EMBED_DIM}"
            )
        execute(
            f"UPDATE {schema}.semantic_facts "
            "   SET embedding = %s::vector, "
            "       embedding_model_version = %s "
            " WHERE id::text = %s",
            (vector_literal(vec), target_model, row["id"]),
        )
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help=f"Target embedding model identifier (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--schemas",
        default=",".join(DEFAULT_SCHEMAS),
        help=f"Comma-separated schemas to migrate (default: {','.join(DEFAULT_SCHEMAS)})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH,
        help="Re-embed batch size (default 16; bge-m3 batches well at 16-32)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be re-embedded; do not call embed() or UPDATE",
    )
    parser.add_argument(
        "--verify-active-model",
        action="store_true",
        help=(
            "Before any UPDATE, run one canary embed and assert active_model() == --target. "
            "Prevents accidental migration with a mis-configured backend."
        ),
    )
    args = parser.parse_args(argv)

    if _DB_IMPORT_ERR:
        print(f"db import failed: {_DB_IMPORT_ERR}", file=sys.stderr)
        return 2
    if _EMB_IMPORT_ERR and not args.dry_run:
        print(f"embed_backend import failed: {_EMB_IMPORT_ERR}", file=sys.stderr)
        return 2

    schemas = [s.strip() for s in args.schemas.split(",") if s.strip()]
    try:
        for s in schemas:
            _safe_schema(s)
    except ValueError as e:
        print(f"invalid schema: {e}", file=sys.stderr)
        return 1

    if args.verify_active_model and not args.dry_run:
        try:
            embed("canary")  # force backend select
            am = active_model()
        except Exception as e:  # noqa: BLE001
            print(f"backend canary failed: {e}", file=sys.stderr)
            return 2
        if am != args.target:
            print(
                f"active model {am!r} != target {args.target!r}; "
                "set EMBED_BACKEND=ollama and EMBED_MODEL=bge-m3, then retry.",
                file=sys.stderr,
            )
            return 1

    total_stale = 0
    total_migrated = 0
    started = time.monotonic()
    for s in schemas:
        try:
            stale = list_stale(s, args.target)
        except Exception as e:  # noqa: BLE001
            print(f"[{s}] list_stale failed: {e}", file=sys.stderr)
            return 2
        n = len(stale)
        total_stale += n
        if n == 0:
            print(f"[{s}] up-to-date (0 rows to re-embed)")
            continue
        print(f"[{s}] {n} row(s) need re-embedding to {args.target!r}")
        if args.dry_run:
            for r in stale[:10]:
                v = (r.get("embedding_model_version") or "NULL")
                print(f"  - {r['id'][:8]}  was={v!r}  subject={(r.get('subject') or '')[:60]!r}")
            if n > 10:
                print(f"  ... and {n - 10} more")
            continue

        for i in range(0, n, args.batch_size):
            batch = stale[i : i + args.batch_size]
            try:
                done = reembed_batch(s, batch, args.target)
            except Exception as e:  # noqa: BLE001
                print(f"[{s}] reembed_batch failed at offset {i}: {e}", file=sys.stderr)
                return 2
            total_migrated += done
            print(
                f"[{s}] re-embedded {i + done}/{n} "
                f"({int((time.monotonic() - started) * 1000)}ms total)"
            )

    if args.dry_run:
        print(f"\nDRY RUN: {total_stale} row(s) would be re-embedded")
        return 0
    print(
        f"\nDone. {total_migrated} row(s) re-embedded across "
        f"{len(schemas)} schema(s) in {int((time.monotonic() - started) * 1000)}ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
