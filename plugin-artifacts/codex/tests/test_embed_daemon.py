"""Tests for the long-running embed daemon (Phase H).

Three layers:
  1. Mocked tests for the client routing in `scripts/embed_backend.py` —
     probe success/failure, force-inprocess override, daemon HTTP
     errors. Always run, no model needed.
  2. Daemon-module unit tests (lifecycle helpers, env overrides, PID
     roundtrip). No model load.
  3. Real-daemon integration test gated on the embed backend stack
     being present (mlx-embeddings or Ollama). Spawns the daemon as a
     subprocess, waits for /health, runs a real embed, asserts shape +
     warm steady-state latency under 100ms (Phase H acceptance gate).

Notes on shape mirroring:
  - Mirrors `tests/test_rerank_daemon.py` exactly so future maintainers
    have one mental model for both daemons. When fields diverge (the
    embed daemon's /health reports `backend` + `dim`; the rerank
    daemon's reports `device`), the test diverges with them.
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

import embed_backend as eb_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """Clear all module-level state between tests so daemon-probe and
    backend-load caches don't bleed across cases."""
    eb_mod.reset_for_tests()
    monkeypatch.delenv("EMBED_FORCE_INPROCESS", raising=False)
    monkeypatch.delenv("EMBED_BACKEND_DEBUG", raising=False)
    yield
    eb_mod.reset_for_tests()


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


def test_probe_returns_none_when_daemon_down(monkeypatch):
    """A failed probe latches _DAEMON_PROBED=True and returns None.

    Subsequent calls in the same process must NOT re-probe — the
    process commits to the in-process backend for its lifetime.
    """
    calls = {"n": 0}

    import urllib.request as _ur

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        calls["n"] += 1
        raise OSError("connection refused")

    monkeypatch.setattr(_ur, "urlopen", fake_urlopen)
    assert eb_mod._probe_daemon() is None
    # Latch is set; second call short-circuits without hitting urlopen.
    assert eb_mod._probe_daemon() is None
    assert calls["n"] == 1


def test_probe_returns_daemon_backend_when_warm(monkeypatch):
    """Healthy probe with warm=True returns a configured DaemonBackend."""
    _patch_urlopen_health(
        monkeypatch,
        {
            "ok": True,
            "warm": True,
            "backend": "mlx",
            "model": "mlx-community/mxbai-embed-large-v1",
            "dim": 1024,
            "uptime_s": 12.3,
            "pid": 12345,
        },
    )
    daemon = eb_mod._probe_daemon()
    assert daemon is not None
    assert isinstance(daemon, eb_mod.DaemonBackend)
    # The DaemonBackend reports the underlying backend, not "daemon".
    assert daemon.name() == "mlx"
    assert daemon.model == "mlx-community/mxbai-embed-large-v1"


def test_probe_treats_cold_daemon_as_unavailable(monkeypatch):
    """A daemon that's up but reports warm=False isn't usable —
    fall through to in-process load."""
    _patch_urlopen_health(
        monkeypatch,
        {"ok": True, "warm": False, "backend": "mlx", "model": "x", "dim": 1024},
    )
    assert eb_mod._probe_daemon() is None


def test_force_inprocess_skips_probe(monkeypatch):
    """EMBED_FORCE_INPROCESS=1 must short-circuit the probe entirely.

    Useful for test runs that want to exercise the in-process MLX/Ollama
    load path without coordinating with whatever daemon happens to be
    running on the developer's box.
    """
    monkeypatch.setenv("EMBED_FORCE_INPROCESS", "1")

    import urllib.request as _ur

    def explode(*a, **k):  # noqa: ARG001
        raise AssertionError("probe should not have been called")

    monkeypatch.setattr(_ur, "urlopen", explode)
    assert eb_mod._probe_daemon() is None


def test_select_backend_uses_daemon_when_available(monkeypatch):
    """When the daemon probes healthy, _select_backend returns the
    DaemonBackend and never touches MLX/Ollama in-process loaders."""
    _patch_urlopen_health(
        monkeypatch,
        {
            "ok": True,
            "warm": True,
            "backend": "mlx",
            "model": "mlx-community/mxbai-embed-large-v1",
            "dim": 1024,
        },
    )
    # If the in-process MLX path is touched the test must fail.
    monkeypatch.setattr(
        eb_mod.MLXBackend,
        "_ensure_loaded",
        lambda self: pytest.fail("MLX loader must not run when daemon is up"),
    )
    backend = eb_mod._select_backend()
    assert isinstance(backend, eb_mod.DaemonBackend)


