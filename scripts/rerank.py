#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Cross-encoder rerank for hybrid retrieval (Phase A chunk 4).

Reranks RRF-fused candidates using `BAAI/bge-reranker-v2-m3` via the
`sentence-transformers` CrossEncoder API. Targets Apple Silicon MPS
(Metal Performance Shaders) for ~80-150ms warm latency on a top-50
pool; falls back to CPU when MPS isn't available.

Why bge-reranker-v2-m3:
  - Matched provenance with the BGE-M3 embedder (chunk 1) — same
    training pedigree, same multilingual character coverage.
  - 568M params — the right size for a 50-doc rerank pool on M-series.
  - The practical baseline cited in the research entry.

Why optional dep:
  - sentence-transformers + torch is ~2GB installed. Forcing it on
    callers that only ever use --mode vector_only or sparse_only is
    rude. The retrieval extra (`uv pip install -e .[retrieval]`) opts
    in.
  - When the extra isn't installed, callers get a clean fallback
    (`rerank()` returns the input list unchanged with a `[rerank]
    sentence-transformers not installed; skipping` note logged once
    per process).

Lazy-load contract:
  - The model is loaded on the FIRST `rerank()` call, not at import.
  - First call cost: ~3-5s (model load + warmup forward pass).
  - Subsequent calls: ~80-150ms warm for top-50 pairs on M-series MPS.
  - Loaded model is cached for the process lifetime.
  - Tests that don't want to pay the load cost should call with
    `model=DummyEncoder()` (see DummyEncoder below).

Public API:
  rerank(query, candidates, top_k=10, id_key='id', text_fn=None) -> list[dict]
  is_available() -> bool        # True iff sentence-transformers importable
  warm()         -> bool        # Force the lazy load; returns True on success
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Callable, Sequence

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_POOL_SIZE = 50
"""Example App uses 100 in production; the research entry notes that quality
plateaus past ~100 (arXiv 2411.11767 "Drowning in Documents"). On a local
M-series box, 50 is the speed/quality knee — saves ~30ms per query vs 100
with negligible quality cost on build-loop's identifier-dense queries."""

# Daemon (Phase G) — long-running process that holds the model in memory
# across recall.py invocations. Probed lazily once per process; cached.
DAEMON_HOST = os.environ.get("RERANK_DAEMON_HOST", "127.0.0.1")
try:
    DAEMON_PORT = int(os.environ.get("RERANK_DAEMON_PORT", "8765"))
except ValueError:
    DAEMON_PORT = 8765
DAEMON_PROBE_TIMEOUT_S = 0.1  # 100ms — fast fail when daemon is down.
DAEMON_CALL_TIMEOUT_S = 30.0  # cross-encoder forward pass + queue wait.

# Module-level singleton state.
_MODEL: Any | None = None
_MODEL_DEVICE: str | None = None
_FALLBACK_LOGGED: bool = False
# Daemon probe cache: None = not yet probed, True/False = probe result.
# Reset between tests via the same fixture that resets _MODEL.
_DAEMON_AVAILABLE: bool | None = None

_log = logging.getLogger("build_loop.rerank")
if not _log.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[rerank] %(message)s"))
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)


def _try_import_cross_encoder():
    """Local import to keep this module loadable on minimal installs."""
    try:
        from sentence_transformers import CrossEncoder  # type: ignore
        return CrossEncoder
    except Exception:  # noqa: BLE001
        return None


def _select_device() -> str:
    """Pick best torch device. MPS on Apple Silicon, else CPU.

    CUDA isn't tested here because the Phase A target is local Apple
    Silicon. Add a CUDA branch if/when the deploy target shifts.
    """
    try:
        import torch  # type: ignore
    except Exception:  # noqa: BLE001
        return "cpu"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def is_available() -> bool:
    """True iff sentence-transformers is importable in the current env."""
    return _try_import_cross_encoder() is not None


def warm(model_id: str | None = None) -> bool:
    """Force the lazy load. Returns True on success, False on any failure.

    Useful for SessionStart hooks that want to amortize the ~3-5s first
    call cost outside the user's interactive path.
    """
    return _ensure_loaded(model_id) is not None


