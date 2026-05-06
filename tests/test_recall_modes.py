"""Tests for recall.py --mode flag dispatch.

Mocks the embedder + DB-leg helpers so the test runs without Postgres.
The integration test against real data lives in test_recall_acceptance.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import recall as recall_mod  # noqa: E402


def _fake_embed(text):
    """Deterministic 1024-dim vector (identical regardless of text — fine
    for these tests since we mock the leg helpers anyway)."""
    return [0.01] * 1024


@pytest.fixture(autouse=True)
def _mock_embed_and_legs(monkeypatch):
    """Mock _embed + the three leg helpers so run_search exercises the
    dispatch logic without touching Postgres or Ollama."""
    monkeypatch.setattr(recall_mod, "_embed", _fake_embed)

    def _fake_hybrid_facts(q, embedding, schema, limit, floor, **kwargs):
        # Return `limit` rows for vector leg.
        return [
            {"id": f"V{i}", "subject": f"v_subj_{i}", "predicate": "p", "object": f"vector hit {i}",
             "score": 0.9 - i * 0.05, "confidence": 1.0, "status": "active", "metadata": {}}
            for i in range(min(limit, 5))
        ]

    def _fake_keyword_facts(q, schema, limit, floor, **kwargs):
        return [
            {"id": f"K{i}", "subject": f"k_subj_{i}", "predicate": "p", "object": f"keyword hit {i}",
             "score": 0.5 - i * 0.05, "confidence": 1.0, "status": "active", "metadata": {}}
            for i in range(min(limit, 3))
        ]

    monkeypatch.setattr(recall_mod, "hybrid_search_facts", _fake_hybrid_facts)
    # Patch keyword_search at the module-import site (run_search imports
    # it inside the function to keep it lazy).
    import keyword_search as ks_mod
    monkeypatch.setattr(ks_mod, "keyword_search_facts", _fake_keyword_facts)


# ---------------------------------------------------------------------------
# Mode dispatch
# ---------------------------------------------------------------------------

def test_vector_only_runs_legacy_path():
    """vector_only must hit the legacy hybrid_search_facts path; sparse
    leg must NOT run; rerank/multipliers must NOT run."""
    facts, stats = recall_mod.run_search("q", "build_loop_memory", 5, 0.5, mode="vector_only")
    assert stats["mode"] == "vector_only"
    assert stats["vector_count"] == 5
    assert stats["sparse_count"] is None
    assert stats["fused_count"] is None
    assert stats["reranked_count"] is None
    assert stats["embed_ms"] >= 0
    assert stats["sparse_ms"] == 0
    assert stats["rerank_ms"] == 0
    assert stats["multiplier_ms"] == 0
    # Returned rows should match the vector-leg fakes.
    assert {f["id"] for f in facts} == {"V0", "V1", "V2", "V3", "V4"}


def test_sparse_only_skips_embed_call(monkeypatch):
    """sparse_only must NOT call _embed (so it works when Ollama is down)."""
    embed_calls = {"n": 0}

    def _embed_counted(text):
        embed_calls["n"] += 1
        return _fake_embed(text)

    monkeypatch.setattr(recall_mod, "_embed", _embed_counted)
    facts, stats = recall_mod.run_search("q", "build_loop_memory", 5, 0.5, mode="sparse_only")
    assert embed_calls["n"] == 0, "sparse_only must not call _embed"
    assert stats["mode"] == "sparse_only"
    assert stats["sparse_count"] == 3
    assert stats["vector_count"] is None
    assert {f["id"] for f in facts} == {"K0", "K1", "K2"}


def test_hybrid_runs_all_legs():
    """hybrid runs vector + sparse, RRF fuses, then rerank+multipliers
    (rerank gracefully no-ops via DummyEncoder injection if needed)."""
    facts, stats = recall_mod.run_search(
        "q", "build_loop_memory", 5, 0.5, mode="hybrid", rerank_disabled=True,
    )
    assert stats["mode"] == "hybrid"
    assert stats["vector_count"] == 5
    assert stats["sparse_count"] == 3
    assert stats["fused_count"] >= 5
    assert stats["multiplier_ms"] >= 0
    # 5 vector + 3 keyword - 0 overlap (different ids) = 8, trimmed to limit*2=10
    assert len(facts) == 5


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        recall_mod.run_search("q", "build_loop_memory", 5, 0.5, mode="bogus")


def test_hybrid_handles_sparse_leg_failure(monkeypatch):
    """When the sparse leg raises (e.g., search_vector column missing),
    the hybrid pipeline must continue with vector-only results."""
    import keyword_search as ks_mod

    def _exploding_keyword(*args, **kwargs):
        raise RuntimeError("simulated missing column")

    monkeypatch.setattr(ks_mod, "keyword_search_facts", _exploding_keyword)
    facts, stats = recall_mod.run_search(
        "q", "build_loop_memory", 5, 0.5, mode="hybrid", rerank_disabled=True,
    )
    assert stats["sparse_count"] == 0
    # Vector hits still surface.
    assert any(f["id"].startswith("V") for f in facts)


def test_vector_only_byte_identical_returns_legacy_score(monkeypatch):
    """Regression baseline: vector_only must return rows with the
    legacy `score` field intact (cosine_sim * 0.6 + trgm * 0.4 — see
    hybrid_search_facts), NOT an RRF score and NOT a rerank score."""
    facts, _ = recall_mod.run_search("q", "build_loop_memory", 3, 0.5, mode="vector_only")
    # The fake leg returns score=0.9, 0.85, 0.80 — those should be the
    # exact `score` values returned (not transformed by RRF or rerank).
    scores = [f["score"] for f in facts]
    assert scores == [0.9, 0.85, 0.8]
    # And no RRF/rerank artifact fields should exist.
    for f in facts:
        assert "_rrf_score" not in f
        assert "_rerank_score" not in f
        assert "_quality_mult" not in f
