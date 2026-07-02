#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Query the SQLite lessons index with progressive disclosure.

query(goal_text, project, limit) flow:
  1. FTS5 BM25 match on goal_text, scoped to project + _unscoped.
  2. If embeddings table is populated AND embed backend is available,
     compute goal embedding and cosine-rerank the BM25 candidates (hybrid).
  3. Return top-N results as list of dicts.

Graceful degradation: if the embed backend is unavailable or the embeddings
table is empty, returns pure BM25 results with no error.
"""
from __future__ import annotations

import math
import os
import struct
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = HERE.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import importlib.util as _iutil  # noqa: E402

def _load_schema_module():
    spec = _iutil.spec_from_file_location(
        "lessons_index._schema", HERE / "schema.py"
    )
    mod = _iutil.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_schema_mod = _load_schema_module()
open_db = _schema_mod.open_db

_SNIPPET_TOKENS = 64  # chars for snippet extraction


def _snippet(text: str, query_words: list[str], max_chars: int = _SNIPPET_TOKENS * 4) -> str:
    """Return a short excerpt near the first query word hit."""
    text_lower = text.lower()
    best_pos = len(text)
    for word in query_words:
        pos = text_lower.find(word.lower())
        if 0 <= pos < best_pos:
            best_pos = pos
    start = max(0, best_pos - 80)
    end = min(len(text), start + max_chars)
    excerpt = text[start:end].strip()
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(text):
        excerpt = excerpt + "…"
    return excerpt


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _unpack_floats(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _try_embed(text: str) -> list[float] | None:
    """Return embedding vector or None if backend unavailable."""
    if os.environ.get("EMBED_BACKEND_UNAVAILABLE"):
        return None
    try:
        import embed_backend as _eb  # type: ignore  # noqa: PLC0415
        vec = _eb.embed(text)
        return list(vec)
    except Exception:  # noqa: BLE001
        return None


def query(
    goal_text: str,
    project: str | None = None,
    limit: int = 5,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return top-N lessons matching goal_text.

    Scoping: includes rows where project matches AND rows where
    project='_unscoped' (top-level lanes). When project is None, returns
    results from all projects.

    Each result dict: {name, description, snippet, score, source_path, lane}.
    """
    from _paths import memory_store_root  # type: ignore  # noqa: PLC0415

    if db_path is None:
        db_path = memory_store_root() / "indexes" / "lessons_index.db"
    db_path = Path(db_path)

    if not db_path.exists():
        return []

    conn = open_db(db_path)
    try:
        return _run_query(conn, goal_text, project, limit)
    finally:
        conn.close()


