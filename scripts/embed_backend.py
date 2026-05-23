#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Embedding backend abstraction for repo-local episodic memory.

Default: MLX (`mlx-community/mxbai-embed-large-v1`, 1024-dim). Faster
per-call once warm (~10ms) and dramatically faster on batches (~2ms
amortized at batch=10) compared to Ollama HTTP (~15ms warm).

Fallback: Ollama (`bge-m3`, 1024-dim). Hybrid recall Phase A migrated
the Ollama default from `mxbai-embed-large` to BGE-M3 — same dimension,
better hybrid retrieval performance, ColBERT-mode-ready for Phase E
late-interaction. The MLX default stays at mxbai-embed-large-v1 because
no `mlx-community/bge-m3` weights are cached locally; cross-backend
vectors are NOT comparable, so callers writing rows must record
`embedding_model_version` and recall must re-embed when querying rows
written by a different model. See research entry
`build-loop-search-architecture` for rationale.

The active backend is chosen on first call from $EMBED_BACKEND
({"mlx","ollama"}, default "mlx"). If MLX init fails (import error,
model load error, first-call failure), the module logs a warning to
stderr and falls through to Ollama for the rest of the process. Once
fallen through, MLX is not retried — keeps stop-hook latency
predictable.

Public API:
  embed(text)                -> list[float]              (single text)
  embed([t1, t2, ...])       -> list[list[float]]        (batched)
  dimension()                -> int                       (always 1024)
  active_backend()           -> "mlx" | "ollama"          (after first call)
  active_model()             -> model id string           (after first call)

Env vars:
  EMBED_BACKEND   "mlx" (default) or "ollama"
  EMBED_MODEL     model id (defaults: mxbai-embed-large-v1 for MLX,
                  bge-m3 for Ollama)
  MLX_FORCE_FAIL  any truthy value forces fallback (used by tests)

Output format: Python lists of floats. Callers don't need numpy / mlx.

Exit semantics: this module never calls sys.exit. On total backend
failure (MLX broken AND Ollama unreachable), embed() raises
RuntimeError. Callers decide policy (write_decision.py logs and
swallows; recall.py exits 2).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Sequence

EMBED_DIM = 1024
MLX_DEFAULT_MODEL = "mlx-community/mxbai-embed-large-v1"
# Phase A hybrid-recall migration: Ollama default switched from
# `mxbai-embed-large` to `bge-m3`. Both are 1024-dim, so the pgvector
# column dimension is unchanged, but the vector spaces are NOT
# comparable. Rows written by mxbai must be re-embedded with bge-m3
# before recall can fuse them with bge-m3-embedded queries — see
# scripts/migrate_reembed_to_bgem3.py.
OLLAMA_DEFAULT_MODEL = "bge-m3"
OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434
OLLAMA_TIMEOUT_S = 60

# Daemon-side keep-alive override. Default Ollama evicts an idle model
# after 5 minutes — re-paying ~250-500ms model-load on the next call.
# 24h matches the cadence of a typical work session and amortizes warmup
# across every Stop hook firing in that window. Override per-process via
# OLLAMA_KEEP_ALIVE env (matches Ollama's own env var name).
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "24h")

# ---- Daemon (Phase H) ---------------------------------------------------
# Long-running stdlib HTTP server that holds the embedder backend in
# memory across recall.py invocations. Probed once per process; cached.
# When the daemon is up, every embed() call routes through it (~10-20ms
# loopback overhead vs ~3000ms cold MLX load per fresh process).
EMBED_DAEMON_HOST = os.environ.get("EMBED_DAEMON_HOST", "127.0.0.1")
try:
    EMBED_DAEMON_PORT = int(os.environ.get("EMBED_DAEMON_PORT", "8766"))
except ValueError:
    EMBED_DAEMON_PORT = 8766
EMBED_DAEMON_PROBE_TIMEOUT_S = 0.1  # 100ms — fast fail when daemon is down.
EMBED_DAEMON_CALL_TIMEOUT_S = 30.0  # generous; covers MLX cold-loaded large batches.


def _log(msg: str) -> None:
    print(f"[embed_backend] {msg}", file=sys.stderr, flush=True)


# ---------- Ollama backend ----------


