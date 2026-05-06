#!/usr/bin/env python3
"""Long-running embedder daemon (Phase H).

The cold-load cliff: every fresh `recall.py` process pays ~3000ms loading
`mlx-community/mxbai-embed-large-v1` via mlx-embeddings before it can
embed the first query. SessionStart bash subshell warm() doesn't help —
the warmed model dies with the subshell (same pattern as the rerank
cliff Phase G fixed). The only architectural fix is a long-running
process that holds the model in memory across `recall.py` invocations.

This daemon mirrors `rerank_daemon.py` exactly:
  - Loads the active embedder backend once on startup (~3s, paid by
    SessionStart in the background, never on the user's interactive
    path).
  - Serves `POST /embed`, `POST /embed_batch`, and `GET /health` on
    `127.0.0.1:8766` (configurable via `EMBED_DAEMON_PORT`). Port 8766
    is adjacent to the rerank daemon's 8765 so they're easy to remember
    side-by-side.
  - Holds a single in-flight call lock — MLX is NOT safe under
    concurrent forward passes against the same model handle; queueing
    is the correct posture.
  - Shuts down cleanly on SIGTERM (PID file removal + thread join).

Why stdlib `http.server` and not FastAPI:
  - Two endpoints plus a health probe — no router, no validation
    framework, no startup/shutdown lifecycle hooks needed beyond the
    bare process boundary.
  - Zero extra dependency. The `[retrieval]` extra already pulls in
    mlx-embeddings (or Ollama via http); avoiding FastAPI + uvicorn +
    pydantic keeps the daemon's surface tight.
  - Cold-start cost matters for the lifecycle: stdlib server is up in
    milliseconds; the only meaningful startup cost is the model load.

Why JSON wire format (not msgpack/binary):
  - 1024-dim float32 vector serializes to ~30KB JSON. On localhost
    loopback, that round-trips in <2ms (measured). The dominant cost
    is the actual embed forward pass (~10-15ms warm), not transport.
  - Binary protocol adds complexity for a sub-millisecond saving;
    the spec says "ONLY pursue if measurement shows JSON is dominant".
    Measurement shows it isn't.

Wire format:
  POST /embed        body={"texts": [str, ...]}
                     returns {"ok": true, "embeddings": [[float, ...], ...],
                              "took_ms": int, "n": int, "dim": int}
  POST /embed_batch  alias for /embed (semantic clarity for callers)
  GET  /health       returns {"ok": bool, "backend": "mlx|ollama",
                              "model": str, "dim": int, "warm": bool,
                              "uptime_s": float, "pid": int}

Lifecycle:
  python3 embed_daemon.py            -> foreground (logs to stderr)
  python3 embed_daemon.py --stop     -> SIGTERM the running daemon
  python3 embed_daemon.py --status   -> print PID + uptime if running

State files (under XDG_STATE_HOME or ~/.local/state):
  build-loop/embed-daemon.pid        -> running daemon PID
  build-loop/embed-daemon.log        -> daemon log when launched via hook
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
ENV_PORT = "EMBED_DAEMON_PORT"
ENV_HOST = "EMBED_DAEMON_HOST"

STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
) / "build-loop"
PID_FILE = STATE_DIR / "embed-daemon.pid"

# Module-level state (process-singleton).
_START_TS: float = 0.0
_MODEL_READY: bool = False
_BACKEND_NAME: str = ""
_MODEL_NAME: str = ""
_DIM: int = 0
# In-flight serializer. mlx-embeddings .generate() against the same model
# handle is NOT thread-safe; concurrent calls can corrupt MLX's device
# pipeline. Serialize the same way rerank_daemon does.
_EMBED_LOCK = threading.Lock()

_log = logging.getLogger("embed_daemon")
if not _log.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("[embed_daemon %(asctime)s] %(message)s"))
    _log.addHandler(h)
    _log.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Model load (delegates to scripts/embed_backend.py to keep one source of truth)
# ---------------------------------------------------------------------------


def _load_backend() -> tuple[bool, str, str, int]:
    """Load the active backend and return (ready, backend_name, model_name, dim).

    Reuses `embed_backend._select_backend()` so the daemon and the
    in-process fallback path are loading the EXACT same backend the
    same way, picking up MLX_FORCE_FAIL, EMBED_BACKEND, EMBED_MODEL,
    etc. consistently.
    """
    global _BACKEND_NAME, _MODEL_NAME, _DIM
    # Force the backend to actually do its lazy import + model load by
    # running one tiny embed. If this succeeds, every subsequent embed()
    # call inside this process is steady-state warm.
    import embed_backend as eb  # noqa: PLC0415

    backend = eb._select_backend()
    _BACKEND_NAME = backend.name()
    _MODEL_NAME = getattr(backend, "model", None) or getattr(backend, "model_id", "unknown")
    try:
        # Warm-up call. MLX's first generate() does the actual model
        # load + device init; Ollama's first request triggers the model
        # pull-from-disk. We pay it here so /embed POSTs are warm.
        v = backend.embed("warmup")
        _DIM = len(v)
        return True, _BACKEND_NAME, _MODEL_NAME, _DIM
    except Exception as e:  # noqa: BLE001
        _log.warning("backend %s warmup embed failed: %s", _BACKEND_NAME, e)
        _DIM = eb.EMBED_DIM
        return False, _BACKEND_NAME, _MODEL_NAME, _DIM


def _do_embed(texts: list[str]) -> tuple[list[list[float]], int]:
    """Run the embed batch under the in-flight lock.

    Returns (embeddings, took_ms). Raises on backend failure so the
    handler can surface a 500.
    """
    import embed_backend as eb  # noqa: PLC0415

    started = time.monotonic()
    with _EMBED_LOCK:
        # eb.embed() accepts list[str] and dispatches to embed_batch.
        out = eb.embed(texts)
    took_ms = int((time.monotonic() - started) * 1000)
    # Coerce to list of lists of floats (defensive — MLX returns
    # floats-of-floats already, but keep the wire contract tight).
    return [[float(x) for x in row] for row in out], took_ms


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Three endpoints: GET /health, POST /embed, POST /embed_batch.

    All other paths return 404. Logs are silenced (we manage logging via
    the module logger) — `BaseHTTPRequestHandler.log_message` would
    otherwise spam stderr per request.
    """

    # Silence the default access log; we have our own.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json(status, {"ok": False, "error": message})

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._send_error(HTTPStatus.NOT_FOUND, f"unknown path: {self.path}")
            return
        body = {
            "ok": True,
            "backend": _BACKEND_NAME,
            "model": _MODEL_NAME,
            "dim": _DIM,
            "warm": _MODEL_READY,
            "uptime_s": round(time.monotonic() - _START_TS, 3),
            "pid": os.getpid(),
        }
        self._send_json(HTTPStatus.OK, body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/embed", "/embed_batch"):
            self._send_error(HTTPStatus.NOT_FOUND, f"unknown path: {self.path}")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return
        if length <= 0:
            self._send_error(HTTPStatus.BAD_REQUEST, "empty body")
            return
        try:
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            self._send_error(HTTPStatus.BAD_REQUEST, f"invalid JSON: {e}")
            return

        texts = payload.get("texts")
        if not isinstance(texts, list) or not texts:
            self._send_error(HTTPStatus.BAD_REQUEST, "texts must be a non-empty list")
            return
        if not all(isinstance(t, str) and t for t in texts):
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "every entry in texts must be a non-empty str",
            )
            return

        if not _MODEL_READY:
            # Daemon up but model didn't load — surface that clearly so
            # the client can fall back to in-process load instead of
            # hammering an unusable daemon.
            self._send_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "backend not loaded; daemon serving in degraded mode",
            )
            return

        try:
            embeddings, took_ms = _do_embed(texts)
        except Exception as e:  # noqa: BLE001
            _log.warning("embed failed: %s", e)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"embed failed: {e}")
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "embeddings": embeddings,
                "took_ms": took_ms,
                "n": len(embeddings),
                "dim": len(embeddings[0]) if embeddings else _DIM,
            },
        )


