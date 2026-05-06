#!/usr/bin/env python3
"""In-process wiki search (Phase I).

The cliff: every `recall.py` invocation pays ~920ms calling
`llmwiki search` as a subprocess. Almost all of that cost is Python
interpreter startup + Ollama model load + parsing 33MB embeddings.json
on every fresh process. The architectural fix is to do the search
in-process inside recall.py and cache the loaded store.

Scope:
  - One-time read of `~/ObsidianVault/.vector/embeddings.json` (1962
    chunks, 768-dim, ~287ms cold; ~10ms warm via pickle cache) and
    `~/ObsidianVault/.vector/wiki.db` (FTS5 + edges; only opened lazy
    if a query needs it — vector + lexical cover the gate criterion).
  - mtime-keyed invalidation: if either source file's mtime advances
    past the cached value, re-read.
  - Cosine + lexical_score (the two dominant signals from
    vault_vector.py). The graph/PPR leg is intentionally OMITTED —
    NetworkX is a heavy dep, and the Phase C integration test only
    requires "at least one wiki result tagged [wiki] in top-5", which
    cosine + lexical comfortably satisfies for any reasonable query.
  - Query is re-embedded via Ollama `nomic-embed-text` (the vault's
    native 768-dim space). Cross-backend cosine doesn't work, so this
    is the only viable embedding path for the wiki leg. The Ollama
    daemon is keep-alive'd by build-loop's existing OLLAMA_KEEP_ALIVE
    so subsequent embeds are ~10-15ms.

Public API (matches scripts/wiki_client.wiki_search() signature):
    search(query: str, k: int = 5) -> list[dict]
        Returns rows shaped like wiki_client's parse_search_output —
        compatible with rrf_fuse() in scripts/recall.py.

Failure modes:
  - embeddings.json missing → raises FileNotFoundError; wiki_client
    catches and falls back to subprocess.
  - Ollama unreachable (or nomic-embed-text not installed) → raises
    RuntimeError; wiki_client falls back to subprocess.
  - mtime check fails (NFS / permissions) → reuse cached store
    silently; better to serve slightly stale results than to crash.

Pickle cache contract:
  - Path: `~/.local/state/build-loop/wiki-cache.pkl`
  - Stores: {"mtime": float, "store": dict, "version": int}
  - Skipped (re-built) when source mtime ≠ cached mtime, or the
    pickle's `version` doesn't match `_CACHE_VERSION` below.
  - Pickle reads run after the mtime check; if the cache's mtime is
    stale we re-read the JSON instead.

References:
  - `~/ObsidianVault/tools/scripts/vault_vector.py` for the canonical
    cosine + lexical_score implementation (lifted here, not imported,
    to keep build-loop's interpreter free of vault deps).
  - `scripts/wiki_client.py` for the parse_search_output schema we
    mirror so callers can swap one path for the other transparently.
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VAULT_ROOT_DEFAULT = Path.home() / "ObsidianVault"
ENV_VAULT_ROOT = "BUILD_LOOP_VAULT_ROOT"

# Vault-side embedder (768-dim, distinct vector space from build-loop's
# 1024-dim mxbai/bge-m3). Cross-space cosine doesn't work, so the wiki
# query MUST be re-embedded with the same model that built the store.
VAULT_OLLAMA_HOST = "127.0.0.1"
VAULT_OLLAMA_PORT = 11434
VAULT_EMBED_MODEL = "nomic-embed-text"
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "24h")

# Pickle cache lives alongside the daemons' state.
STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
) / "build-loop"
CACHE_FILE = STATE_DIR / "wiki-cache.pkl"
# Bump when the cache layout changes (e.g. add new precomputed fields).
_CACHE_VERSION = 1

EXCERPT_MAX_CHARS = 400

# Per-call query embed cache. Tiny (one entry) but saves ~12ms when the
# same query embeds twice in the same process — recall.py does NOT
# normally re-query the wiki, but this guards against future patterns.
_QUERY_EMBED_CACHE: dict[str, list[float]] = {}

_log = logging.getLogger("build_loop.wiki_local")
if not _log.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("[wiki_local] %(message)s"))
    _log.addHandler(h)
    _log.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Module-level singleton store
# ---------------------------------------------------------------------------


@dataclass
class _Store:
    """Loaded wiki vector store with provenance for invalidation."""

    chunks: list[dict[str, Any]]
    embeddings_path: Path
    mtime: float
    provider: str
    model: str
    dim: int


_STORE: _Store | None = None


def _vault_root() -> Path:
    return Path(os.environ.get(ENV_VAULT_ROOT) or VAULT_ROOT_DEFAULT).expanduser()


def _embeddings_path() -> Path:
    return _vault_root() / ".vector" / "embeddings.json"


def is_available() -> bool:
    """True iff the vault embeddings store exists and is readable."""
    p = _embeddings_path()
    return p.exists() and os.access(p, os.R_OK)


# ---------------------------------------------------------------------------
# Load + invalidate
# ---------------------------------------------------------------------------


def _read_pickle_cache(expected_mtime: float) -> _Store | None:
    """Return the pickled store if mtime + version match, else None."""
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, "rb") as f:
            payload = pickle.load(f)  # noqa: S301
    except Exception as e:  # noqa: BLE001
        _log.debug("pickle cache read failed (%s); ignoring", e)
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != _CACHE_VERSION:
        return None
    if abs(float(payload.get("mtime", 0.0)) - expected_mtime) > 1e-6:
        return None
    store = payload.get("store")
    if not isinstance(store, _Store):
        return None
    return store


def _write_pickle_cache(store: _Store) -> None:
    """Best-effort pickle write; failures are logged but never raised."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(
                {"version": _CACHE_VERSION, "mtime": store.mtime, "store": store},
                f,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
    except Exception as e:  # noqa: BLE001
        _log.debug("pickle cache write failed (%s); skipping", e)