class OllamaBackend:
    """Persistent-HTTP Ollama backend. Reuses one HTTPConnection across calls."""

    def __init__(self, model: str = OLLAMA_DEFAULT_MODEL) -> None:
        self.model = model
        self._conn = None  # lazy

    def _ensure_conn(self):
        # Stdlib http.client; recreate on broken-pipe-style errors.
        import http.client

        if self._conn is None:
            self._conn = http.client.HTTPConnection(OLLAMA_HOST, OLLAMA_PORT, timeout=OLLAMA_TIMEOUT_S)
        return self._conn

    def _post_one(self, text: str) -> list[float]:
        body = json.dumps({
            "model": self.model,
            "prompt": text,
            "keep_alive": OLLAMA_KEEP_ALIVE,
        }).encode("utf-8")
        # Try persistent conn; on failure recreate once.
        for attempt in (1, 2):
            try:
                conn = self._ensure_conn()
                conn.request(
                    "POST",
                    "/api/embeddings",
                    body=body,
                    headers={"Content-Type": "application/json", "Connection": "keep-alive"},
                )
                resp = conn.getresponse()
                payload = resp.read()
                if resp.status != 200:
                    raise RuntimeError(f"ollama HTTP {resp.status}: {payload[:200]!r}")
                data = json.loads(payload)
                emb = data.get("embedding")
                if not isinstance(emb, list):
                    raise RuntimeError("ollama: missing 'embedding' in response")
                return [float(x) for x in emb]
            except (ConnectionError, OSError, RuntimeError):
                # Reset and retry once.
                try:
                    if self._conn is not None:
                        self._conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self._conn = None
                if attempt == 2:
                    raise

        raise RuntimeError("ollama: unreachable")

    def embed(self, text: str) -> list[float]:
        return self._post_one(text)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        # Ollama /api/embeddings does not natively batch; loop on persistent conn.
        return [self._post_one(t) for t in texts]

    def name(self) -> str:
        return "ollama"


# ---------- MLX backend ----------


class MLXBackend:
    """Local MLX backend using mlx-embeddings.

    Lazy-loads the model on first call (~220ms warm cache). The model
    object is cached for the process lifetime.
    """

    def __init__(self, model_id: str = MLX_DEFAULT_MODEL) -> None:
        self.model_id = model_id
        self._model = None
        self._tokenizer = None
        self._generate = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if os.environ.get("MLX_FORCE_FAIL"):
            raise RuntimeError("MLX_FORCE_FAIL set; simulated failure")
        # Local import keeps Linux installs (where mlx-embeddings is not
        # available) from blowing up at module import time.
        from mlx_embeddings import generate, load  # type: ignore

        self._model, self._tokenizer = load(self.model_id)
        self._generate = generate

    def embed(self, text: str) -> list[float]:
        self._ensure_loaded()
        if not text:
            raise ValueError("embed_backend: empty text not supported")
        out = self._generate(self._model, self._tokenizer, texts=[text])
        return [float(x) for x in out.text_embeds[0].tolist()]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        self._ensure_loaded()
        if not texts:
            return []
        if any((not t) for t in texts):
            raise ValueError("embed_backend: empty text in batch not supported")
        out = self._generate(self._model, self._tokenizer, texts=list(texts))
        return [[float(x) for x in row.tolist()] for row in out.text_embeds]

    def name(self) -> str:
        return "mlx"


# ---------- Daemon backend (Phase H) ----------


