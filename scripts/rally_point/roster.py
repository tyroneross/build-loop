#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rally Point roster — cross-channel live agent view.

Walks every ``<apps_root>/*/sessions/*.json`` (all repos/channels at
once), filters to sessions still heartbeating within a stale window,
and builds a parent/child tree from each record's ``parent`` link plus
the self-reported ``spawned`` fan-out. This is the read-only data layer
behind ``agent_rally.py roster``; rendering lives in the CLI.

Stdlib only. Never raises on a malformed/partial session file — a bad
record is skipped, not fatal.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

try:  # package import
    from . import channel_paths
except ImportError:  # script import
    import channel_paths  # type: ignore

_SESSIONS_DIR = "sessions"
DEFAULT_STALE_SECS = 120


def _last_seen(rec: dict) -> float:
    """Epoch of last heartbeat. Prefer ``last_seen``; fall back to the
    legacy ``heartbeat_ts`` for records written before the field existed."""
    try:
        return float(rec.get("last_seen", rec.get("heartbeat_ts", 0)) or 0)
    except (TypeError, ValueError):
        return 0.0


def _spawned_total(rec: dict) -> int:
    spawned = rec.get("spawned") or {}
    if not isinstance(spawned, dict):
        return 0
    total = 0
    for v in spawned.values():
        try:
            total += int(v)
        except (TypeError, ValueError):
            continue
    return total


def iter_session_records(apps_root: Path | None = None, *, app: str | None = None):
    """Yield ``(app_dir_name, record)`` for every session JSON on disk.

    ``apps_root`` defaults to ``channel_paths.apps_root()`` (which honours
    ``$AGENT_RALLY_APPS_ROOT`` / ``$BUILD_LOOP_APPS_ROOT``). ``app``
    filters to a single channel directory name (the app slug). Reader
    cursor stubs (``tool == "reader"``) are skipped — they are not agents.
    """
    root = Path(apps_root) if apps_root is not None else channel_paths.apps_root()
    try:
        app_dirs = sorted(d for d in root.iterdir() if d.is_dir())
    except (OSError, FileNotFoundError):
        return
    for app_dir in app_dirs:
        if app is not None and app_dir.name != app:
            continue
        sessions = app_dir / _SESSIONS_DIR
        try:
            files = sorted(sessions.glob("*.json"))
        except OSError:
            continue
        for f in files:
            try:
                rec = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("tool") == "reader":
                continue
            yield app_dir.name, rec


def build_roster(
    apps_root: Path | None = None,
    *,
    app: str | None = None,
    stale_secs: int = DEFAULT_STALE_SECS,
    include_stale: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Build the cross-channel roster.

    Returns ``{"generated_ts", "stale_secs", "live_count", "stale_count",
    "agents": [...]}``. ``agents`` is the list of TOP-LEVEL agents (no
    ``parent``, or a parent not present in the live set), each with a
    ``children`` list of nested live agents whose ``parent`` points at it.

    Each agent object carries: ``session_id``, ``app``, ``host``, ``cwd``,
    ``tool``, ``model``, ``task``, ``parent``, ``last_seen``, ``age_secs``,
    ``spawned`` (dict), ``spawned_total`` (int), ``stale`` (bool when
    ``include_stale``), ``branch_name``, ``children``.

    Subagents that posted their own presence appear nested under their
    parent. Subagents that did NOT post presence are reflected only by the
    parent's ``spawned`` totals.
    """
    now = time.time() if now is None else now
    cutoff = now - stale_secs

    flat: list[dict[str, Any]] = []
    stale_count = 0
    for app_name, rec in iter_session_records(apps_root, app=app):
        ls = _last_seen(rec)
        is_stale = ls < cutoff
        if is_stale and not include_stale:
            stale_count += 1
            continue
        spawned = rec.get("spawned") or {}
        if not isinstance(spawned, dict):
            spawned = {}
        flat.append({
            "session_id": rec.get("session_id"),
            "app": app_name,
            "host": rec.get("host") or "unknown",
            "cwd": rec.get("cwd") or "unknown",
            "tool": rec.get("tool") or "unknown",
            "model": rec.get("model") or "unknown",
            "task": rec.get("task") or rec.get("phase") or "",
            "phase": rec.get("phase") or "",
            "parent": rec.get("parent") or None,
            "last_seen": ls,
            "age_secs": max(0.0, round(now - ls, 1)) if ls else None,
            "spawned": {str(k): int(v) for k, v in spawned.items()
                        if _coerce_int(v) is not None},
            "spawned_total": _spawned_total(rec),
            "branch_name": rec.get("branch_name") or "unknown",
            "run_id": rec.get("run_id") or "unknown",
            "build_loop_run_label": rec.get("build_loop_run_label"),
            "stale": is_stale,
            "children": [],
        })

    by_id = {a["session_id"]: a for a in flat if a["session_id"]}
    roots: list[dict[str, Any]] = []
    for a in flat:
        parent_id = a["parent"]
        if parent_id and parent_id in by_id and parent_id != a["session_id"]:
            by_id[parent_id]["children"].append(a)
        else:
            roots.append(a)

    # Deterministic order: app, then session_id.
    def _key(a: dict) -> tuple:
        return (a["app"] or "", a["session_id"] or "")

    roots.sort(key=_key)
    for a in flat:
        a["children"].sort(key=_key)

    return {
        "generated_ts": now,
        "stale_secs": stale_secs,
        "live_count": len(flat),
        "stale_count": stale_count,
        "agents": roots,
    }


def _coerce_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _fmt_age(age: float | None) -> str:
    if age is None:
        return "?"
    age = int(age)
    if age < 60:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m{age % 60:02d}s"
    return f"{age // 3600}h{(age % 3600) // 60:02d}m"


def _fmt_spawned(agent: dict) -> str:
    spawned = agent.get("spawned") or {}
    total = agent.get("spawned_total", 0)
    live_children = len(agent.get("children") or [])
    if not spawned and not live_children:
        return "-"
    parts = []
    if total:
        by_type = " ".join(f"{k}:{v}" for k, v in sorted(spawned.items()))
        parts.append(f"Σ{total} ({by_type})")
    if live_children:
        parts.append(f"+{live_children} live")
    return "  ".join(parts) if parts else "-"


def _short_cwd(cwd: str, host: str) -> str:
    home = str(Path.home())
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]
    return f"{host}:{cwd}"


def render_text(roster: dict[str, Any]) -> str:
    """Render the roster as an indented tree for human reading."""
    lines: list[str] = []
    gen = time.strftime("%H:%M:%S", time.localtime(roster["generated_ts"]))
    lines.append(
        f"Rally roster @ {gen}  ·  live={roster['live_count']} "
        f"stale={roster['stale_count']} (window {roster['stale_secs']}s)"
    )
    if not roster["agents"]:
        lines.append("  (no live agents)")
        return "\n".join(lines)

    def emit(agent: dict, depth: int) -> None:
        pad = "  " + "    " * depth + ("└─ " if depth else "")
        tool = agent["tool"]
        model = agent["model"]
        toolmodel = f"{tool}/{model}" if model and model != "unknown" else tool
        task = agent["task"] or agent["phase"] or "-"
        lines.append(
            f"{pad}{agent['session_id']}  [{agent['app']}]  "
            f"{_short_cwd(agent['cwd'], agent['host'])}"
        )
        lines.append(
            f"{pad}    {toolmodel} · {task} · "
            f"seen {_fmt_age(agent['age_secs'])} ago · "
            f"subagents {_fmt_spawned(agent)}"
        )
        for child in agent.get("children") or []:
            emit(child, depth + 1)

    for agent in roster["agents"]:
        emit(agent, 0)
    return "\n".join(lines)