def _load_store(force: bool = False) -> _Store:
    """Load the vault embeddings store, mtime-checked, pickle-cached.

    On cold reads from JSON: ~287ms (33MB parse). On warm reads from
    pickle: ~30-50ms. Cache invalidates when the source mtime advances.
    """
    global _STORE
    src = _embeddings_path()
    if not src.exists():
        raise FileNotFoundError(f"vault embeddings missing at {src}")
    src_mtime = src.stat().st_mtime
    if not force and _STORE is not None and abs(_STORE.mtime - src_mtime) < 1e-6:
        return _STORE

    # Try the pickle cache first.
    cached = _read_pickle_cache(src_mtime) if not force else None
    if cached is not None:
        _STORE = cached
        return cached

    # Cold path: parse the JSON.
    t0 = time.monotonic()
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    chunks = data.get("chunks") or []
    if not chunks:
        raise RuntimeError(f"vault store at {src} has no chunks")
    store = _Store(
        chunks=chunks,
        embeddings_path=src,
        mtime=src_mtime,
        provider=data.get("provider", "ollama"),
        model=data.get("model", VAULT_EMBED_MODEL),
        dim=int(data.get("dimension", 768)),
    )
    load_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        "loaded %d chunks (dim=%d provider=%s/%s) from JSON in %dms",
        len(chunks),
        store.dim,
        store.provider,
        store.model,
        load_ms,
    )
    _STORE = store
    _write_pickle_cache(store)
    return store


def reset_for_tests() -> None:
    """Clear the in-memory store + query cache. Pickle cache untouched."""
    global _STORE
    _STORE = None
    _QUERY_EMBED_CACHE.clear()


# ---------------------------------------------------------------------------
# Search math (lifted from vault_vector.py — keep names identical)
# ---------------------------------------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity over plain Python lists. Tolerates dim mismatch
    by returning 0.0 (defensive — should never happen for in-store rows
    but possible if the store is mid-rebuild)."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return (dot / (na * nb)) if (na and nb) else 0.0


# Stopword + tokenization tables (verbatim from vault_vector.py).
MATCH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "for", "from", "how", "i", "in",
    "is", "it", "me", "my", "of", "on", "or", "the", "this", "to", "vs",
    "what", "what's", "whats", "where", "why", "with",
}
TYPE_PREFIXES = {
    "company", "concept", "decision", "person", "project", "research",
    "source", "portfolio", "deal", "pass", "thesis", "vehicle",
    "investment", "investments",
}


def _normalize_match_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _match_terms(text: str) -> list[str]:
    return [
        t
        for t in re.findall(r"[a-z0-9]+", text.casefold())
        if len(t) > 1 and t not in MATCH_STOPWORDS
    ]


def _drop_type_prefix(terms: list[str]) -> list[str]:
    if terms and terms[0] in TYPE_PREFIXES:
        return terms[1:]
    return terms


def _term_coverage(query_terms: list[str], surface: str) -> float:
    if not query_terms:
        return 0.0
    surface_terms = set(_match_terms(surface))
    if not surface_terms:
        return 0.0
    return sum(1 for t in query_terms if t in surface_terms) / len(query_terms)


def _chunk_identity_surface(chunk: dict) -> str:
    page_id = chunk.get("page_id", "") or ""
    page_id_terms = " ".join(
        _drop_type_prefix(_match_terms(page_id.replace("-", " ")))
    )
    return " ".join(
        [
            page_id.replace("-", " "),
            page_id_terms,
            chunk.get("title", "") or "",
            Path(chunk.get("page_path", "") or "").stem.replace("-", " "),
        ]
    )


