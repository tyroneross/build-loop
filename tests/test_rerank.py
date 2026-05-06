"""Unit tests for scripts/rerank.py — cross-encoder rerank.

Two layers:
  1. Mocked tests via DummyEncoder — always run, no install needed.
  2. Integration test against BAAI/bge-reranker-v2-m3 — gated on
     `pytest.importorskip('sentence_transformers')`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import rerank as rerank_mod  # noqa: E402
from rerank import DummyEncoder, is_available, rerank  # noqa: E402


# ---------------------------------------------------------------------------
# Mocked tests (always run)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """Reset rerank.py's module-level singleton between tests so monkey-
    patches don't leak.

    Also forces in-process routing so a live Phase G daemon on the
    developer's box doesn't bypass the mocked _try_import_cross_encoder
    in tests that exercise the in-process fallback contract. Tests that
    specifically verify daemon routing (test_rerank_daemon.py) opt OUT
    by setting their own state.
    """
    rerank_mod._MODEL = None
    rerank_mod._MODEL_DEVICE = None
    rerank_mod._FALLBACK_LOGGED = False
    rerank_mod._DAEMON_AVAILABLE = None
    monkeypatch.setenv("RERANK_FORCE_INPROCESS", "1")
    yield
    rerank_mod._MODEL = None
    rerank_mod._MODEL_DEVICE = None
    rerank_mod._FALLBACK_LOGGED = False
    rerank_mod._DAEMON_AVAILABLE = None


def test_dummy_encoder_reorders_by_overlap():
    """DummyEncoder ranks by lowercase word overlap. Doc with most
    overlap wins."""
    candidates = [
        {"id": "A", "subject": "x", "predicate": "y", "object": "completely unrelated content"},
        {"id": "B", "subject": "x", "predicate": "y", "object": "package level dead detection in find_dead"},
        {"id": "C", "subject": "x", "predicate": "y", "object": "another irrelevant blob"},
    ]
    out = rerank("package-level dead detection", candidates, top_k=3, model=DummyEncoder())
    assert out[0]["id"] == "B"
    assert "_rerank_score" in out[0]
    assert out[0]["score"] == out[0]["_rerank_score"]


def test_preserves_rrf_score_under_underscore_key():
    candidates = [{"id": "A", "subject": "s", "predicate": "p", "object": "o", "score": 0.0123}]
    out = rerank("q", candidates, top_k=1, model=DummyEncoder())
    assert out[0]["_rrf_score"] == 0.0123


def test_empty_candidates_returns_empty():
    assert rerank("q", [], top_k=10, model=DummyEncoder()) == []


def test_empty_query_preserves_input_order():
    cands = [{"id": "A", "subject": "s", "predicate": "p", "object": "o"} for _ in range(3)]
    cands[0]["id"] = "first"
    cands[1]["id"] = "second"
    out = rerank("", cands, top_k=2, model=DummyEncoder())
    assert [r["id"] for r in out] == ["first", "second"]


def test_pool_size_caps_scoring():
    """When `pool_size` < len(candidates), only the head is scored;
    the tail keeps its incoming order."""
    head_cands = [
        {"id": f"H{i}", "subject": "x", "predicate": "y", "object": f"head doc {i}"}
        for i in range(3)
    ]
    tail_cands = [
        {"id": f"T{i}", "subject": "x", "predicate": "y", "object": "tail unscored"}
        for i in range(3)
    ]
    out = rerank(
        "head doc",
        head_cands + tail_cands,
        top_k=10,
        pool_size=3,
        model=DummyEncoder(),
    )
    # Tail rows must NOT have _rerank_score (they were skipped).
    tail_rows = [r for r in out if r["id"].startswith("T")]
    assert all("_rerank_score" not in r for r in tail_rows)
    # And they keep their incoming relative order.
    assert [r["id"] for r in tail_rows] == ["T0", "T1", "T2"]


def test_top_k_cap():
    cands = [{"id": str(i), "subject": "x", "predicate": "y", "object": "doc"} for i in range(10)]
    out = rerank("q", cands, top_k=3, model=DummyEncoder())
    assert len(out) == 3


def test_top_k_zero_returns_all():
    cands = [{"id": str(i), "subject": "x", "predicate": "y", "object": "doc"} for i in range(5)]
    out = rerank("q", cands, top_k=0, model=DummyEncoder())
    assert len(out) == 5


def test_graceful_fallback_when_st_missing(monkeypatch):
    """If sentence-transformers isn't importable, return RRF order
    unchanged with a one-time log; never raise."""
    monkeypatch.setattr(rerank_mod, "_try_import_cross_encoder", lambda: None)
    cands = [{"id": "A", "subject": "x", "predicate": "y", "object": "o"} for _ in range(3)]
    cands[0]["id"] = "first"
    cands[1]["id"] = "second"
    cands[2]["id"] = "third"
    out = rerank("q", cands, top_k=2)
    assert [r["id"] for r in out] == ["first", "second"]


def test_predict_exception_falls_back_to_rrf_order(monkeypatch):
    """If the encoder raises during predict(), recall must not crash."""

    class ExplodingEncoder:
        def predict(self, pairs):
            raise RuntimeError("simulated MPS oom")

    cands = [{"id": str(i), "subject": "x", "predicate": "y", "object": "o"} for i in range(3)]
    out = rerank("q", cands, top_k=2, model=ExplodingEncoder())
    assert len(out) == 2
    assert [r["id"] for r in out] == ["0", "1"]


def test_score_count_mismatch_falls_back(monkeypatch):
    """If the encoder returns the wrong number of scores, fall back."""

    class WrongCountEncoder:
        def predict(self, pairs):
            return [0.5]  # wrong: should be one per pair

    cands = [{"id": str(i), "subject": "x", "predicate": "y", "object": "o"} for i in range(3)]
    out = rerank("q", cands, top_k=2, model=WrongCountEncoder())
    assert [r["id"] for r in out] == ["0", "1"]


# ---------------------------------------------------------------------------
# Integration tests (gated on sentence-transformers being installed)
# ---------------------------------------------------------------------------

def test_is_available_matches_import():
    """is_available() must reflect whether the import would succeed."""
    try:
        import sentence_transformers  # noqa: F401  pragma: no cover
        assert is_available() is True
    except ImportError:
        assert is_available() is False


@pytest.mark.integration
def test_real_cross_encoder_separates_relevant_from_noise():
    """End-to-end with the real BAAI/bge-reranker-v2-m3 model.

    Skipped automatically when sentence-transformers (or torch) isn't
    installed. Slow first call (~3-5s model load + ~1.5s warmup); cached
    by the module singleton thereafter.
    """
    pytest.importorskip("sentence_transformers")
    pytest.importorskip("torch")
    candidates = [
        {"id": "noise1", "subject": "x", "predicate": "y", "object": "completely unrelated about the weather"},
        {"id": "noise2", "subject": "x", "predicate": "y", "object": "another irrelevant blob with random words"},
        {"id": "target", "subject": "x", "predicate": "y", "object": "package level dead detection in find_dead"},
        {"id": "noise3", "subject": "x", "predicate": "y", "object": "yet another distractor that has no signal"},
    ]
    out = rerank("package-level dead detection", candidates, top_k=4)
    assert out[0]["id"] == "target", f"target should rank first; got {[r['id'] for r in out]}"
    # Real cross-encoder should produce a clear separation.
    target_score = out[0]["_rerank_score"]
    next_score = max(r.get("_rerank_score", 0.0) for r in out[1:])
    assert target_score > 0.5, f"target rerank score too low: {target_score}"
    assert target_score > next_score * 5, (
        f"target should dominate distractors; got {target_score} vs {next_score}"
    )