def _ensure_loaded(model_id: str | None = None) -> Any | None:
    """Lazy-load the cross-encoder. Returns the model, or None on failure."""
    global _MODEL, _MODEL_DEVICE, _FALLBACK_LOGGED
    if _MODEL is not None:
        return _MODEL

    CrossEncoder = _try_import_cross_encoder()
    if CrossEncoder is None:
        if not _FALLBACK_LOGGED:
            _log.warning(
                "sentence-transformers not installed; rerank disabled "
                "(install with `uv pip install -e .[retrieval]`)"
            )
            _FALLBACK_LOGGED = True
        return None

    target_model = model_id or os.environ.get("RERANK_MODEL", DEFAULT_MODEL)
    device = _select_device()
    try:
        # max_length=512 is the standard BGE family limit; ts_chunks in
        # build-loop's semantic_facts are well under this.
        _MODEL = CrossEncoder(target_model, max_length=512, device=device)
        _MODEL_DEVICE = device
    except Exception as e:  # noqa: BLE001
        if not _FALLBACK_LOGGED:
            _log.warning(
                "failed to load %r on device=%s (%s); rerank disabled",
                target_model, device, e,
            )
            _FALLBACK_LOGGED = True
        _MODEL = None
        return None
    _log.info("loaded %s on device=%s", target_model, device)
    return _MODEL


# ---------------------------------------------------------------------------
# Daemon client (Phase G)
# ---------------------------------------------------------------------------


def _daemon_url(path: str) -> str:
    return f"http://{DAEMON_HOST}:{DAEMON_PORT}{path}"


def _probe_daemon() -> bool:
    """One-shot probe of the rerank daemon's /health endpoint.

    Cached for the process lifetime via `_DAEMON_AVAILABLE` so we don't
    pay a probe per rerank() call. The cache is invalidated implicitly
    if the daemon dies mid-process — in which case the next POST will
    raise and `_call_daemon` returns None, forcing a fall-through to the
    in-process path.
    """
    global _DAEMON_AVAILABLE
    if _DAEMON_AVAILABLE is not None:
        return _DAEMON_AVAILABLE
    if os.environ.get("RERANK_FORCE_INPROCESS"):
        _DAEMON_AVAILABLE = False
        return False
    try:
        import urllib.request  # noqa: PLC0415

        req = urllib.request.Request(_daemon_url("/health"), method="GET")
        with urllib.request.urlopen(req, timeout=DAEMON_PROBE_TIMEOUT_S) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8"))
        # Daemon is "available" iff it claims the model is warm. A daemon
        # serving in degraded mode (model not loaded) returns 503 on POST,
        # so treating warm=False as unavailable lets us fall back to the
        # in-process path which may succeed via a different model load
        # path (e.g. retry after a transient torch/sentence-transformers
        # import error).
        if body.get("ok") and body.get("warm"):
            _DAEMON_AVAILABLE = True
            _log.info(
                "rerank daemon up at %s (model=%s device=%s)",
                _daemon_url(""), body.get("model"), body.get("device"),
            )
            return True
    except Exception as e:  # noqa: BLE001
        _log.debug("rerank daemon probe failed: %s", e)
    _DAEMON_AVAILABLE = False
    return False


def _json_default(obj: Any) -> Any:
    """JSON-serialize values that semantic_facts rows carry but stdlib
    json doesn't natively handle.

    Postgres returns datetime for `valid_from`, UUID for `id`, etc. The
    cross-encoder only scores subject/predicate/object so we coerce the
    rest to strings for transport. Round-trip integrity isn't required
    — the daemon returns its own results dict and recall.py uses those
    fields directly.
    """
    import datetime as _dt  # noqa: PLC0415
    import uuid as _uuid  # noqa: PLC0415

    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, _uuid.UUID):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    # Last resort: stringify. Better than failing the whole rerank.
    return str(obj)


