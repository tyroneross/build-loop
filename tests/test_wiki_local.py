"""Tests for scripts/wiki_local.py — Phase I in-process wiki retrieval.

The unit tests stub out the Ollama embed call and the embeddings.json file
so they never touch a live vault. The integration test is gated behind
both the vault store presence AND Ollama reachability so it skips cleanly
on machines without the wiki.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Clear the in-memory store between tests so cache pollution doesn't leak."""
    import wiki_local  # type: ignore  # noqa: PLC0415
    wiki_local.reset_for_tests()
    yield
    wiki_local.reset_for_tests()


@pytest.fixture
def fake_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Synthetic vault with a tiny 2-chunk embeddings.json under tmp_path."""
    vault = tmp_path / "vault"
    (vault / ".vector").mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    payload = {
        "provider": "ollama",
        "model": "nomic-embed-text",
        "dimension": 4,
        "chunks": [
            {
                "page_id": "concept-hybrid-search",
                "page_path": "wiki/concepts/concept-hybrid-search.md",
                "heading": "Tradeoffs",
                "content_preview": "Hybrid retrieval merges vector and keyword signals via RRF for higher recall.",
                "embedding": [1.0, 0.0, 0.0, 0.0],
                "title": "Hybrid Search",
            },
            {
                "page_id": "concept-other",
                "page_path": "wiki/concepts/concept-other.md",
                "heading": "TL;DR",
                "content_preview": "Unrelated topic about pumpkins.",
                "embedding": [0.0, 1.0, 0.0, 0.0],
                "title": "Other",
            },
        ],
    }
    (vault / ".vector" / "embeddings.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("BUILD_LOOP_VAULT_ROOT", str(vault))
    # Redirect the pickle cache to the tmp dir so test runs don't pollute
    # the real ~/.local/state directory.
    import wiki_local  # type: ignore  # noqa: PLC0415
    monkeypatch.setattr(wiki_local, "STATE_DIR", state, raising=True)
    monkeypatch.setattr(wiki_local, "CACHE_FILE", state / "wiki-store.pkl", raising=True)
    return vault


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

def test_is_available_true_when_embeddings_present(fake_vault: Path) -> None:
    import wiki_local  # type: ignore  # noqa: PLC0415
    assert wiki_local.is_available() is True


def test_is_available_false_when_embeddings_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUILD_LOOP_VAULT_ROOT", str(tmp_path / "no_such_vault"))
    import wiki_local  # type: ignore  # noqa: PLC0415
    assert wiki_local.is_available() is False


def test_default_vault_root_uses_current_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUILD_LOOP_VAULT_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    import wiki_local  # type: ignore  # noqa: PLC0415
    assert wiki_local._vault_root() == tmp_path / "ObsidianVault"


# ---------------------------------------------------------------------------
# cosine + lexical math
# ---------------------------------------------------------------------------

def test_cosine_orthogonal_zero() -> None:
    import wiki_local  # type: ignore  # noqa: PLC0415
    assert wiki_local.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_identical_one() -> None:
    import wiki_local  # type: ignore  # noqa: PLC0415
    assert wiki_local.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_zero_vector_returns_zero() -> None:
    import wiki_local  # type: ignore  # noqa: PLC0415
    assert wiki_local.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_lexical_score_matches_overlap() -> None:
    import wiki_local  # type: ignore  # noqa: PLC0415
    chunk = {"title": "Hybrid Search", "heading": "Tradeoffs",
             "content_preview": "vector and keyword via RRF"}
    high = wiki_local.lexical_score("hybrid search vector RRF", chunk)
    low = wiki_local.lexical_score("nothing related at all", chunk)
    assert high > low


# ---------------------------------------------------------------------------
# Search end-to-end (mocked Ollama)
# ---------------------------------------------------------------------------

def test_search_surfaces_relevant_chunk_above_unrelated(fake_vault: Path) -> None:
    """Query embedding [1,0,0,0] aligns with chunk #1; unrelated chunk loses."""
    import wiki_local  # type: ignore  # noqa: PLC0415
    with patch.object(wiki_local, "_ollama_embed_query", return_value=[1.0, 0.0, 0.0, 0.0]):
        results = wiki_local.search("hybrid retrieval", k=2)
    assert len(results) >= 1
    assert results[0]["subject"] == "concept-hybrid-search"
    assert results[0]["source"] == "wiki"
    # Result shape parity with wiki_client.parse_search_output
    for required in ("id", "subject", "predicate", "object", "score", "wiki_path"):
        assert required in results[0]


