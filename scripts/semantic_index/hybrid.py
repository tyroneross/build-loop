#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Hybrid keyword+embedding rerank for the SQLite semantic index.

Lifts the documented MemTier ceiling: default recall stops being pure
keyword (`scripts/semantic_index/__init__.py::query_facts` legacy path)
and gains an embedding-rerank stage that surfaces synonyms / paraphrases
the keyword scorer misses.

Pipeline (default):
  1. Keyword candidate generation (legacy SQLite scorer, overfetched).
  2. Embed the query via ``embed_backend.embed`` (MLX default, Ollama
     fallback, daemon-aware).
  3. For each candidate that has a persisted embedding (``embedding_json``
     column), compute cosine similarity to the query vector.
  4. Final score = ``keyword_score + cosine_sim``. Rows without a
     persisted embedding keep their keyword-only score.

Graceful fallback (NEVER raises):
  - Embed backend down (MLX broken + Ollama unreachable + daemon off):
    return the pure-keyword ranking.
  - No candidate has a persisted embedding: return the pure-keyword
    ranking. Embedding backfill is opportunistic at write time
    (``upsert_fact(embedding=...)``).

Reuse: the embedding store IS the existing ``embedding_json`` column on
``semantic_facts`` — already populated when callers pass ``embedding=``
to ``upsert_fact``. No new table; no schema migration beyond the
pre-existing ``CREATE TABLE IF NOT EXISTS`` in ``init()``.

Stdlib-only. Math is plain Python (no numpy).
"""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Any, Callable, Optional, Sequence


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two equal-length float vectors.

    Returns 0.0 when either vector is zero-length or zero-norm. Tolerates
    short reads (caller may pass an embedding from a different model
    version with a shorter dimension) by truncating to the shorter length.
    """
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        x = float(a[i])
        y = float(b[i])
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _parse_embedding(raw: Any) -> Optional[list[float]]:
    """Parse the embedding_json column value into a Python list of floats.

    Returns None when the column is NULL, empty, or unparseable. Defensive
    by design: a corrupt embedding row must not crash recall.
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        try:
            return [float(x) for x in raw]
        except (TypeError, ValueError):
            return None
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    try:
        return [float(x) for x in parsed]
    except (TypeError, ValueError):
        return None


def _safe_embed_query(
    query: str,
    embed_fn: Optional[Callable[[str], list[float]]],
) -> Optional[list[float]]:
    """Embed the query with the supplied backend. Returns None on failure.

    ``embed_fn`` is injectable so tests can supply a deterministic backend
    without monkey-patching the module-level ``embed_backend.embed``. When
    None, falls through to ``embed_backend.embed`` lazily — the import is
    deferred so missing optional deps don't fail at module import time.
    """
    if not query or not query.strip():
        return None
    if embed_fn is None:
        try:
            from embed_backend import embed as _embed  # type: ignore  # noqa: PLC0415
        except Exception:  # noqa: BLE001  graceful: backend not importable.
            return None
        embed_fn = _embed
    try:
        vec = embed_fn(query)
    except Exception:  # noqa: BLE001  graceful: backend raised at call time.
        return None
    if not isinstance(vec, list) or not vec:
        return None
    return vec


def rerank_candidates(
    candidates: list[tuple[float, sqlite3.Row]],
    query_embedding: list[float],
) -> list[tuple[float, sqlite3.Row]]:
    """Add cosine boost to candidate scores.

    Returns a new list sorted by (boosted_score DESC). Candidates without a
    parseable embedding keep their original keyword score (no cosine
    boost), so they don't lose ground to no-embedding rows in the pure
    keyword case.

    Score formula: ``final = keyword_score + cosine(query, candidate)``.
    Additive rather than weighted-mix because (a) the keyword score is a
    small integer (token match count) and the cosine is in [-1,1] — the
    addition naturally lets a strong cosine match (≈0.7+) rescue a
    candidate that only matched on 1 keyword token; (b) anything with the
    same keyword score now ranks by semantic similarity. No weighted
    tuning needed; we never trade keyword precision away.
    """
    boosted: list[tuple[float, sqlite3.Row]] = []
    for keyword_score, row in candidates:
        emb = _parse_embedding(row["embedding_json"] if "embedding_json" in row.keys() else None)
        if emb is None:
            boosted.append((float(keyword_score), row))
            continue
        sim = _cosine(query_embedding, emb)
        boosted.append((float(keyword_score) + sim, row))
    boosted.sort(key=lambda item: item[0], reverse=True)
    return boosted


def has_any_embedding(candidates: list[tuple[float, sqlite3.Row]]) -> bool:
    """Cheap probe: does any candidate row carry a parseable embedding?"""
    for _, row in candidates:
        if "embedding_json" not in row.keys():
            return False
        if _parse_embedding(row["embedding_json"]) is not None:
            return True
    return False