class DaemonBackend:
    """HTTP client for `scripts/embed_daemon.py`.

    Speaks the same `embed`/`embed_batch`/`name` interface as the in-process
    backends so `_select_backend()` can hand it to callers unchanged. The
    daemon holds the model in memory across recall.py invocations,
    eliminating the ~3000ms MLX cold-load cliff on every fresh process.

    Failure handling: any HTTP error is raised so `_select_backend()` can
    fall back to the in-process MLX/Ollama path. Once the in-process
    backend is selected for this process, the daemon is not retried —
    keeps latency predictable and avoids per-call probe cost.
    """

    def __init__(
        self,
        *,
        host: str = EMBED_DAEMON_HOST,
        port: int = EMBED_DAEMON_PORT,
        backend_name: str = "unknown",
        model: str = "unknown",
    ) -> None:
        self.host = host
        self.port = port
        # Carries the BACKEND the daemon advertises (mlx|ollama) plus its
        # model id, so callers asking active_backend() / active_model()
        # see the underlying truth, not the literal string "daemon".
        self._daemon_backend = backend_name
        self.model = model
        self._conn = None  # lazy http.client.HTTPConnection

    def _ensure_conn(self):
        import http.client  # noqa: PLC0415

        if self._conn is None:
            self._conn = http.client.HTTPConnection(
                self.host, self.port, timeout=EMBED_DAEMON_CALL_TIMEOUT_S
            )
        return self._conn

    def _post_embed(self, texts: list[str]) -> list[list[float]]:
        body = json.dumps({"texts": texts}).encode("utf-8")
        for attempt in (1, 2):
            try:
                conn = self._ensure_conn()
                conn.request(
                    "POST",
                    "/embed",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Connection": "keep-alive",
                    },
                )
                resp = conn.getresponse()
                payload = resp.read()
                if resp.status != 200:
                    raise RuntimeError(
                        f"embed daemon HTTP {resp.status}: {payload[:200]!r}"
                    )
                data = json.loads(payload)
                if not data.get("ok"):
                    raise RuntimeError(
                        f"embed daemon not-ok: {data.get('error', 'unknown')}"
                    )
                embs = data.get("embeddings")
                if not isinstance(embs, list):
                    raise RuntimeError(
                        "embed daemon: missing 'embeddings' in response"
                    )
                return [[float(x) for x in row] for row in embs]
            except (ConnectionError, OSError, RuntimeError):
                # Reset and retry once. If the daemon was up at probe but
                # has since died, this gives us one shot to reconnect
                # before kicking back to the caller for fallback.
                try:
                    if self._conn is not None:
                        self._conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self._conn = None
                if attempt == 2:
                    raise
        raise RuntimeError("embed daemon: unreachable")

    def embed(self, text: str) -> list[float]:
        out = self._post_embed([text])
        if not out:
            raise RuntimeError("embed daemon returned empty embeddings list")
        return out[0]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._post_embed(list(texts))

    def name(self) -> str:
        # Surface the underlying backend rather than "daemon" so callers
        # writing rows tag them with the actual model that produced them.
        return self._daemon_backend or "daemon"


# ---------- module-level singleton ----------

_BACKEND = None  # type: ignore  # OllamaBackend | MLXBackend | DaemonBackend | None
_FALLBACK_REASON: str | None = None
_DAEMON_PROBED: bool = False  # Per-process probe latch.


def _probe_daemon() -> DaemonBackend | None:
    """One-shot probe of the embed daemon's /health endpoint.

    Latched via `_DAEMON_PROBED` so we never re-probe within the same
    process. If `EMBED_FORCE_INPROCESS` is set, the probe is skipped
    entirely and returns None — used by tests and by users diagnosing
    a degraded daemon.

    Returns a configured `DaemonBackend` when the daemon is up AND
    reports `warm: true`, else None.
    """
    global _DAEMON_PROBED
    if _DAEMON_PROBED:
        # Caller checks this through the cached _BACKEND singleton.
        return None
    _DAEMON_PROBED = True
    if os.environ.get("EMBED_FORCE_INPROCESS"):
        return None
    try:
        import urllib.request  # noqa: PLC0415

        url = f"http://{EMBED_DAEMON_HOST}:{EMBED_DAEMON_PORT}/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=EMBED_DAEMON_PROBE_TIMEOUT_S) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8"))
        # Daemon is "available" iff it claims the backend is warm. A
        # daemon serving in degraded mode (backend not loaded) returns
        # 503 on POST, so treating warm=False as unavailable lets us
        # fall back to the in-process path which may succeed via a
        # different load path.
        if body.get("ok") and body.get("warm"):
            backend_name = body.get("backend", "unknown")
            model_name = body.get("model", "unknown")
            _log(
                f"embed daemon up at {EMBED_DAEMON_HOST}:{EMBED_DAEMON_PORT} "
                f"(backend={backend_name} model={model_name})"
            )
            return DaemonBackend(
                backend_name=backend_name,
                model=model_name,
            )
    except Exception as e:  # noqa: BLE001
        # Quiet by design — probe failures are routine when the daemon
        # isn't running. Logged at debug level only.
        _log_debug = os.environ.get("EMBED_BACKEND_DEBUG")
        if _log_debug:
            _log(f"embed daemon probe failed: {e!r}")
    return None


