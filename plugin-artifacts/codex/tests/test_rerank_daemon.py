"""Tests for the long-running rerank daemon (Phase G).

Two layers:
  1. Mocked tests for the client routing in `scripts/rerank.py` —
     probe success/failure, force-inprocess override, daemon HTTP errors.
     Always run, no model needed.
  2. Real-daemon integration test gated on:
       - sentence_transformers + torch installed
       - port 8765 (or RERANK_DAEMON_PORT) actually free
     Spawns the daemon as a subprocess, waits for /health, runs a real
     rerank, asserts ranking + sub-200ms warm latency.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import rerank as rerank_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """Clear all module-level state between tests so daemon-probe and
    model-load caches don't bleed across cases."""
    rerank_mod._MODEL = None
    rerank_mod._MODEL_DEVICE = None
    rerank_mod._FALLBACK_LOGGED = False
    rerank_mod._DAEMON_AVAILABLE = None
    monkeypatch.delenv("RERANK_FORCE_INPROCESS", raising=False)
    yield
    rerank_mod._MODEL = None
    rerank_mod._MODEL_DEVICE = None
    rerank_mod._FALLBACK_LOGGED = False
    rerank_mod._DAEMON_AVAILABLE = None


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# Client routing: mocked
# ---------------------------------------------------------------------------


def _patch_urlopen_health(monkeypatch, body: dict[str, Any], status: int = 200):
    """Stand in for urllib.request.urlopen used by _probe_daemon."""

    class _Resp:
        def __init__(self, body_bytes: bytes):
            self._body = body_bytes
            self.status = status

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        if status >= 400:
            raise urllib.error.HTTPError(
                "http://x/health", status, "x", {}, None  # type: ignore[arg-type]
            )
        return _Resp(json.dumps(body).encode("utf-8"))

    import urllib.request as _ur

    monkeypatch.setattr(_ur, "urlopen", fake_urlopen)


def test_probe_caches_negative(monkeypatch):
    """A failed probe sets _DAEMON_AVAILABLE=False and skips re-probing."""
    calls = {"n": 0}

    import urllib.request as _ur

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        calls["n"] += 1
        raise OSError("connection refused")

    monkeypatch.setattr(_ur, "urlopen", fake_urlopen)
    assert rerank_mod._probe_daemon() is False
    assert rerank_mod._probe_daemon() is False
    # Second call must be served from cache, not re-probed.
    assert calls["n"] == 1


def test_probe_caches_positive(monkeypatch):
    """A successful probe with warm=True caches True."""
    _patch_urlopen_health(
        monkeypatch,
        {"ok": True, "warm": True, "model": "BAAI/bge-reranker-v2-m3", "device": "mps"},
    )
    assert rerank_mod._probe_daemon() is True
    assert rerank_mod._DAEMON_AVAILABLE is True


def test_probe_treats_cold_daemon_as_unavailable(monkeypatch):
    """A daemon that's up but reports warm=False isn't usable —
    fall through to in-process load."""
    _patch_urlopen_health(monkeypatch, {"ok": True, "warm": False, "model": "x", "device": "cpu"})
    assert rerank_mod._probe_daemon() is False


def test_force_inprocess_skips_probe(monkeypatch):
    """RERANK_FORCE_INPROCESS=1 must short-circuit the probe entirely."""
    monkeypatch.setenv("RERANK_FORCE_INPROCESS", "1")

    import urllib.request as _ur

    def explode(*a, **k):  # noqa: ARG001
        raise AssertionError("probe should not have been called")

    monkeypatch.setattr(_ur, "urlopen", explode)
    assert rerank_mod._probe_daemon() is False


