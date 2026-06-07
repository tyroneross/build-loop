#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the hybrid keyword+embedding rerank path in semantic_index.

Covers the three P1 acceptance criteria:
  1. Synonym/paraphrase: a query that keyword ranking PUTS WRONG-RANK
     (or misses) gets the right top hit via dense rerank.
  2. Graceful fallback: with no embed backend (force-fail), recall
     returns keyword results and never raises.
  3. Migration idempotency: re-running ``init()`` against an existing
     DB is a no-op; pre-existing rows survive.

The tests inject a deterministic ``embed_fn`` so we don't depend on MLX
or Ollama being installed. The synonym test uses a tiny 4-dim vector
space where curated coordinates make ``alpha → beta`` cosine-close while
keyword overlap is zero.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from semantic_index import (  # noqa: E402
    init,
    query_facts,
    upsert_fact,
)
from semantic_index.hybrid import (  # noqa: E402
    _cosine,
    _parse_embedding,
    _safe_embed_query,
    has_any_embedding,
)


# ---------------------------------------------------------------- helpers


def _make_db(tmp_path: Path) -> Path:
    return tmp_path / "hybrid.sqlite"


# Tiny 4-dim semantic space. Two clusters:
#   - "vehicle" cluster: synonyms map to roughly the same direction.
#   - "weather" cluster: orthogonal.
_VEC = {
    # vehicle cluster
    "auto": [1.0, 0.0, 0.0, 0.0],
    "car": [0.95, 0.31, 0.0, 0.0],
    "automobile": [0.92, 0.39, 0.0, 0.0],
    "vehicle": [0.93, 0.37, 0.0, 0.0],
    # weather cluster (orthogonal)
    "rain": [0.0, 0.0, 1.0, 0.0],
    "storm": [0.0, 0.0, 0.95, 0.31],
    # generic/noise
    "thing": [0.0, 0.0, 0.0, 1.0],
}


def _embed_fake(text: str) -> list[float]:
    """Deterministic embedder: returns the curated vector for the first
    known token in the text, else a neutral vector.
    """
    tokens = text.lower().split()
    for t in tokens:
        t = t.strip(".,!?;:")
        if t in _VEC:
            return list(_VEC[t])
    return [0.0, 0.0, 0.0, 1.0]


# ---------------------------------------------------------------- 1. synonym


def test_dense_rerank_surfaces_synonym_keyword_misses(tmp_path: Path) -> None:
    """The headline P1 acceptance: a query that keyword-only RANKS WRONG
    gets the right top hit once dense rerank is on.

    Setup: 3 facts.
      F1 "auto cluster: a transport device"  (semantically the right hit
                                              for query "automobile")
      F2 "thing about the auto cluster"      (keyword decoy: matches the
                                              query token "the" but not
                                              semantically aligned)
      F3 "rain weather report"               (unrelated)

    Query: "automobile". Keyword-only puts F2 ahead (decoy token "the" +
    semantic-irrelevant noise) — dense rerank pushes F1 to the top
    because ``automobile ≈ auto`` in cosine space.
    """
    db = _make_db(tmp_path)
    upsert_fact(
        subject="F1",
        predicate="cluster",
        object_text="auto cluster device",
        project="p",
        embedding=_VEC["auto"],
        db_path=db,
    )
    upsert_fact(
        subject="F2",
        predicate="thing",
        object_text="the thing notes",
        project="p",
        embedding=_VEC["thing"],
        db_path=db,
    )
    upsert_fact(
        subject="F3",
        predicate="weather",
        object_text="rain weather report",
        project="p",
        embedding=_VEC["rain"],
        db_path=db,
    )

    keyword_results = query_facts(
        query="automobile", project="p", db_path=db, mode="keyword",
    )
    hybrid_results = query_facts(
        query="automobile",
        project="p",
        db_path=db,
        mode="hybrid",
        embed_fn=_embed_fake,
    )

    # Control: keyword-only completely MISSES (the query token "automobile"
    # has zero substring hits across F1/F2/F3).
    assert keyword_results == [], (
        f"keyword-only must miss this synonym query; got {[r['subject'] for r in keyword_results]}"
    )

    # Treatment: hybrid SURFACES F1 (auto ≈ automobile in cosine space).
    assert hybrid_results, "hybrid mode must surface at least one result"
    assert hybrid_results[0]["subject"] == "F1", (
        f"hybrid top result expected F1 (auto cluster device); got "
        f"{[r['subject'] for r in hybrid_results]}"
    )