def _call_daemon(
    query: str,
    candidates: Sequence[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]] | None:
    """POST /rerank to the daemon. Returns None on any failure so the
    caller can fall back to in-process scoring.
    """
    global _DAEMON_AVAILABLE
    try:
        import urllib.error  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        body = json.dumps(
            {
                "query": query,
                "candidates": list(candidates),
                "top_k": int(top_k),
            },
            default=_json_default,
        ).encode("utf-8")
        req = urllib.request.Request(
            _daemon_url("/rerank"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=DAEMON_CALL_TIMEOUT_S) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
        if not payload.get("ok"):
            _log.warning("daemon rerank returned not-ok: %s", payload.get("error"))
            return None
        results = payload.get("results")
        if not isinstance(results, list):
            _log.warning("daemon rerank returned non-list results")
            return None
        return results
    except urllib.error.HTTPError as e:
        # 503 (model not warm) or 5xx — invalidate the cache so we don't
        # keep hitting a degraded daemon for the rest of the process.
        _log.warning("daemon HTTPError %s; falling back to in-process", e.code)
        _DAEMON_AVAILABLE = False
        return None
    except Exception as e:  # noqa: BLE001
        _log.warning("daemon call failed (%s); falling back to in-process", e)
        _DAEMON_AVAILABLE = False
        return None


def _default_text_fn(row: dict[str, Any]) -> str:
    """Build the document text the cross-encoder scores against the query.

    Mirrors the embed-time construction (subject + predicate + object)
    so the cross-encoder sees the same surface the embedder did.
    """
    s = (row.get("subject") or "").strip()
    p = (row.get("predicate") or "").strip()
    o = (row.get("object") or "").strip()
    text = f"{s} {p} {o}".strip()
    return text or "(empty)"


def rerank(
    query: str,
    candidates: Sequence[dict[str, Any]],
    top_k: int = 10,
    *,
    id_key: str = "id",  # noqa: ARG001 — kept for API symmetry
    text_fn: Callable[[dict[str, Any]], str] | None = None,
    pool_size: int = DEFAULT_POOL_SIZE,
    model: Any | None = None,
) -> list[dict[str, Any]]:
    """Cross-encoder rerank `candidates` against `query`, return top-k.

    Args:
      query:       The user query string.
      candidates:  RRF-fused (or any) list of result dicts. The first
                   `pool_size` are scored; the remainder are appended
                   after the reranked head in their original order
                   (preserves recall — useful for --stats).
      top_k:       Cap on returned rows. <=0 returns the full reranked
                   pool + tail.
      id_key:      Reserved for future use; rerank itself doesn't dedupe.
      text_fn:     Function (row -> text) used to build the document
                   side of each query/doc pair. Defaults to
                   subject||predicate||object.
      pool_size:   How many top candidates the cross-encoder scores.
                   Past this, items keep their RRF order.
      model:       Optional injected cross-encoder (e.g. DummyEncoder
                   in tests). When provided, skips the lazy load.

    Returns:
      A new list. Each scored row gets `_rerank_score` set and `score`
      overwritten. Tail rows (beyond pool_size) keep their incoming
      `score` and have no `_rerank_score` field.

    Graceful fallback contract:
      - Empty candidates → returns [].
      - sentence-transformers not installed OR model load fails →
        returns `candidates[:top_k]` unchanged (RRF order preserved).
      - Forward-pass exception → same fallback as above.
    """
    if not candidates:
        return []
    if not query or not query.strip():
        # No query to rerank against; preserve input order.
        return list(candidates)[: top_k if top_k and top_k > 0 else None]

    # Daemon route (Phase G): only when caller didn't inject a test model
    # (DummyEncoder & friends MUST exercise the in-process glue).
    if model is None and _probe_daemon():
        # Effective top-k for the daemon: -1 / 0 / None means "return all".
        effective_top_k = top_k if (top_k and top_k > 0) else len(candidates)
        daemon_results = _call_daemon(query, candidates, effective_top_k)
        if daemon_results is not None:
            return daemon_results

    encoder = model if model is not None else _ensure_loaded()
    if encoder is None:
        # Graceful fallback: return RRF order unchanged.
        return list(candidates)[: top_k if top_k and top_k > 0 else None]

    text_fn = text_fn or _default_text_fn
    head = list(candidates[:pool_size])
    tail = list(candidates[pool_size:])

    pairs = [(query, text_fn(row)) for row in head]
    try:
        # CrossEncoder.predict returns either a numpy array or a python
        # list of floats depending on version; coerce to plain floats.
        raw_scores = encoder.predict(pairs)
    except Exception as e:  # noqa: BLE001
        _log.warning("rerank predict failed (%s); returning RRF order", e)
        return list(candidates)[: top_k if top_k and top_k > 0 else None]

    scores = [float(s) for s in raw_scores]
    if len(scores) != len(head):
        _log.warning(
            "encoder returned %d scores for %d candidates; falling back",
            len(scores), len(head),
        )
        return list(candidates)[: top_k if top_k and top_k > 0 else None]

    scored: list[dict[str, Any]] = []
    for row, score in zip(head, scores):
        merged = dict(row)
        # Preserve the prior score (RRF) under `_rrf_score` if it had
        # been set by the upstream fuser; this aids --stats triage.
        if "score" in merged and "_rrf_score" not in merged:
            merged["_rrf_score"] = merged["score"]
        merged["_rerank_score"] = score
        merged["score"] = score
        scored.append(merged)

    scored.sort(key=lambda r: r["score"], reverse=True)
    fused = scored + tail
    if top_k and top_k > 0:
        return fused[:top_k]
    return fused


# ---------- test helper ----------


class DummyEncoder:
    """Test double: assigns scores by string match cardinality.

    Scores higher for documents that share more lowercase words with
    the query. Deterministic, zero-dep, ~microsecond per pair. Use in
    `rerank(..., model=DummyEncoder())` for unit tests that want to
    exercise the rerank glue without paying the model-load cost or
    requiring sentence-transformers.
    """

    def predict(self, pairs):  # noqa: D401
        out: list[float] = []
        for q, d in pairs:
            qs = set((q or "").lower().split())
            ds = set((d or "").lower().split())
            shared = qs & ds
            out.append(float(len(shared)))
        return out