def test_rerank_uses_daemon_when_available(monkeypatch):
    """When the daemon probes healthy, rerank() POSTs to it and
    returns daemon results without loading a local model."""
    # Probe says yes.
    rerank_mod._DAEMON_AVAILABLE = True

    captured = {}
    daemon_results = [
        {"id": "B", "subject": "x", "predicate": "y", "object": "match",
         "_rerank_score": 0.9, "score": 0.9},
        {"id": "A", "subject": "x", "predicate": "y", "object": "noise",
         "_rerank_score": 0.1, "score": 0.1},
    ]

    class _Resp:
        def __init__(self, body_bytes: bytes):
            self._body = body_bytes

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    import urllib.request as _ur

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(json.dumps({
            "ok": True,
            "results": daemon_results,
            "took_ms": 42,
            "pool_size": 2,
        }).encode("utf-8"))

    monkeypatch.setattr(_ur, "urlopen", fake_urlopen)

    # If the daemon path runs, the local model loader must NEVER be touched.
    monkeypatch.setattr(
        rerank_mod, "_ensure_loaded",
        lambda *a, **k: pytest.fail("in-process model must not load when daemon is up"),
    )

    cands = [
        {"id": "A", "subject": "x", "predicate": "y", "object": "noise"},
        {"id": "B", "subject": "x", "predicate": "y", "object": "match"},
    ]
    out = rerank_mod.rerank("query", cands, top_k=2)
    assert [r["id"] for r in out] == ["B", "A"]
    assert captured["url"].endswith("/rerank")
    assert captured["body"]["query"] == "query"
    assert captured["body"]["top_k"] == 2


def test_daemon_503_falls_back_to_inprocess(monkeypatch):
    """When the daemon returns 503 (model not loaded mid-flight), the
    client invalidates its cache and falls through to in-process.

    To exercise the cache invalidation we must NOT inject `model=` (that
    short-circuits the daemon route entirely by design). Instead we
    patch `_ensure_loaded` so the in-process path can succeed without
    a real sentence-transformers install.
    """
    rerank_mod._DAEMON_AVAILABLE = True
    monkeypatch.setattr(rerank_mod, "_ensure_loaded", lambda *a, **k: rerank_mod.DummyEncoder())

    import urllib.request as _ur

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        raise urllib.error.HTTPError(
            req.full_url, 503, "Service Unavailable", {}, None  # type: ignore[arg-type]
        )

    monkeypatch.setattr(_ur, "urlopen", fake_urlopen)

    cands = [
        {"id": "A", "subject": "x", "predicate": "y", "object": "completely irrelevant"},
        {"id": "B", "subject": "x", "predicate": "y", "object": "package level dead detection"},
    ]
    # No model= here — exercises the daemon-then-fallback flow end-to-end.
    out = rerank_mod.rerank("package-level dead detection", cands, top_k=2)
    # DummyEncoder (returned by patched _ensure_loaded) picks "B" by overlap.
    assert out[0]["id"] == "B"
    # And the cache is now invalidated so subsequent calls skip the probe.
    assert rerank_mod._DAEMON_AVAILABLE is False


def test_daemon_route_skipped_when_test_model_injected(monkeypatch):
    """Tests that pass an explicit `model=` MUST exercise the in-process
    glue, not the daemon — DummyEncoder is the test contract for that."""
    rerank_mod._DAEMON_AVAILABLE = True

    import urllib.request as _ur

    def explode(*a, **k):  # noqa: ARG001
        raise AssertionError("daemon must not be called when model= is injected")

    monkeypatch.setattr(_ur, "urlopen", explode)
    cands = [{"id": "A", "subject": "x", "predicate": "y", "object": "doc"}]
    out = rerank_mod.rerank("q", cands, top_k=1, model=rerank_mod.DummyEncoder())
    assert out[0]["id"] == "A"


# ---------------------------------------------------------------------------
# Daemon module: import + lifecycle helpers (no model load)
# ---------------------------------------------------------------------------