# ---------------------------------------------------------------------------
# Lifecycle: serve, stop, status
# ---------------------------------------------------------------------------


def _resolve_address() -> tuple[str, int]:
    host = os.environ.get(ENV_HOST, DEFAULT_HOST)
    try:
        port = int(os.environ.get(ENV_PORT, str(DEFAULT_PORT)))
    except ValueError:
        port = DEFAULT_PORT
    return host, port


def _write_pid_file() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _remove_pid_file() -> None:
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except OSError:
        pass


def _read_pid() -> int | None:
    try:
        text = PID_FILE.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    """Cheap "is this PID alive" check via signal 0 (POSIX)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but isn't ours. Treat as alive — we won't try
        # to manage it but we shouldn't claim it's gone either.
        return True


def _port_in_use(host: str, port: int) -> bool:
    """Probe whether a TCP listener is already bound to (host, port)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.1)
    try:
        s.connect((host, port))
        return True
    except (ConnectionRefusedError, socket.timeout):
        return False
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def serve() -> int:
    """Run the daemon in the foreground (blocking).

    Returns process exit code on shutdown.
    """
    global _START_TS, _MODEL_READY

    # Single-instance: refuse to start if a live daemon already owns the PID file.
    existing_pid = _read_pid()
    if existing_pid is not None and _pid_alive(existing_pid):
        _log.info(
            "daemon already running (pid=%d); refusing to start a second instance",
            existing_pid,
        )
        return 0

    host, port = _resolve_address()
    if _port_in_use(host, port):
        _log.warning(
            "port %s:%d already in use by a non-daemon process; aborting",
            host, port,
        )
        return 2

    _START_TS = time.monotonic()
    _log.info("loading embedder backend on startup (this is the one-time cold-load cost)…")
    ready, backend_name, model_name, dim = _load_backend()
    _MODEL_READY = ready
    if ready:
        _log.info(
            "backend %s ready model=%s dim=%d", backend_name, model_name, dim
        )
    else:
        _log.warning(
            "backend %s/%s failed to warm up; daemon will serve health "
            "but POST /embed will return 503",
            backend_name, model_name,
        )

    server = HTTPServer((host, port), _Handler)

    _shutdown_done = threading.Event()

    def _on_signal(signum: int, _frame: Any) -> None:
        _log.info("received signal %d; shutting down", signum)
        # Stop accepting new requests and drain. shutdown() must be called
        # from a different thread than serve_forever().
        threading.Thread(
            target=_graceful_close, args=(server, _shutdown_done), daemon=True
        ).start()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    _write_pid_file()
    _log.info("listening on http://%s:%d (pid=%d)", host, port, os.getpid())
    try:
        server.serve_forever()
    finally:
        _shutdown_done.wait(timeout=5.0)
        _remove_pid_file()
        _log.info("daemon stopped")
    return 0


