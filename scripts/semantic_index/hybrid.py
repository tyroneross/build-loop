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
  2. Embed the query through the selected, verified backend/model.
  3. For each candidate that has a persisted embedding (``embedding_json``
     column) with verified matching model provenance, compute cosine similarity.
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

    Returns 0.0 for empty, zero-norm, or unequal-length vectors.
    """
    n = len(a)
    if n == 0 or len(b) != n:
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
    """Parse one finite numeric vector; malformed data has no dense score."""
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            raw = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return None
    if not isinstance(raw, list) or not raw:
        return None
    result = []
    for value in raw:
        if type(value) not in (int, float):
            return None
        try:
            number = float(value)
        except OverflowError:
            return None
        if not math.isfinite(number):
            return None
        result.append(number)
    return result


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
    return _parse_embedding(vec)


def prepare_candidates(candidates, embedding_model):
    """Read each row's provenance and vector once; retain keyword fallback."""
    from embed_backend import same_model
    prepared = []
    for score, row in candidates:
        try:
            metadata = json.loads(row["metadata_json"]) if "metadata_json" in row.keys() else {}
        except (ValueError, TypeError):
            metadata = {}
        trusted = (isinstance(metadata, dict) and metadata.get("embedding_identity_verified") is True
                   and same_model(metadata.get("embedding_model_version"), embedding_model))
        vector = _parse_embedding(row["embedding_json"]) if trusted and "embedding_json" in row.keys() else None
        if score > 0 or vector is not None:
            prepared.append((float(score), row, vector))
    return prepared


def rerank_prepared(prepared, query_embedding):
    ranked = []
    for score, row, vector in prepared:
        compatible = vector is not None and len(vector) == len(query_embedding)
        if score > 0 or compatible:
            ranked.append((score + (_cosine(query_embedding, vector) if compatible else 0.0), row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def rerank_candidates(candidates, query_embedding, *, embedding_model=None):
    return rerank_prepared(prepare_candidates(candidates, embedding_model), query_embedding)


def has_any_embedding(candidates: list[tuple[float, sqlite3.Row]]) -> bool:
    """Cheap probe: does any candidate row carry a parseable embedding?

    Per-row guard: a candidate without the column is skipped via ``continue``,
    not short-circuited with ``return False``. Heterogeneous candidate sets
    (e.g. a mid-migration mix of with/without-embedding rows, or a unioned
    cursor from a future multi-source path) must still surface "yes" when
    ANY row carries a vector. The earlier ``return False`` on the first
    column-less row hid valid embedding rows downstream and turned hybrid
    silently into keyword for the whole query — the exact dormancy this
    rerank was meant to lift.
    """
    for _, row in candidates:
        if "embedding_json" not in row.keys():
            continue
        if _parse_embedding(row["embedding_json"]) is not None:
            return True
    return False
