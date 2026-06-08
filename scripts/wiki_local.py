#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
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
import math
import os
import pickle
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
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
_CACHE_VERSION = 2

EXCERPT_MAX_CHARS = 400
MAX_VECTOR_CANDIDATES = int(os.environ.get("BUILD_LOOP_WIKI_VECTOR_CANDIDATES", "800"))

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
    embedding_norms: list[float] = field(default_factory=list)
    lex_fields: list[dict[str, Any]] = field(default_factory=list)
    term_index: dict[str, list[int]] = field(default_factory=dict)


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
    embedding_norms, lex_fields, term_index = _build_store_index(chunks)
    store = _Store(
        chunks=chunks,
        embeddings_path=src,
        mtime=src_mtime,
        provider=data.get("provider", "ollama"),
        model=data.get("model", VAULT_EMBED_MODEL),
        dim=int(data.get("dimension", 768)),
        embedding_norms=embedding_norms,
        lex_fields=lex_fields,
        term_index=term_index,
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


def _term_set(text: str) -> set[str]:
    return set(_match_terms(text))


def _lexical_fields(chunk: dict) -> dict[str, Any]:
    """Precompute normalized lexical surfaces for one chunk."""
    title = chunk.get("title", "") or ""
    page_id = chunk.get("page_id", "") or ""
    heading = chunk.get("heading", "") or ""
    path = chunk.get("page_path", "") or ""
    preview = chunk.get("content_preview", "") or ""
    page_id_text = page_id.replace("-", " ")
    page_id_terms = _match_terms(page_id_text)
    page_id_no_type_terms = _drop_type_prefix(page_id_terms)
    identity = _chunk_identity_surface(chunk)
    return {
        "title_norm": _normalize_match_text(title),
        "page_id_norm": _normalize_match_text(page_id_text),
        "page_id_no_type_terms": page_id_no_type_terms,
        "page_id_no_type_norm": _normalize_match_text(" ".join(page_id_no_type_terms)),
        "path_norm": _normalize_match_text(path),
        "heading_norm": _normalize_match_text(heading),
        "body_norm": _normalize_match_text(preview),
        "identity_terms": _term_set(identity),
        "title_terms": _term_set(title),
        "heading_terms": _term_set(heading),
        "body_terms": _term_set(preview),
        "path_terms": _term_set(path),
        "title_term_count": len(_match_terms(title)),
    }


def _build_store_index(
    chunks: list[dict[str, Any]],
) -> tuple[list[float], list[dict[str, Any]], dict[str, list[int]]]:
    """Build per-store caches used by the steady-state search path."""
    embedding_norms: list[float] = []
    lex_fields: list[dict[str, Any]] = []
    term_index_sets: dict[str, set[int]] = {}
    for idx, chunk in enumerate(chunks):
        emb = chunk.get("embedding")
        if isinstance(emb, list) and emb:
            try:
                embedding_norms.append(math.sqrt(sum(float(x) * float(x) for x in emb)))
            except (TypeError, ValueError):
                embedding_norms.append(0.0)
        else:
            embedding_norms.append(0.0)

        fields = _lexical_fields(chunk)
        lex_fields.append(fields)
        terms: set[str] = set()
        for key in ("identity_terms", "title_terms", "heading_terms", "body_terms", "path_terms"):
            terms.update(fields.get(key, set()))
        for term in terms:
            term_index_sets.setdefault(term, set()).add(idx)
    term_index = {term: sorted(indices) for term, indices in term_index_sets.items()}
    return embedding_norms, lex_fields, term_index


def _term_coverage_set(query_terms: list[str], surface_terms: set[str]) -> float:
    if not query_terms:
        return 0.0
    if not surface_terms:
        return 0.0
    return sum(1 for t in query_terms if t in surface_terms) / len(query_terms)


def _lexical_score_from_fields(
    query_terms: list[str],
    q_norm: str,
    fields: dict[str, Any],
) -> float:
    if not query_terms:
        return 0.0

    title_norm = fields.get("title_norm", "")
    page_id_norm = fields.get("page_id_norm", "")
    page_id_no_type_terms = fields.get("page_id_no_type_terms", [])
    page_id_no_type_norm = fields.get("page_id_no_type_norm", "")
    path_norm = fields.get("path_norm", "")
    heading_norm = fields.get("heading_norm", "")
    body_norm = fields.get("body_norm", "")

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
    if title_norm and fields.get("title_term_count", 0) >= 2 and title_norm in q_norm:
        score += 1.15
    if q_norm and q_norm in path_norm:
        score += 0.65
    if q_norm and q_norm in heading_norm:
        score += 0.45
    if q_norm and q_norm in body_norm:
        score += 0.35

    identity_cov = _term_coverage_set(query_terms, fields.get("identity_terms", set()))
    title_cov = _term_coverage_set(query_terms, fields.get("title_terms", set()))
    heading_cov = _term_coverage_set(query_terms, fields.get("heading_terms", set()))
    body_cov = _term_coverage_set(query_terms, fields.get("body_terms", set()))
    score += 1.05 * identity_cov
    score += 0.60 * title_cov
    score += 0.35 * heading_cov
    score += 0.55 * body_cov
    if len(query_terms) >= 3 and body_cov >= 0.9:
        score += 0.45
    if body_cov >= 0.9 and identity_cov > 0:
        score += 0.60
    if len(query_terms) >= 2 and identity_cov >= 0.9:
        score += 0.60

    return min(score, 3.0)


def _normalize_vector(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(float(x) * float(x) for x in vec))
    if not norm:
        return []
    return [float(x) / norm for x in vec]


def _cosine_unit_query(q_unit: list[float], emb: list[float], emb_norm: float) -> float:
    if not q_unit or not emb or not emb_norm or len(q_unit) != len(emb):
        return 0.0
    return sum(q * float(e) for q, e in zip(q_unit, emb)) / emb_norm


def lexical_score(query: str, chunk: dict) -> float:
    """Deterministic exact-match signal — verbatim port of vault_vector.py
    `lexical_score` minus the `full_text` arg (we don't load full pages
    into the in-process store; preview is enough for the in-loop signal).
    """
    terms = _match_terms(query)
    return _lexical_score_from_fields(
        terms,
        _normalize_match_text(query),
        _lexical_fields(chunk),
    )


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
    q_unit = _normalize_vector(q_vec)
    query_terms = _match_terms(query)
    query_norm = _normalize_match_text(query)

    candidate_set: set[int] = set()
    for term in query_terms:
        candidate_set.update(store.term_index.get(term, []))
    if candidate_set:
        candidate_indices = list(candidate_set)
    else:
        candidate_indices = list(range(len(store.chunks)))

    # Score lexical surfaces first from precomputed fields. If a term query
    # fans out broadly, bound vector scoring to the strongest lexical
    # candidates; no-term semantic-only queries still fall back to full scan.
    lex_by_idx: dict[int, float] = {}
    for idx in candidate_indices:
        if idx < len(store.lex_fields):
            lex_by_idx[idx] = _lexical_score_from_fields(
                query_terms,
                query_norm,
                store.lex_fields[idx],
            )
    if candidate_set and len(candidate_indices) > MAX_VECTOR_CANDIDATES:
        limit = max(MAX_VECTOR_CANDIDATES, k * 80)
        candidate_indices.sort(key=lambda i: lex_by_idx.get(i, 0.0), reverse=True)
        candidate_indices = candidate_indices[:limit]

    scored: list[dict[str, Any]] = []
    for idx in candidate_indices:
        c = store.chunks[idx]
        emb = c.get("embedding")
        if not emb:
            continue
        emb_norm = store.embedding_norms[idx] if idx < len(store.embedding_norms) else 0.0
        cos = _cosine_unit_query(q_unit, emb, emb_norm)
        scored.append({"cos": cos, "lex": lex_by_idx.get(idx, 0.0), "chunk": c})
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
