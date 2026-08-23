#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Stable Build Loop identity for native Rally actors.

Rally routes presence, claims, handoffs, and status by ``--tool``.  The host
family (``codex``, ``claude_code``, or ``cursor``) is therefore metadata, not a
session identity.  This module keeps both values explicit and gives every
native Rally caller the same environment/session resolution order.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
from dataclasses import dataclass
from typing import Mapping


_UNSAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]+")
MAX_NATIVE_TOOL_BYTES = 64
_HOST_SESSION_ENV: dict[str, tuple[str, ...]] = {
    "codex": ("CODEX_THREAD_ID", "CODEX_SESSION_ID"),
    "claude": ("CLAUDE_SESSION_ID",),
    "claude_code": ("CLAUDE_SESSION_ID",),
    "cursor": ("CURSOR_SESSION_ID",),
}


def _safe_segment(value: object, *, max_length: int = 72) -> str:
    """Return one bounded Rally identity segment without collision folding."""
    raw = str(value or "unknown").strip()
    cleaned = _UNSAFE_SEGMENT.sub("-", raw).strip("._-").lower() or "unknown"
    if cleaned == raw.lower() and len(cleaned) <= max_length:
        return cleaned
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    prefix_length = max(1, max_length - len(digest) - 1)
    prefix = cleaned[:prefix_length].rstrip("._-") or "unknown"
    return f"{prefix}-{digest}"


def base_tool(tool: object) -> str:
    """Return only the host-family portion of a tool or actor id."""
    raw = str(tool or "unknown").strip()
    family = raw.split(":", 1)[0]
    return _safe_segment(family, max_length=32)


def _explicit_actor(tool: object, *, max_length: int) -> str | None:
    raw = str(tool or "").strip()
    _family, separator, actor = raw.partition(":")
    if not separator or not actor.strip():
        return None
    return _safe_segment(actor, max_length=max_length)


def resolve_session_id(
    tool: object,
    explicit: object | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    observer_pid: int | None = None,
) -> str:
    """Resolve one stable session key shared by every Build Loop consumer.

    Explicit input wins.  The remaining order is Build Loop override, native
    host session id, Rally agent/session id, terminal id, then the long-lived
    observer/parent pid.  The pid fallback distinguishes concurrently-live
    host processes without using the short-lived hook/script pid.
    """
    env = os.environ if environ is None else environ
    family = base_tool(tool)
    candidates: list[object | None] = [explicit, env.get("BUILD_LOOP_RALLY_SESSION_ID")]
    candidates.extend(env.get(name) for name in _HOST_SESSION_ENV.get(family, ()))
    candidates.extend(
        (
            env.get("RALLY_AGENT_ID"),
            env.get("RALLY_SESSION_ID"),
            env.get("TERM_SESSION_ID"),
        )
    )
    for candidate in candidates:
        if candidate is not None and str(candidate).strip():
            return _safe_segment(candidate)

    if observer_pid is None:
        raw_observer = env.get("RALLY_OBSERVER_PID")
        try:
            observer_pid = int(raw_observer) if raw_observer else os.getppid()
        except (TypeError, ValueError):
            observer_pid = os.getppid()
    if isinstance(observer_pid, bool) or observer_pid is None or observer_pid <= 1:
        observer_pid = os.getppid()
    return _safe_segment(f"{family}-proc-{observer_pid}")


def native_tool_id(
    tool: object,
    session_id: object | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    observer_pid: int | None = None,
) -> str:
    """Return ``<base-tool>:<session-actor>`` for native Rally.

    A caller-supplied actor id is already an explicit routing identity and is
    preserved.  A bare host family receives the resolved session key.
    """
    family = base_tool(tool)
    actor_budget = MAX_NATIVE_TOOL_BYTES - len(family.encode("ascii")) - 1
    actor = _explicit_actor(tool, max_length=actor_budget)
    if actor is None:
        actor = _safe_segment(
            resolve_session_id(
                family,
                session_id,
                environ=environ,
                observer_pid=observer_pid,
            ),
            max_length=actor_budget,
        )
    return f"{family}:{actor}"


@dataclass(frozen=True)
class ActorIdentity:
    base_tool: str
    session_id: str
    native_tool: str


def resolve_identity(
    tool: object,
    session_id: object | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    observer_pid: int | None = None,
) -> ActorIdentity:
    """Resolve the three identity values consumers must keep distinct."""
    family = base_tool(tool)
    resolved_session = resolve_session_id(
        family,
        session_id,
        environ=environ,
        observer_pid=observer_pid,
    )
    return ActorIdentity(
        base_tool=family,
        session_id=resolved_session,
        native_tool=native_tool_id(
            tool,
            resolved_session,
            environ=environ,
            observer_pid=observer_pid,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tool", required=True)
    parser.add_argument("--session-id", default=None)
    parser.add_argument(
        "--field",
        choices=("native-tool", "base-tool", "session-id"),
        default="native-tool",
    )
    args = parser.parse_args(argv)
    identity = resolve_identity(args.tool, args.session_id)
    print(
        {
            "native-tool": identity.native_tool,
            "base-tool": identity.base_tool,
            "session-id": identity.session_id,
        }[args.field]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