# ---------------------------------------------------------------- 2. fallback


def test_fallback_to_keyword_when_embed_backend_unavailable(
    tmp_path: Path,
) -> None:
    """When the embed backend raises, recall must NOT crash. It must
    degrade to keyword ranking, return successfully, exit cleanly.
    """
    db = _make_db(tmp_path)
    upsert_fact(
        subject="alpha",
        predicate="describes",
        object_text="contains the word adapter explicitly",
        project="p",
        embedding=_VEC["auto"],
        db_path=db,
    )
    upsert_fact(
        subject="beta",
        predicate="describes",
        object_text="unrelated cluster",
        project="p",
        embedding=_VEC["rain"],
        db_path=db,
    )

    def broken_embed(_text: str) -> list[float]:
        raise RuntimeError("simulated MLX + Ollama outage")

    out = query_facts(
        query="adapter",
        project="p",
        db_path=db,
        mode="hybrid",
        embed_fn=broken_embed,
    )

    # Got at least one result back (alpha matches keyword "adapter") and
    # no exception was raised — the headline fallback contract.
    assert out, "fallback must return keyword results, not empty"
    assert out[0]["subject"] == "alpha", out


def test_fallback_when_no_candidate_has_embedding(tmp_path: Path) -> None:
    """When zero candidates carry an embedding, hybrid must transparently
    degrade to pure keyword ranking — never call the embedder.

    NOTE: post-f1, ``upsert_fact`` auto-embeds on the write path by
    default. To assert the no-embeddings-anywhere state we must opt out
    via ``auto_embed=False`` (this is the legacy behavior). The new
    production behavior is covered by
    ``test_production_path_synonym_hit_without_explicit_embedding``.
    """
    db = _make_db(tmp_path)
    upsert_fact(
        subject="only-keyword-fact",
        predicate="p",
        object_text="adapter boundary lesson",
        project="p",
        db_path=db,
        auto_embed=False,
    )
    calls = {"n": 0}

    def tracker_embed(text: str) -> list[float]:
        calls["n"] += 1
        return _VEC.get("auto", [0.0, 0.0, 0.0, 1.0])

    out = query_facts(
        query="adapter",
        project="p",
        db_path=db,
        mode="hybrid",
        embed_fn=tracker_embed,
    )

    assert out, "must return keyword candidates even with no embeddings"
    assert out[0]["subject"] == "only-keyword-fact"
    # The embedder was never invoked — no candidate had an embedding.
    assert calls["n"] == 0, (
        f"embed_fn should not be called when no candidates carry vectors; "
        f"calls={calls['n']}"
    )


def test_safe_embed_query_swallows_runtime_errors() -> None:
    """Unit-level guard: ``_safe_embed_query`` must return None on any
    raise. This is the seam that turns backend outages into fallback."""

    def boom(_text: str) -> list[float]:
        raise RuntimeError("backend down")

    assert _safe_embed_query("anything", boom) is None
    assert _safe_embed_query("", _embed_fake) is None
    assert _safe_embed_query("auto", _embed_fake) == _VEC["auto"]


# ---------------------------------------------------------------- 3. migration


def test_init_is_idempotent(tmp_path: Path) -> None:
    """Re-running ``init()`` against an existing DB is a no-op: pre-existing
    rows survive, schema stays intact, no exception raised.
    """
    db = _make_db(tmp_path)
    # First init creates the table + indexes.
    init(db)
    upsert_fact(
        subject="kept",
        predicate="p",
        object_text="should survive re-init",
        project="p",
        embedding=_VEC["auto"],
        db_path=db,
    )
    # Second init must be a no-op (idempotent) — no schema reset.
    init(db)
    # Third init for good measure (proves N-times idempotency).
    init(db)

    # Verify the row survived.
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT subject, embedding_json FROM semantic_facts"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "kept"
    # embedding_json column exists AND the value persisted.
    parsed = _parse_embedding(rows[0][1])
    assert parsed is not None and parsed == _VEC["auto"]