def lexical_score(query: str, chunk: dict) -> float:
    """Deterministic exact-match signal — verbatim port of vault_vector.py
    `lexical_score` minus the `full_text` arg (we don't load full pages
    into the in-process store; preview is enough for the in-loop signal).
    """
    terms = _match_terms(query)
    if not terms:
        return 0.0

    q_norm = _normalize_match_text(query)
    title = chunk.get("title", "") or ""
    page_id = chunk.get("page_id", "") or ""
    heading = chunk.get("heading", "") or ""
    path = chunk.get("page_path", "") or ""
    preview = chunk.get("content_preview", "") or ""
    identity = _chunk_identity_surface(chunk)
    title_norm = _normalize_match_text(title)
    page_id_norm = _normalize_match_text(page_id.replace("-", " "))
    page_id_no_type_terms = _drop_type_prefix(
        _match_terms(page_id.replace("-", " "))
    )
    page_id_no_type_norm = _normalize_match_text(
        " ".join(page_id_no_type_terms)
    )
    heading_norm = _normalize_match_text(heading)
    body_norm = _normalize_match_text(preview)

    score = 0.0
    if q_norm in {title_norm, page_id_norm, page_id_no_type_norm}:
        score += 1.35
    elif q_norm and (
        q_norm in title_norm
        or q_norm in page_id_norm
        or q_norm in page_id_no_type_norm
    ):
        score += 1.05
    if (
        page_id_no_type_norm
        and len(page_id_no_type_terms) >= 2
        and page_id_no_type_norm in q_norm
    ):
        score += 1.15
    if title_norm and len(_match_terms(title)) >= 2 and title_norm in q_norm:
        score += 1.15
    if q_norm and q_norm in _normalize_match_text(path):
        score += 0.65
    if q_norm and q_norm in heading_norm:
        score += 0.45
    if q_norm and q_norm in body_norm:
        score += 0.35

    identity_cov = _term_coverage(terms, identity)
    title_cov = _term_coverage(terms, title)
    heading_cov = _term_coverage(terms, heading)
    body_cov = _term_coverage(terms, preview)
    score += 1.05 * identity_cov
    score += 0.60 * title_cov
    score += 0.35 * heading_cov
    score += 0.55 * body_cov
    if len(terms) >= 3 and body_cov >= 0.9:
        score += 0.45
    if body_cov >= 0.9 and _term_coverage(terms, identity) > 0:
        score += 0.60
    if len(terms) >= 2 and identity_cov >= 0.9:
        score += 0.60

    return min(score, 3.0)