def _select_backend():
    global _BACKEND, _FALLBACK_REASON
    if _BACKEND is not None:
        return _BACKEND

    # Phase H: try the daemon FIRST. When it's up, this is the entire
    # selection — we don't load MLX/Ollama in-process at all.
    daemon = _probe_daemon()
    if daemon is not None:
        _BACKEND = daemon
        return _BACKEND

    requested = os.environ.get("EMBED_BACKEND", "mlx").lower().strip()
    custom_model = os.environ.get("EMBED_MODEL")

    if requested == "ollama":
        _BACKEND = OllamaBackend(model=custom_model or OLLAMA_DEFAULT_MODEL)
        return _BACKEND

    if requested != "mlx":
        _log(f"unknown EMBED_BACKEND={requested!r}; using mlx")

    # Try MLX; fall through to Ollama on any error.
    candidate = MLXBackend(model_id=custom_model or MLX_DEFAULT_MODEL)
    try:
        # Force the lazy load NOW so failure is detected before first
        # production call. Adds ~220ms one-time cold start; we eat it
        # here so the first user-facing call is steady-state.
        candidate._ensure_loaded()
        _BACKEND = candidate
        return _BACKEND
    except Exception as e:  # noqa: BLE001
        _FALLBACK_REASON = f"MLX init failed: {e!r}"
        _log(f"falling back to ollama ({_FALLBACK_REASON})")
        _BACKEND = OllamaBackend(model=OLLAMA_DEFAULT_MODEL)
        return _BACKEND


def embed(text):  # type: ignore[no-untyped-def]
    """Embed a single string or a list of strings.

    Single → list[float] of length 1024.
    Batch  → list[list[float]] (each length 1024).
    """
    backend = _select_backend()
    if isinstance(text, str):
        return backend.embed(text)
    if isinstance(text, (list, tuple)):
        return backend.embed_batch(list(text))
    raise TypeError(f"embed() expects str or list[str], got {type(text).__name__}")


def dimension() -> int:
    """Return the embedding dimensionality (1024)."""
    return EMBED_DIM


def active_backend() -> str:
    """Return 'mlx' or 'ollama' (after first embed call)."""
    return _select_backend().name()


def active_model() -> str:
    """Return the model identifier the active backend is configured to use.

    Useful for `embedding_model_version` provenance: every write should
    record which model produced the vector so cross-space mismatches can
    be detected at recall time.
    """
    backend = _select_backend()
    return getattr(backend, "model", None) or getattr(backend, "model_id", "unknown")


def fallback_reason() -> str | None:
    """Return why we fell through to Ollama, or None if MLX is active."""
    _select_backend()
    return _FALLBACK_REASON


def reset_for_tests() -> None:
    """Clear the singleton so tests can re-select with new env.

    Closes the Ollama / Daemon HTTP connection if one was open to avoid
    ResourceWarning on rapid backend switches. Also clears the
    per-process daemon-probe latch so `_select_backend()` will probe
    the daemon again on the next call.
    """
    global _BACKEND, _FALLBACK_REASON, _DAEMON_PROBED
    if _BACKEND is not None and isinstance(_BACKEND, (OllamaBackend, DaemonBackend)):
        try:
            if _BACKEND._conn is not None:
                _BACKEND._conn.close()
        except Exception:  # noqa: BLE001
            pass
    _BACKEND = None
    _FALLBACK_REASON = None
    _DAEMON_PROBED = False


if __name__ == "__main__":
    # CLI: `python3 embed_backend.py 'some text'` → print dim and first 5 floats
    if len(sys.argv) < 2:
        print("usage: embed_backend.py <text>", file=sys.stderr)
        sys.exit(1)
    v = embed(sys.argv[1])
    print(f"backend={active_backend()} dim={len(v)} preview={v[:5]}")