def test_init_creates_embedding_column_on_fresh_db(tmp_path: Path) -> None:
    """Fresh DB must have ``embedding_json`` column from the start —
    the embedding store IS the column, no separate migration step."""
    db = _make_db(tmp_path)
    init(db)
    conn = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(semantic_facts)").fetchall()}
    finally:
        conn.close()
    assert "embedding_json" in cols, f"missing embedding_json column; got {cols}"


# ---------------------------------------------------------------- math


def test_cosine_orthogonal_is_zero() -> None:
    assert _cosine([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]) == 0.0


def test_cosine_identical_is_one() -> None:
    assert _cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_zero_vector_returns_zero() -> None:
    assert _cosine([0.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_parse_embedding_handles_corrupt_json() -> None:
    assert _parse_embedding(None) is None
    assert _parse_embedding("") is None
    assert _parse_embedding("not json") is None
    assert _parse_embedding('{"not": "a list"}') is None
    assert _parse_embedding('[1, "bad", 3]') is None
    assert _parse_embedding("[1.0, 2.0, 3.0]") == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------- f3: has_any_embedding


def test_has_any_embedding_skips_column_less_rows_finds_later_hit(tmp_path: Path) -> None:
    """Regression for the dormancy bug: a candidate row WITHOUT the column
    must not short-circuit the probe. A later row WITH a valid embedding
    must still cause the probe to return True.

    Before the fix: ``has_any_embedding`` returned False on the first
    column-less row, hybrid silently downgraded to keyword, and dense
    rerank never fired against any heterogeneous candidate set.
    """
    # Build two minimal sqlite3.Row stand-ins via a real SQLite conn.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Row 1: no embedding_json column at all.
    conn.execute("CREATE TABLE no_emb (subject TEXT)")
    conn.execute("INSERT INTO no_emb (subject) VALUES ('without-emb')")
    row_without = conn.execute("SELECT * FROM no_emb").fetchone()
    # Row 2: has embedding_json column and a valid vector.
    conn.execute("CREATE TABLE with_emb (subject TEXT, embedding_json TEXT)")
    conn.execute(
        "INSERT INTO with_emb (subject, embedding_json) VALUES ('with-emb', ?)",
        ("[0.1, 0.2, 0.3]",),
    )
    row_with = conn.execute("SELECT * FROM with_emb").fetchone()
    conn.close()

    # Bug repro order: column-less row FIRST, embedded row SECOND.
    candidates = [(1.0, row_without), (1.0, row_with)]
    assert has_any_embedding(candidates) is True, (
        "has_any_embedding must continue past column-less rows; "
        "before the fix this returned False on the first row and hybrid "
        "silently downgraded to keyword."
    )

    # Sanity: all column-less → False.
    assert has_any_embedding([(1.0, row_without)]) is False

    # Sanity: all-embedded → True.
    assert has_any_embedding([(1.0, row_with)]) is True


# ---------------------------------------------------------------- f1/f2: production write+read path


def test_production_path_synonym_hit_without_explicit_embedding(tmp_path: Path) -> None:
    """The headline f2 regression. ``upsert_fact`` is called WITHOUT
    ``embedding=`` and WITHOUT a curated vector — only an ``embed_fn``
    that the write path uses to populate ``embedding_json`` automatically.
    Then we read via the SAME default path (``query_facts``, no explicit
    ``embedding`` arg anywhere), inject the SAME embedder, and verify
    that a synonym query (zero keyword overlap) returns the right hit.

    This test would have FAILED before f1 because:
      - upsert_fact stored embedding_json=NULL,
      - has_any_embedding returned False (no candidate had a vector),
      - hybrid silently degraded to keyword,
      - keyword has zero overlap on the synonym query → empty result.

    After f1: ``upsert_fact(auto_embed=True)`` (default) calls the
    injected ``embed_fn`` and persists the vector, so the query hits.
    """
    db = _make_db(tmp_path)
    # NO explicit embedding= on the write — production code never passes one.
    upsert_fact(
        subject="F1",
        predicate="cluster",
        object_text="auto cluster device",
        project="p",
        db_path=db,
        embed_fn=_embed_fake,
    )
    upsert_fact(
        subject="F2",
        predicate="thing",
        object_text="the thing notes",
        project="p",
        db_path=db,
        embed_fn=_embed_fake,
    )
    upsert_fact(
        subject="F3",
        predicate="weather",
        object_text="rain weather report",
        project="p",
        db_path=db,
        embed_fn=_embed_fake,
    )

    # Verify embeddings were auto-populated on write.
    conn = sqlite3.connect(str(db))
    null_count = conn.execute(
        "SELECT COUNT(*) FROM semantic_facts WHERE embedding_json IS NULL"
    ).fetchone()[0]
    conn.close()
    assert null_count == 0, (
        f"production write path must auto-populate embedding_json; "
        f"got {null_count} NULL rows"
    )

    # Keyword-only sanity: synonym query has zero substring hits.
    keyword_results = query_facts(
        query="automobile", project="p", db_path=db, mode="keyword",
    )
    assert keyword_results == []

    # Default hybrid path — same embed_fn on the read side, no explicit
    # embedding anywhere. Without f1's fix, this would also be empty.
    hybrid_results = query_facts(
        query="automobile",
        project="p",
        db_path=db,
        mode="hybrid",
        embed_fn=_embed_fake,
    )
    assert hybrid_results, (
        "production path synonym hit failed — hybrid is dormant. "
        "Auto-embed-on-write + read-path embed_fn must both fire."
    )
    assert hybrid_results[0]["subject"] == "F1", (
        f"expected F1 (auto cluster) as the top synonym hit; got "
        f"{[r['subject'] for r in hybrid_results]}"
    )


def test_upsert_fact_auto_embed_false_keeps_null(tmp_path: Path) -> None:
    """Opt-out preserves legacy behavior — used by the backfill path
    (which controls the embedder itself) and by tests that want to
    assert the NULL-embedding-then-backfill flow."""
    db = _make_db(tmp_path)
    upsert_fact(
        subject="no-vec",
        predicate="p",
        object_text="text",
        project="p",
        db_path=db,
        embed_fn=_embed_fake,  # provided but ignored
        auto_embed=False,
    )
    conn = sqlite3.connect(str(db))
    raw = conn.execute(
        "SELECT embedding_json FROM semantic_facts WHERE subject='no-vec'"
    ).fetchone()[0]
    conn.close()
    assert raw is None


def test_upsert_fact_write_path_swallows_embed_failure(tmp_path: Path) -> None:
    """Write contract: backend down on auto-embed MUST NOT raise. Row
    lands with NULL embedding_json; backfill can populate later."""
    db = _make_db(tmp_path)

    def broken(_t: str) -> list[float]:
        raise RuntimeError("simulated backend down at write time")

    # Must not raise — this is the production-availability contract.
    upsert_fact(
        subject="written-despite-outage",
        predicate="p",
        object_text="content",
        project="p",
        db_path=db,
        embed_fn=broken,
    )

    conn = sqlite3.connect(str(db))
    raw = conn.execute(
        "SELECT embedding_json FROM semantic_facts WHERE subject='written-despite-outage'"
    ).fetchone()[0]
    conn.close()
    assert raw is None  # NULL — backfill can pick this up later.


def test_read_semantic_passes_embed_fn_through_to_query_facts(tmp_path: Path) -> None:
    """The f1(b) wiring: ``memory_facade.semantic.read_semantic`` must
    forward an ``embed_fn`` to ``query_facts`` so hybrid rerank actually
    fires on the production read path.

    Verified by writing rows that pass synonym hits via embedding (zero
    keyword overlap), then calling read_semantic with the same embedder.
    """
    db = _make_db(tmp_path)
    upsert_fact(
        subject="F1",
        predicate="cluster",
        object_text="auto cluster device",
        project="p",
        db_path=db,
        embed_fn=_embed_fake,
    )

    # We must point read_semantic's underlying query_facts at OUR temp DB.
    # The facade resolves the path via semantic_index.default_db_path(); we
    # monkey-patch _db_path for the duration of this call.
    import semantic_index as si  # noqa: PLC0415
    from memory_facade import semantic as facade  # noqa: PLC0415

    original_path_fn = si._db_path
    si._db_path = lambda _p=None: db  # type: ignore[assignment]
    try:
        out, reasons = facade.read_semantic(
            workdir=tmp_path,
            query="automobile",
            limit=5,
            project="p",
            skip_postgres=True,
            embed_fn=_embed_fake,
        )
    finally:
        si._db_path = original_path_fn

    assert out, f"read_semantic must surface the synonym via embed_fn; reasons={reasons}"
    assert out[0]["subject"] == "F1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