def test_search_returns_empty_for_blank_query(fake_vault: Path) -> None:
    import wiki_local  # type: ignore  # noqa: PLC0415
    assert wiki_local.search("", k=5) == []
    assert wiki_local.search("   ", k=5) == []


def test_search_dedupes_by_page(fake_vault: Path, tmp_path: Path) -> None:
    """Two chunks of the same page collapse to one row, strongest score wins."""
    # Rewrite the vault with two chunks sharing a page_id.
    vault = fake_vault
    payload = json.loads((vault / ".vector" / "embeddings.json").read_text())
    payload["chunks"] = [
        {**payload["chunks"][0], "heading": "Section A"},
        {**payload["chunks"][0], "heading": "Section B", "embedding": [0.9, 0.1, 0.0, 0.0]},
    ]
    (vault / ".vector" / "embeddings.json").write_text(json.dumps(payload))

    import wiki_local  # type: ignore  # noqa: PLC0415
    wiki_local.reset_for_tests()
    with patch.object(wiki_local, "_ollama_embed_query", return_value=[1.0, 0.0, 0.0, 0.0]):
        results = wiki_local.search("hybrid", k=5)
    assert len(results) == 1, f"expected 1 deduped row, got {len(results)}"
    assert results[0]["subject"] == "concept-hybrid-search"


# ---------------------------------------------------------------------------
# mtime-based invalidation
# ---------------------------------------------------------------------------

def test_load_store_caches_then_invalidates_on_mtime_change(fake_vault: Path) -> None:
    import wiki_local  # type: ignore  # noqa: PLC0415
    s1 = wiki_local._load_store()
    s2 = wiki_local._load_store()
    # Same mtime → same cached instance.
    assert s1 is s2

    # Bump the file mtime by writing it again.
    p = wiki_local._embeddings_path()
    new_mtime = p.stat().st_mtime + 5.0
    os.utime(p, (new_mtime, new_mtime))
    s3 = wiki_local._load_store()
    assert s3 is not s2, "expected cache invalidation when mtime advances"


def test_load_store_pickle_cache_round_trip(fake_vault: Path) -> None:
    """Cold load writes pickle; second cold-equivalent load reads from pickle."""
    import wiki_local  # type: ignore  # noqa: PLC0415
    wiki_local._load_store()  # populates pickle
    assert wiki_local.CACHE_FILE.exists()
    wiki_local.reset_for_tests()
    s2 = wiki_local._load_store()
    assert s2.dim == 4


# ---------------------------------------------------------------------------
# Integration (real vault + Ollama; gated)
# ---------------------------------------------------------------------------

def _vault_present() -> bool:
    return Path("~/ObsidianVault/.vector/embeddings.json").expanduser().exists()


def _ollama_up() -> bool:
    import http.client  # noqa: PLC0415
    try:
        conn = http.client.HTTPConnection("127.0.0.1", 11434, timeout=1.0)
        conn.request("GET", "/api/tags")
        resp = conn.getresponse()
        return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(
    not (_vault_present() and _ollama_up()),
    reason="requires real vault + running Ollama",
)
def test_real_search_under_100ms_warm_steady_state() -> None:
    """Acceptance gate (Phase I): warm wiki_local.search returns in <100ms.

    Cold first call may be slower (33MB JSON parse / pickle read). The
    second call is the steady-state measurement that's the gate.
    """
    import time  # noqa: PLC0415
    import wiki_local  # type: ignore  # noqa: PLC0415
    wiki_local.reset_for_tests()
    # Warm: cold load.
    wiki_local.search("hybrid search BM25 vector RRF", k=3)
    # Measured: steady-state.
    t0 = time.monotonic()
    results = wiki_local.search("hybrid search BM25 vector RRF", k=5)
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert results, "expected at least one wiki result for the canary query"
    assert elapsed_ms < 100.0, f"steady-state wiki_local.search took {elapsed_ms:.0f}ms (target <100ms)"
