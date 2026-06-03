#!/usr/bin/env python3
# capability:
#   purpose: Serve build-loop-memory hot context and evidence reads over a local HTTP API.
#   application: memory
#   status: experimental
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Optional local HTTP adapter for build-loop-memory.

This module is intentionally thin.  The source of truth remains
`memory_context`; the HTTP layer only parses JSON/query params and returns
the same envelopes as the Python API and `blm` CLI.

Endpoints:
  GET  /health
  GET  /status?workdir=<path>
  GET  /context?workdir=<path>&query=<goal>&mode=fast
  POST /context  {"workdir": str, "query": str, "mode": "fast|expand"}
  GET  /open?id=<evidence-id>&workdir=<path>
  POST /open     {"id": str, "workdir": str}

API calls are read-only by default.  `/context` writes CURRENT files only
when `write=true` is explicitly supplied.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from memory_context import (  # type: ignore  # noqa: E402
    SCHEMA_VERSION,
    build_context,
    describe_access,
    open_artifact,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8777
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected integer, got {value!r}") from exc


def _first(params: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    values = params.get(key)
    if not values:
        return default
    return values[0]


def _json_bytes(body: dict[str, Any]) -> bytes:
    return json.dumps(body, indent=2, sort_keys=True, default=str).encode("utf-8")


def _safe_error(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def _context_from_payload(payload: dict[str, Any], *, default_write: bool) -> dict[str, Any]:
    workdir = Path(str(payload.get("workdir") or os.getcwd())).resolve()
    return build_context(
        workdir=workdir,
        query=str(payload.get("query") or ""),
        mode=str(payload.get("mode") or "fast"),
        project=payload.get("project") or None,
        write=_as_bool(payload.get("write"), default=default_write),
        limit=_as_int(payload.get("limit"), default=5),
        max_chars=_as_int(payload.get("max_chars"), default=900),
    )


def _open_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    artifact_id = payload.get("id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("id is required")
    return open_artifact(
        artifact_id,
        workdir=Path(str(payload.get("workdir") or os.getcwd())).resolve(),
        project=payload.get("project") or None,
        max_chars=_as_int(payload.get("max_chars"), default=8000),
    )


def make_handler(*, start_ts: float | None = None) -> type[BaseHTTPRequestHandler]:
    started = start_ts or time.monotonic()

    class BuildLoopMemoryHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_json(self, status: int, body: dict[str, Any]) -> None:
            payload = _json_bytes(body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_error(self, status: int, message: str) -> None:
            self._send_json(status, {"ok": False, "error": message})

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid JSON: {exc}") from exc

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            try:
                if parsed.path in {"/", "/health"}:
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "schema_version": SCHEMA_VERSION,
                            "service": "build-loop-memory-api",
                            "uptime_s": round(time.monotonic() - started, 3),
                            "pid": os.getpid(),
                            "endpoints": ["/health", "/status", "/context", "/open"],
                        },
                    )
                    return
                if parsed.path == "/status":
                    server_host, server_port = self.server.server_address[:2]
                    payload = {
                        "workdir": _first(params, "workdir") or os.getcwd(),
                        "project": _first(params, "project"),
                    }
                    self._send_json(
                        HTTPStatus.OK,
                        describe_access(
                            **payload,
                            host=str(server_host),
                            port=int(server_port),
                        ),
                    )
                    return
                if parsed.path == "/context":
                    payload = {
                        "workdir": _first(params, "workdir") or os.getcwd(),
                        "query": _first(params, "query") or "",
                        "project": _first(params, "project"),
                        "mode": _first(params, "mode") or "fast",
                        "limit": _first(params, "limit"),
                        "max_chars": _first(params, "max_chars"),
                        "write": _first(params, "write"),
                    }
                    self._send_json(HTTPStatus.OK, _context_from_payload(payload, default_write=False))
                    return
                if parsed.path == "/open":
                    payload = {
                        "id": _first(params, "id"),
                        "workdir": _first(params, "workdir") or os.getcwd(),
                        "project": _first(params, "project"),
                        "max_chars": _first(params, "max_chars"),
                    }
                    result = _open_from_payload(payload)
                    self._send_json(HTTPStatus.OK if result.get("exists") else HTTPStatus.NOT_FOUND, result)
                    return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # noqa: BLE001
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, _safe_error(exc))
                return
            self._send_error(HTTPStatus.NOT_FOUND, f"unknown path: {parsed.path}")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                payload = self._read_json()
                if parsed.path == "/context":
                    self._send_json(HTTPStatus.OK, _context_from_payload(payload, default_write=False))
                    return
                if parsed.path == "/open":
                    result = _open_from_payload(payload)
                    self._send_json(HTTPStatus.OK if result.get("exists") else HTTPStatus.NOT_FOUND, result)
                    return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # noqa: BLE001
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, _safe_error(exc))
                return
            self._send_error(HTTPStatus.NOT_FOUND, f"unknown path: {parsed.path}")

    return BuildLoopMemoryHandler


def serve(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    allow_nonlocal: bool = False,
) -> None:
    if host not in LOCAL_HOSTS and not allow_nonlocal:
        raise ValueError(f"refusing non-local bind {host!r}; pass --allow-nonlocal to override")
    server = ThreadingHTTPServer((host, port), make_handler())
    print(f"build-loop-memory API listening on http://{host}:{server.server_port}", file=sys.stderr)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("BLM_API_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BLM_API_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--allow-nonlocal", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        serve(host=args.host, port=args.port, allow_nonlocal=args.allow_nonlocal)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