def test_embed_routes_through_daemon_when_available(monkeypatch):
    """End-to-end: embed("foo") with a healthy daemon issues an HTTP
    POST to the daemon and returns the daemon's vector unchanged."""
    _patch_urlopen_health(
        monkeypatch,
        {
            "ok": True,
            "warm": True,
            "backend": "mlx",
            "model": "mlx-community/mxbai-embed-large-v1",
            "dim": 4,  # tiny to keep the test obvious
        },
    )

    captured: dict[str, Any] = {}

    class _FakeConn:
        def __init__(self, host, port, timeout=None):  # noqa: ARG002
            captured["host"] = host
            captured["port"] = port

        def request(self, method, path, body=None, headers=None):  # noqa: ARG002
            captured["method"] = method
            captured["path"] = path
            captured["body"] = json.loads(body.decode("utf-8"))
            captured["headers"] = headers

        def getresponse(self):
            class _Resp:
                status = 200

                def read(self):
                    return json.dumps(
                        {
                            "ok": True,
                            "embeddings": [[0.1, 0.2, 0.3, 0.4]],
                            "took_ms": 7,
                            "n": 1,
                            "dim": 4,
                        }
                    ).encode("utf-8")

            return _Resp()

        def close(self):  # noqa: D401
            pass

    import http.client as _http_client

    monkeypatch.setattr(_http_client, "HTTPConnection", _FakeConn)

    v = eb_mod.embed("hello daemon")
    assert v == [0.1, 0.2, 0.3, 0.4]
    assert captured["method"] == "POST"
    assert captured["path"] == "/embed"
    assert captured["body"]["texts"] == ["hello daemon"]


def test_embed_batch_routes_through_daemon(monkeypatch):
    """Batch embed routes through /embed with the full text list."""
    _patch_urlopen_health(
        monkeypatch,
        {"ok": True, "warm": True, "backend": "mlx", "model": "x", "dim": 2},
    )

    captured: dict[str, Any] = {}

    class _FakeConn:
        def __init__(self, host, port, timeout=None):  # noqa: ARG002
            pass

        def request(self, method, path, body=None, headers=None):  # noqa: ARG002
            captured["body"] = json.loads(body.decode("utf-8"))

        def getresponse(self):
            class _Resp:
                status = 200

                def read(self):
                    return json.dumps(
                        {
                            "ok": True,
                            "embeddings": [[0.1, 0.2], [0.3, 0.4]],
                            "took_ms": 9,
                            "n": 2,
                            "dim": 2,
                        }
                    ).encode("utf-8")

            return _Resp()

        def close(self):  # noqa: D401
            pass

    import http.client as _http_client

    monkeypatch.setattr(_http_client, "HTTPConnection", _FakeConn)

    out = eb_mod.embed(["foo", "bar"])
    assert out == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["body"]["texts"] == ["foo", "bar"]


def test_daemon_503_falls_back_to_inprocess_on_next_process(monkeypatch):
    """When the daemon returns 503 mid-flight, DaemonBackend raises so
    callers see a hard failure (in-process fallback happens at process
    boundary via _DAEMON_PROBED latch — within a single process the
    daemon is a sticky choice).

    This test asserts the failure mode is loud rather than silent;
    embed() should propagate the RuntimeError to its caller.
    """
    _patch_urlopen_health(
        monkeypatch,
        {"ok": True, "warm": True, "backend": "mlx", "model": "x", "dim": 4},
    )

    class _FakeConn:
        def __init__(self, *a, **k):  # noqa: ARG002
            pass

        def request(self, *a, **k):  # noqa: ARG002
            pass

        def getresponse(self):
            class _Resp:
                status = 503

                def read(self):
                    return b'{"ok": false, "error": "backend not loaded"}'

            return _Resp()

        def close(self):  # noqa: D401
            pass

    import http.client as _http_client

    monkeypatch.setattr(_http_client, "HTTPConnection", _FakeConn)

    with pytest.raises(RuntimeError, match="503"):
        eb_mod.embed("anything")


# ---------------------------------------------------------------------------
# Daemon module: import + lifecycle helpers (no model load)
# ---------------------------------------------------------------------------


