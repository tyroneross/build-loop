#!/usr/bin/env python3
"""Long-running cross-encoder rerank daemon (Phase G).

The cold-load cliff: every fresh `recall.py` process pays ~5-6s loading
`BAAI/bge-reranker-v2-m3` via sentence-transformers + MPS before it can
score the first query. SessionStart bash subshell warm() doesn't help —
the warmed model dies with the subshell (commit ae384cb documents the
attempt). The only architectural fix is a long-running process that
holds the model in memory across `recall.py` invocations.

This daemon:
  - Loads the model once on startup (~3-5s, paid by SessionStart in the
    background, never on the user's interactive path).
  - Serves `POST /rerank` and `GET /health` on `127.0.0.1:8765`
    (configurable via `RERANK_DAEMON_PORT`).
  - Holds a single in-flight call lock — the cross-encoder + MPS device
    pipeline isn't safe under concurrent forward passes; queueing is the
    correct posture.
  - Shuts down cleanly on SIGTERM (PID file removal + thread join).

Why stdlib `http.server` and not FastAPI:
  - One endpoint plus a health probe — no router, no validation
    framework, no startup/shutdown lifecycle hooks needed beyond the
    bare process boundary.
  - Zero extra dependency. The `[retrieval]` extra already pulls in
    sentence-transformers + torch (~2GB); avoiding FastAPI + uvicorn +
    pydantic keeps the daemon's surface tight.
  - Cold-start cost matters: stdlib server is up in milliseconds; the
    only meaningful startup cost is the model load itself.

Wire format:
  POST /rerank  body={"query": str, "candidates": [{...}], "top_k": int}
                returns {"results": [{...with _rerank_score, score...}],
                         "took_ms": int, "pool_size": int}
  GET  /health  returns {"ok": bool, "model": str, "device": str,
                         "warm": bool, "uptime_s": float, "pid": int}

Lifecycle:
  python3 rerank_daemon.py            -> foreground (logs to stderr)
  python3 rerank_daemon.py --stop     -> SIGTERM the running daemon
  python3 rerank_daemon.py --status   -> print PID + uptime if running

State files (under XDG_STATE_HOME or ~/.local/state):
  build-loop/rerank-daemon.pid        -> running daemon PID
  build-loop/rerank-daemon.log        -> daemon log when launched via hook
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
DEFAULT_PORT = 8765
ENV_PORT = "RERANK_DAEMON_PORT"
ENV_HOST = "RERANK_DAEMON_HOST"

STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
) / "build-loop"
PID_FILE = STATE_DIR / "rerank-daemon.pid"

# Module-level state (process-singleton).
_START_TS: float = 0.0
_MODEL_READY: bool = False
_MODEL_NAME: str = ""
_DEVICE: str = ""
# In-flight serializer. CrossEncoder.predict on MPS is NOT thread-safe —
# concurrent forward passes can corrupt the device queue. Serialize.
_PREDICT_LOCK = threading.Lock()

_log = logging.getLogger("rerank_daemon")
if not _log.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("[rerank_daemon %(asctime)s] %(message)s"))
    _log.addHandler(h)
    _log.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Model load (delegates to scripts/rerank.py to keep one source of truth)
# ---------------------------------------------------------------------------


def _load_model() -> tuple[Any | None, str, str]:
    """Load the cross-encoder and return (model, name, device).

    Reuses `rerank._ensure_loaded` so the daemon and the in-process
    fallback path are loading the EXACT same model the same way.
    """
    global _MODEL_NAME, _DEVICE
    import rerank as rerank_mod  # noqa: PLC0415

    model = rerank_mod._ensure_loaded()
    if model is None:
        _MODEL_NAME = os.environ.get("RERANK_MODEL", rerank_mod.DEFAULT_MODEL)
        _DEVICE = "unavailable"
        return None, _MODEL_NAME, _DEVICE
    _MODEL_NAME = os.environ.get("RERANK_MODEL", rerank_mod.DEFAULT_MODEL)
    _DEVICE = rerank_mod._MODEL_DEVICE or "unknown"
    return model, _MODEL_NAME, _DEVICE


def _do_rerank(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int,
) -> tuple[list[dict[str, Any]], int]:
    """Run the rerank under the in-flight lock.

    Returns (results, took_ms).
    """
    import rerank as rerank_mod  # noqa: PLC0415

    started = time.monotonic()
    with _PREDICT_LOCK:
        results = rerank_mod.rerank(query, candidates, top_k=top_k)
    took_ms = int((time.monotonic() - started) * 1000)
    return results, took_ms


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Two endpoints: GET /health, POST /rerank.

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
            "model": _MODEL_NAME,
            "device": _DEVICE,
            "warm": _MODEL_READY,
            "uptime_s": round(time.monotonic() - _START_TS, 3),
            "pid": os.getpid(),
        }
        self._send_json(HTTPStatus.OK, body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/rerank":
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

        query = payload.get("query")
        candidates = payload.get("candidates")
        top_k = payload.get("top_k", 10)
        if not isinstance(query, str):
            self._send_error(HTTPStatus.BAD_REQUEST, "query must be str")
            return
        if not isinstance(candidates, list):
            self._send_error(HTTPStatus.BAD_REQUEST, "candidates must be list")
            return
        if not isinstance(top_k, int):
            self._send_error(HTTPStatus.BAD_REQUEST, "top_k must be int")
            return

        if not _MODEL_READY:
            # Daemon up but model didn't load — surface that clearly so
            # the client can fall back to in-process load instead of
            # hammering an unusable daemon.
            self._send_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "model not loaded; daemon serving in degraded mode",
            )
            return

        try:
            results, took_ms = _do_rerank(query, candidates, top_k)
        except Exception as e:  # noqa: BLE001
            _log.warning("rerank failed: %s", e)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"rerank failed: {e}")
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "results": results,
                "took_ms": took_ms,
                "pool_size": len(candidates),
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
    _log.info("loading model on startup (this is the one-time cold-load cost)…")
    model, model_name, device = _load_model()
    if model is None:
        _log.warning(
            "model %r failed to load; daemon will serve health "
            "but POST /rerank will return 503", model_name,
        )
        _MODEL_READY = False
    else:
        _MODEL_READY = True
        _log.info("model %r ready on device=%s", model_name, device)

    server = HTTPServer((host, port), _Handler)

    _shutdown_done = threading.Event()

    def _on_signal(signum: int, _frame: Any) -> None:
        _log.info("received signal %d; shutting down", signum)
        # Stop accepting new requests and drain. shutdown() must be called
        # from a different thread than serve_forever().
        threading.Thread(target=_graceful_close, args=(server, _shutdown_done), daemon=True).start()

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
    p = argparse.ArgumentParser(description="Build-loop rerank daemon (Phase G)")
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