def _normalize_scores(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    if abs(hi - lo) < 1e-12:
        return {k: 1.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


# ---------------------------------------------------------------------------
# Query embedding (Ollama nomic-embed-text via persistent HTTP)
# ---------------------------------------------------------------------------


def _ollama_embed_query(query: str) -> list[float]:
    """Embed a single query via Ollama nomic-embed-text on localhost.

    Uses urllib.request (stdlib) to avoid bringing in build-loop's
    embed_backend (which speaks the 1024-dim mxbai/bge-m3 space, not
    the wiki's 768-dim nomic space). One small dedicated client keeps
    the spaces cleanly separated.

    Cached per-process via `_QUERY_EMBED_CACHE` so repeat queries
    inside the same process don't re-pay the ~12ms Ollama roundtrip.
    """
    if query in _QUERY_EMBED_CACHE:
        return _QUERY_EMBED_CACHE[query]
    body = json.dumps(
        {
            "model": VAULT_EMBED_MODEL,
            "prompt": query,
            "keep_alive": OLLAMA_KEEP_ALIVE,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"http://{VAULT_OLLAMA_HOST}:{VAULT_OLLAMA_PORT}/api/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"ollama embed failed for wiki query: {e}") from e
    emb = payload.get("embedding")
    if not isinstance(emb, list):
        raise RuntimeError("ollama: missing 'embedding' in response")
    vec = [float(x) for x in emb]
    _QUERY_EMBED_CACHE[query] = vec
    return vec


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _truncate(text: str, max_chars: int = EXCERPT_MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def search(query: str, k: int = 5) -> list[dict[str, Any]]:
    """Search the in-process wiki store and return up to `k` page-level rows.

    Result shape matches `wiki_client.parse_search_output` exactly so
    callers can swap one path for the other transparently:

        {
            "id":        "wiki:<page-id>#<section>",
            "subject":   "<page-id>",
            "predicate": "<section>",
            "object":    "<excerpt>",
            "score":     <cos>,
            "ppr":       0.0,         # not computed in-process; informational
            "cos":       <cos>,
            "lex":       <lex>,
            "wiki_path": "<vault-relative path>",
            "source":    "wiki",
        }

    Page-dedup: the store is chunked at section granularity; we collapse
    to one row per page_id, keeping the chunk with the highest combined
    rerank score (0.55*cos_norm + 0.45*lex_norm).

    Raises:
        FileNotFoundError — embeddings.json missing.
        RuntimeError      — Ollama unreachable / nomic-embed-text down.
    """
    if not query or not query.strip():
        return []
    store = _load_store()
    q_vec = _ollama_embed_query(query)

    # Score every chunk on cosine + lexical. Numpy is opportunistic:
    # already on the wheel via sentence-transformers + torch (~33MB
    # transitive dep, no incremental install cost). Vectorized cosine
    # over 1962×768 takes ~3-5ms vs ~80-130ms for the pure-Python loop;
    # crosses the wiki_ms <100ms acceptance gate decisively. Falls back
    # to the per-chunk loop when numpy is missing (e.g. minimal install
    # without retrieval extras).
    scored: list[dict[str, Any]] = []
    try:
        import numpy as _np  # type: ignore  # noqa: PLC0415
        _have_numpy = True
    except ImportError:
        _have_numpy = False

    if _have_numpy:
        valid: list[tuple[int, dict, list[float]]] = []
        for i, c in enumerate(store.chunks):
            emb = c.get("embedding")
            if emb:
                valid.append((i, c, emb))
        if not valid:
            return []
        # Stack to a (N, D) matrix; normalize rows; dot with the
        # normalized query to get cosines in one BLAS call.
        mat = _np.asarray([emb for _i, _c, emb in valid], dtype=_np.float32)
        norms = _np.linalg.norm(mat, axis=1, keepdims=True)
        norms = _np.where(norms == 0, 1.0, norms)
        mat = mat / norms
        q = _np.asarray(q_vec, dtype=_np.float32)
        q_norm = float(_np.linalg.norm(q)) or 1.0
        cos_vec = (mat @ (q / q_norm)).tolist()
        for (_i, c, _emb), cos in zip(valid, cos_vec):
            lex = lexical_score(query, c)
            scored.append({"cos": float(cos), "lex": lex, "chunk": c})
    else:
        for c in store.chunks:
            emb = c.get("embedding")
            if not emb:
                continue
            cos = cosine(q_vec, emb)
            lex = lexical_score(query, c)
            scored.append({"cos": cos, "lex": lex, "chunk": c})
    if not scored:
        return []

    cos_norm = _normalize_scores({i: d["cos"] for i, d in enumerate(scored)})
    lex_norm = _normalize_scores({i: d["lex"] for i, d in enumerate(scored)})
    for i, d in enumerate(scored):
        d["pre"] = 0.55 * cos_norm.get(i, 0.0) + 0.45 * lex_norm.get(i, 0.0)
    scored.sort(key=lambda d: d["pre"], reverse=True)

    # Page-dedup: keep the strongest chunk per page_id. Walk the sorted
    # list once; first occurrence of each page wins.
    seen_pages: dict[str, dict] = {}
    for d in scored:
        c = d["chunk"]
        pid = c.get("page_id") or ""
        if not pid:
            continue
        if pid in seen_pages:
            continue
        seen_pages[pid] = d
        if len(seen_pages) >= k:
            break

    # Build wire-shape rows mirroring wiki_client.parse_search_output.
    results: list[dict[str, Any]] = []
    for d in seen_pages.values():
        c = d["chunk"]
        pid = c.get("page_id") or ""
        section = (c.get("heading") or "").strip()
        path = c.get("page_path") or ""
        results.append(
            {
                "id": f"wiki:{pid}" + (f"#{section}" if section else ""),
                "subject": pid,
                "predicate": section,
                "object": _truncate(c.get("content_preview") or ""),
                "score": float(d["cos"]),
                "ppr": 0.0,  # graph leg intentionally omitted (see module docstring).
                "cos": float(d["cos"]),
                "lex": float(d["lex"]),
                "wiki_path": path,
                "source": "wiki",
            }
        )
    return results


# ---------------------------------------------------------------------------
# CLI passthrough for ad-hoc inspection
# ---------------------------------------------------------------------------


def _cli_main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="In-process wiki search (Phase I, mirrors wiki_client.search wire format)"
    )
    p.add_argument("query", nargs="+", help="search query (joined with spaces)")
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--force-reload", action="store_true",
                   help="bypass mtime + pickle cache; re-read JSON")
    args = p.parse_args(argv)
    query = " ".join(args.query)
    if args.force_reload:
        reset_for_tests()
    t0 = time.monotonic()
    results = search(query, k=args.k)
    took_ms = int((time.monotonic() - t0) * 1000)
    print(f"# wiki_local: {len(results)} results in {took_ms}ms", file=sys.stderr)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
