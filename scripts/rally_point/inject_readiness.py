#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Pane-backend readiness probe for Rally inject.

This checks whether the host has a backend that can receive pane injection
without starting tmux, ptyd, or any daemon. A missing backend is not an error:
callers should degrade to ledger handoffs.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
from pathlib import Path
from typing import Mapping

RALLY_PTYD_SOCKET_ENV = "RALLY_PTYD_SOCKET"
RALLY_PTYD_BIN_ENV = "RALLY_PTYD_BIN"
DEFAULT_SOCKET_TIMEOUT_SECONDS = 0.25

BackendName = str


def _which(name: str, env: Mapping[str, str]) -> str | None:
    return shutil.which(name, path=env.get("PATH"))


def tmux_available(env: Mapping[str, str] | None = None) -> bool:
    """True when `tmux` is executable on PATH."""
    return _which("tmux", env or os.environ) is not None


def ptyd_binary_available(env: Mapping[str, str] | None = None) -> bool:
    """True when Rally could invoke a ptyd binary without provisioning it."""
    env = env or os.environ
    explicit = env.get(RALLY_PTYD_BIN_ENV)
    if explicit:
        p = Path(explicit).expanduser()
        return p.is_file() and os.access(p, os.X_OK)
    return _which("ptyd", env) is not None


def ptyd_socket_path(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve Rally's owned ptyd socket path without probing it."""
    env = env or os.environ
    explicit = env.get(RALLY_PTYD_SOCKET_ENV)
    if explicit:
        return explicit
    home = env.get("HOME")
    if not home:
        return None
    return str(Path(home) / ".local" / "share" / "rally" / "ptyd.sock")


def ptyd_socket_live(
    socket_path: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_SOCKET_TIMEOUT_SECONDS,
) -> bool:
    """True iff a ptyd daemon answers a cheap `pane.list` request.

    File existence alone is not enough: a stale Unix socket from a crashed
    daemon must not make agents try `rally inject`.
    """
    path = socket_path or ptyd_socket_path(env)
    if not path or not Path(path).exists():
        return False

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(max(0.01, float(timeout_seconds)))
            client.connect(path)
            request = json.dumps({
                "id": "build-loop-inject-readiness",
                "method": "pane.list",
                "params": {},
            })
            client.sendall((request + "\n").encode("utf-8"))
            chunks: list[bytes] = []
            total = 0
            while total < 65536:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if b"\n" in chunk:
                    break
    except (OSError, TimeoutError, ValueError):
        return False
    raw = b"".join(chunks)
    if not raw:
        return False
    try:
        reply = json.loads(raw.decode("utf-8", "replace").splitlines()[0])
    except (IndexError, json.JSONDecodeError, UnicodeError):
        return False
    return isinstance(reply, dict) and "result" in reply


def recommended_backend(
    *,
    tmux: bool,
    ptyd_socket_live_: bool,
    ptyd_bin: bool,
) -> BackendName:
    """Choose the cheapest viable pane backend, or `handoff` when absent."""
    if ptyd_socket_live_:
        return "ptyd"
    if tmux:
        return "tmux"
    if ptyd_bin:
        return "ptyd"
    return "handoff"


def probe(
    *,
    env: Mapping[str, str] | None = None,
    socket_path: str | None = None,
    timeout_seconds: float = DEFAULT_SOCKET_TIMEOUT_SECONDS,
) -> dict[str, bool | str]:
    """Return Rally inject readiness in the stable handoff shape."""
    env = env or os.environ
    tmux = tmux_available(env)
    ptyd_bin = ptyd_binary_available(env)
    socket_live = ptyd_socket_live(
        socket_path,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    backend = recommended_backend(
        tmux=tmux,
        ptyd_socket_live_=socket_live,
        ptyd_bin=ptyd_bin,
    )
    return {
        "tmux": tmux,
        "ptyd_socket_live": socket_live,
        "ptyd_bin": ptyd_bin,
        "inject_available": backend != "handoff",
        "recommended_backend": backend,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Report Rally pane-backend readiness for inject."
    )
    p.add_argument(
        "--socket",
        default=None,
        help="Override the ptyd socket path to probe.",
    )
    p.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_SOCKET_TIMEOUT_SECONDS,
        help="Unix-socket probe timeout.",
    )
    p.add_argument("--json", action="store_true", help="Print JSON output")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = probe(socket_path=args.socket, timeout_seconds=args.timeout_seconds)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