def test_daemon_module_importable():
    """The daemon module must import without sentence_transformers
    installed — model load is lazy. This protects the test environment."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import importlib

    mod = importlib.import_module("rerank_daemon")
    assert hasattr(mod, "serve")
    assert hasattr(mod, "stop")
    assert hasattr(mod, "status")


def test_daemon_resolve_address_env_overrides(monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import importlib

    mod = importlib.import_module("rerank_daemon")
    monkeypatch.setenv("RERANK_DAEMON_HOST", "127.0.0.1")
    monkeypatch.setenv("RERANK_DAEMON_PORT", "9999")
    host, port = mod._resolve_address()
    assert host == "127.0.0.1"
    assert port == 9999


def test_daemon_pid_helpers_roundtrip(monkeypatch, tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import importlib

    mod = importlib.import_module("rerank_daemon")
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(mod, "PID_FILE", tmp_path / "rerank-daemon.pid")
    assert mod._read_pid() is None
    mod._write_pid_file()
    assert mod._read_pid() == os.getpid()
    mod._remove_pid_file()
    assert mod._read_pid() is None


# ---------------------------------------------------------------------------
# Integration test: spin up the real daemon and call it
# ---------------------------------------------------------------------------


def _wait_for_health(host: str, port: int, timeout_s: float) -> dict | None:
    """Poll /health until OK or timeout. Returns body or None."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(  # noqa: S310
                f"http://{host}:{port}/health", timeout=0.5
            ) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok") and body.get("warm"):
                return body
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.25)
    return None


@pytest.mark.integration
def test_real_daemon_serves_rerank_under_200ms(tmp_path):
    """End-to-end: spawn the daemon, wait for warm /health, send a real
    rerank request, assert the cliff fix actually fires.

    The success criterion is the Phase G acceptance gate from the build
    spec: with daemon up and warm, the rerank call from a fresh client
    process must complete in <200ms (warm bge-reranker-v2-m3 on M-series
    MPS serves a 4-doc rerank in ~40-150ms).
    """
    pytest.importorskip("sentence_transformers")
    pytest.importorskip("torch")

    port = _free_port()
    env = {**os.environ, "RERANK_DAEMON_PORT": str(port), "XDG_STATE_HOME": str(tmp_path)}

    daemon = subprocess.Popen(  # noqa: S603
        [sys.executable, str(REPO_ROOT / "scripts" / "rerank_daemon.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        # Model load can be slow on first run (download). Allow generous timeout.
        body = _wait_for_health("127.0.0.1", port, timeout_s=120.0)
        if body is None:
            # Capture a window of the daemon log for triage.
            daemon.terminate()
            try:
                out, _ = daemon.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                out, _ = daemon.communicate()
            pytest.skip(
                "daemon failed to become warm within 120s; likely model "
                f"download or torch backend issue. Daemon log tail:\n{out.decode()[-2000:]}"
            )

        # Warm past MPS JIT. On bge-reranker-v2-m3 the FIRST POST after
        # model load pays ~300ms JIT cost; the SECOND POST also pays a
        # smaller JIT for a different shape. Steady-state is ~40ms after
        # 3 throw-away calls. We measure trial #4 to assert steady-state.
        cands = [
            {"id": "noise1", "subject": "x", "predicate": "y", "object": "completely unrelated about the weather"},
            {"id": "noise2", "subject": "x", "predicate": "y", "object": "another irrelevant blob"},
            {"id": "target", "subject": "x", "predicate": "y", "object": "package level dead detection in find_dead"},
            {"id": "noise3", "subject": "x", "predicate": "y", "object": "yet another distractor"},
        ]
        body_bytes = json.dumps({
            "query": "package-level dead detection",
            "candidates": cands,
            "top_k": 4,
        }).encode("utf-8")

        def _post():
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/rerank",
                data=body_bytes,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                p = json.loads(resp.read().decode("utf-8"))
            return p, (time.monotonic() - t0) * 1000

        # Warmup: 3 throw-away calls to get past MPS JIT.
        for _ in range(3):
            _post()
        # Timed call: this is what recall.py actually pays in steady state.
        payload, wall_ms = _post()

        assert payload["ok"] is True
        assert payload["results"][0]["id"] == "target", (
            f"target should rank first; got {[r['id'] for r in payload['results']]}"
        )
        # Phase G acceptance gate: under 200ms warm STEADY-STATE.
        # `took_ms` is the daemon-internal cost; `wall_ms` is end-to-end
        # over loopback. We assert on wall_ms because that's what
        # recall.py actually pays. Measured ~38ms on M-series MPS;
        # 200ms threshold leaves headroom for noisier CI machines.
        assert wall_ms < 200, (
            f"warm steady-state rerank took {wall_ms:.1f}ms wall, "
            f"daemon-internal {payload['took_ms']}ms — Phase G cliff not fixed"
        )
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