def _run_query(
    conn,
    goal_text: str,
    project: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    # Check if FTS table has any content.
    fts_count = conn.execute("SELECT COUNT(*) FROM facts_fts").fetchone()[0]
    if fts_count == 0:
        # FTS table may be behind facts (e.g. if triggers weren't active).
        # Rebuild from facts.
        _rebuild_fts(conn)
        fts_count = conn.execute("SELECT COUNT(*) FROM facts_fts").fetchone()[0]
    if fts_count == 0:
        return []

    # Sanitize goal_text for FTS5: escape special chars, wrap in quotes for
    # phrase matching fallback if the raw query fails.
    fts_query = _sanitize_fts_query(goal_text)

    # BM25 candidates: fetch up to limit*4 for reranking headroom.
    candidates = _fts_candidates(conn, fts_query, project, limit * 4)
    if not candidates:
        # FTS found nothing — last resort LIKE on name + description.
        candidates = _like_fallback(conn, goal_text, project, limit)

    if not candidates:
        return []

    # Hybrid rerank if embeddings are available.
    goal_vec = _try_embed(goal_text)
    if goal_vec is not None and _embeddings_populated(conn):
        candidates = _cosine_rerank(conn, candidates, goal_vec)

    # Trim to limit.
    results = candidates[:limit]

    # Build output dicts.
    query_words = [w for w in goal_text.lower().split() if len(w) > 2]
    out = []
    for row in results:
        out.append({
            "name": row["name"],
            "description": row["description"],
            "snippet": _snippet(row["body"], query_words),
            "score": float(row.get("score", 0.0)),
            "source_path": row["source_path"],
            "lane": row["lane"],
        })
    return out


def _sanitize_fts_query(text: str) -> str:
    """Strip FTS5 syntax chars and return a safe query string."""
    # Remove FTS5 special operators to avoid syntax errors on raw user input.
    sanitized = text.replace('"', " ").replace("'", " ")
    sanitized = sanitized.replace("^", " ").replace("*", " ")
    # Collapse whitespace, take individual tokens.
    tokens = [t for t in sanitized.split() if t]
    if not tokens:
        return '""'
    # Simple OR: each token must match somewhere.
    return " OR ".join(tokens)


def _fts_candidates(conn, fts_query: str, project: str | None, n: int) -> list:
    """Run BM25 FTS5 query, return rows ordered by relevance."""
    try:
        if project is not None:
            sql = """
                SELECT f.id, f.lane, f.project, f.name, f.description,
                       f.body, f.source_path,
                       bm25(facts_fts) AS score
                FROM facts_fts
                JOIN facts f ON facts_fts.rowid = f.id
                WHERE facts_fts MATCH ?
                  AND (f.project = ? OR f.project = '_unscoped')
                ORDER BY score
                LIMIT ?
            """
            rows = conn.execute(sql, (fts_query, project, n)).fetchall()
        else:
            sql = """
                SELECT f.id, f.lane, f.project, f.name, f.description,
                       f.body, f.source_path,
                       bm25(facts_fts) AS score
                FROM facts_fts
                JOIN facts f ON facts_fts.rowid = f.id
                WHERE facts_fts MATCH ?
                ORDER BY score
                LIMIT ?
            """
            rows = conn.execute(sql, (fts_query, n)).fetchall()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def _like_fallback(conn, text: str, project: str | None, n: int) -> list:
    """Simple LIKE fallback when FTS returns nothing."""
    pattern = f"%{text[:50]}%"
    try:
        if project is not None:
            sql = """
                SELECT id, lane, project, name, description, body, source_path,
                       0.0 AS score
                FROM facts
                WHERE (name LIKE ? OR description LIKE ? OR body LIKE ?)
                  AND (project = ? OR project = '_unscoped')
                LIMIT ?
            """
            rows = conn.execute(sql, (pattern, pattern, pattern, project, n)).fetchall()
        else:
            sql = """
                SELECT id, lane, project, name, description, body, source_path,
                       0.0 AS score
                FROM facts
                WHERE name LIKE ? OR description LIKE ? OR body LIKE ?
                LIMIT ?
            """
            rows = conn.execute(sql, (pattern, pattern, pattern, n)).fetchall()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def _embeddings_populated(conn) -> bool:
    """True if there are any rows in the embeddings table."""
    count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    return count > 0


def _cosine_rerank(conn, candidates: list[dict], goal_vec: list[float]) -> list[dict]:
    """Rerank BM25 candidates by cosine similarity of stored embeddings.

    Returns candidates sorted by cosine similarity (descending). Candidates
    without an embedding retain their BM25 rank at the end.
    """
    ids = [r["id"] for r in candidates]
    placeholders = ",".join("?" * len(ids))
    emb_rows = conn.execute(
        f"SELECT fact_id, vec FROM embeddings WHERE fact_id IN ({placeholders})",  # nosec: only ?-placeholders / constant fragments interpolated; values bound as params
        ids,
    ).fetchall()
    emb_map = {row["fact_id"]: _unpack_floats(row["vec"]) for row in emb_rows}

    scored = []
    unscored = []
    for r in candidates:
        if r["id"] in emb_map:
            cos = _cosine(goal_vec, emb_map[r["id"]])
            scored.append({**r, "score": cos})
        else:
            unscored.append(r)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored + unscored


def _rebuild_fts(conn) -> None:
    """Populate FTS from the facts table (recovery path)."""
    conn.execute("DELETE FROM facts_fts")
    conn.execute(
        "INSERT INTO facts_fts(rowid, name, description, body) "
        "SELECT id, name, description, body FROM facts"
    )
    conn.commit()