def _graceful_close(server: HTTPServer, done: threading.Event) -> None:
    try:
        server.shutdown()
        server.server_close()
    finally:
        done.set()


def stop() -> int:
    """SIGTERM the running daemon (if any). Idempotent."""
    pid = _read_pid()
    if pid is None:
        _log.info("no PID file at %s; nothing to stop", PID_FILE)
        return 0
    if not _pid_alive(pid):
        _log.info("stale PID file (pid=%d not alive); cleaning up", pid)
        _remove_pid_file()
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _remove_pid_file()
        return 0
    except PermissionError:
        _log.warning("not permitted to signal pid=%d", pid)
        return 1
    # Give it a moment to clean up.
    for _ in range(50):
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    _log.info("daemon (pid=%d) shut down", pid)
    return 0


def status() -> int:
    """Print PID + uptime if running. Exit 0 if up, 1 if not."""
    pid = _read_pid()
    if pid is None or not _pid_alive(pid):
        print("daemon not running", file=sys.stderr)
        return 1
    host, port = _resolve_address()
    info = {"pid": pid, "host": host, "port": port}
    # Best-effort: try /health for richer info.
    try:
        import urllib.request  # noqa: PLC0415

        with urllib.request.urlopen(  # noqa: S310
            f"http://{host}:{port}/health", timeout=0.5
        ) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        info.update(body)
    except Exception:  # noqa: BLE001
        pass
    print(json.dumps(info, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build-loop embed daemon (Phase H)")
    p.add_argument("--stop", action="store_true", help="SIGTERM the running daemon and exit")
    p.add_argument("--status", action="store_true", help="Print daemon status and exit")
    args = p.parse_args(argv)
    if args.stop:
        return stop()
    if args.status:
        return status()
    return serve()


if __name__ == "__main__":
    sys.exit(main())