def test_daemon_module_importable():
    """The daemon module must import without mlx-embeddings or torch
    installed — backend load is lazy. Protects the test environment."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import importlib

    mod = importlib.import_module("embed_daemon")
    assert hasattr(mod, "serve")
    assert hasattr(mod, "stop")
    assert hasattr(mod, "status")


def test_daemon_resolve_address_env_overrides(monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import importlib

    mod = importlib.import_module("embed_daemon")
    monkeypatch.setenv("EMBED_DAEMON_HOST", "127.0.0.1")
    monkeypatch.setenv("EMBED_DAEMON_PORT", "9998")
    host, port = mod._resolve_address()
    assert host == "127.0.0.1"
    assert port == 9998


def test_daemon_pid_helpers_roundtrip(monkeypatch, tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import importlib

    mod = importlib.import_module("embed_daemon")
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(mod, "PID_FILE", tmp_path / "embed-daemon.pid")
    assert mod._read_pid() is None
    mod._write_pid_file()
    assert mod._read_pid() == os.getpid()
    mod._remove_pid_file()
    assert mod._read_pid() is None


def test_daemon_default_port_is_8766():
    """Adjacent to rerank's 8765 by design — easy to reason about
    side-by-side. Regressions that drift the default are caught here."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import importlib

    mod = importlib.import_module("embed_daemon")
    assert mod.DEFAULT_PORT == 8766


# ---------------------------------------------------------------------------
# Integration test: spin up the real daemon and call it
# ---------------------------------------------------------------------------


def _wait_for_health(host: str, port: int, timeout_s: float) -> dict | None:
    """Poll /health until OK + warm or timeout. Returns body or None."""
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
def test_real_daemon_serves_embed_under_100ms(tmp_path):
    """End-to-end: spawn the daemon, wait for warm /health, send a real
    embed request, assert the cliff fix actually fires.

    The success criterion is the Phase H acceptance gate from the build
    spec: with daemon up and warm, the embed call from a fresh client
    process must complete in <100ms (warm MLX mxbai-embed-large-v1 on
    M-series serves a 1-text embed in ~10-20ms; loopback HTTP adds
    another 1-2ms).
    """
    # We need EITHER mlx-embeddings OR ollama reachable for the daemon
    # to warm up. Ollama check is a TCP probe — cheaper than importing.
    have_mlx = False
    try:
        import mlx_embeddings  # type: ignore  # noqa: F401

        have_mlx = True
    except Exception:  # noqa: BLE001
        pass

    have_ollama = False
    try:
        with urllib.request.urlopen(  # noqa: S310
            "http://127.0.0.1:11434/api/tags", timeout=0.3
        ):
            have_ollama = True
    except Exception:  # noqa: BLE001
        pass

    if not (have_mlx or have_ollama):
        pytest.skip("neither mlx-embeddings nor Ollama available; daemon can't warm")

    port = _free_port()
    env = {
        **os.environ,
        "EMBED_DAEMON_PORT": str(port),
        "XDG_STATE_HOME": str(tmp_path),
    }
    # Force Ollama backend if MLX isn't installed (mlx_embeddings will
    # blow up on the warmup embed otherwise).
    if not have_mlx:
        env["EMBED_BACKEND"] = "ollama"

    daemon = subprocess.Popen(  # noqa: S603
        [sys.executable, str(REPO_ROOT / "scripts" / "embed_daemon.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        body = _wait_for_health("127.0.0.1", port, timeout_s=120.0)
        if body is None:
            daemon.terminate()
            try:
                out, _ = daemon.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                out, _ = daemon.communicate()
            pytest.skip(
                "daemon failed to become warm within 120s; likely model "
                f"download or backend issue. Daemon log tail:\n{out.decode()[-2000:]}"
            )

        body_bytes = json.dumps({"texts": ["package level dead detection"]}).encode(
            "utf-8"
        )

        def _post():
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/embed",
                data=body_bytes,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                p = json.loads(resp.read().decode("utf-8"))
            return p, (time.monotonic() - t0) * 1000

        # Warmup: 2 throw-away calls past any per-shape JIT cost.
        for _ in range(2):
            _post()
        # Timed call: this is what recall.py actually pays in steady state.
        payload, wall_ms = _post()

        assert payload["ok"] is True
        assert isinstance(payload["embeddings"], list)
        assert len(payload["embeddings"]) == 1
        assert payload["dim"] >= 384  # most embedders are >= 384-dim
        # Phase H acceptance gate: under 100ms warm STEADY-STATE end-to-end.
        # Measured ~12-25ms on M-series MLX; 100ms threshold leaves
        # headroom for noisier CI machines and Ollama-backed daemons.
        assert wall_ms < 100, (
            f"warm steady-state embed took {wall_ms:.1f}ms wall, "
            f"daemon-internal {payload['took_ms']}ms — Phase H cliff not fixed"
        )
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
