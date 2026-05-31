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
    from .leadership import is_lease_valid, read_lead
except ImportError:  # script import
    import channel_paths  # type: ignore
    from leadership import is_lease_valid, read_lead  # type: ignore

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


def render_named(roster: dict[str, Any]) -> str:
    """Render the roster grouped by app with per-tool sequence numbers.

    Layout:
      [app-slug]
        Claude 01 ★  session-id  · task · seen Xs ago · subagents …
          └─ Claude 02  child-id  · task · seen Xs ago
        Codex  01  other-id  · task · seen Xs ago

    Numbering: per-tool, 1-based, assigned by ascending session_id within
    the full flat set (deterministic, matches build_roster sort order).

    Lead marker (★) — two-tier resolution, per app group:
      1. Authoritative: read ``<apps_root>/<app>/rally/lead.json`` via
         ``leadership.read_lead`` + ``is_lease_valid``.  If the file exists
         and the lease has NOT expired, exactly the session recorded as
         ``lead.session_id`` receives ★ — regardless of tree position.
      2. Orchestrator heuristic (fallback, used only when no valid lead.json
         exists for the app): mark ★ only agents that have actually spawned
         or are leading others — those with live ``children`` or
         ``spawned_total > 0``.  A plain independent session with neither
         gets no marker.

    Subagent nesting: uses the ``children`` list built by ``build_roster``
    from the ``parent`` field — full nesting is available and used.
    """
    lines: list[str] = []
    gen = time.strftime("%H:%M:%S", time.localtime(roster["generated_ts"]))
    lines.append(
        f"Rally roster (named) @ {gen}  ·  live={roster['live_count']} "
        f"stale={roster['stale_count']} (window {roster['stale_secs']}s)"
    )

    all_agents = roster["agents"]
    if not all_agents:
        lines.append("  (no live agents)")
        return "\n".join(lines)

    # Build flat list (roots + all children, depth-first) to assign tool
    # sequence numbers before grouping.
    flat_all: list[dict] = []

    def _collect(agent: dict) -> None:
        flat_all.append(agent)
        for child in agent.get("children") or []:
            _collect(child)

    for agent in all_agents:
        _collect(agent)

    # Per-tool counter: assign sequence numbers sorted by session_id
    # (same deterministic key used by build_roster).
    tool_agents: dict[str, list[dict]] = {}
    for a in flat_all:
        tool_agents.setdefault(a["tool"], []).append(a)
    for agents in tool_agents.values():
        agents.sort(key=lambda a: a["session_id"] or "")

    seq_num: dict[str, int] = {}  # session_id -> sequence number for its tool
    for agents in tool_agents.values():
        for i, a in enumerate(agents, start=1):
            seq_num[a["session_id"] or ""] = i

    def _tool_label(agent: dict) -> str:
        tool = agent["tool"]
        n = seq_num.get(agent["session_id"] or "", 0)
        # Friendly short name: capitalise first word of underscore/hyphen tool.
        friendly = tool.replace("_", " ").replace("-", " ").split()[0].capitalize()
        return f"{friendly} {n:02d}"

    # Group top-level agents by app (build_roster already sorts roots by app
    # then session_id, so insertion order gives stable grouping).
    groups: dict[str, list[dict]] = {}
    for agent in all_agents:
        groups.setdefault(agent["app"], []).append(agent)

    def emit_named(agent: dict, *, depth: int, is_lead: bool) -> None:
        label = _tool_label(agent)
        lead_mark = " ★" if is_lead else "  "  # ★ or two spaces
        model = agent["model"]
        toolmodel = (f"{agent['tool']}/{model}"
                     if model and model != "unknown" else agent["tool"])
        task = agent["task"] or agent["phase"] or "-"
        sid = agent["session_id"] or "?"
        age = f"seen {_fmt_age(agent['age_secs'])} ago"
        sub = _fmt_spawned(agent)

        indent = "  " + "  " * depth + ("└─ " if depth else "")
        lines.append(
            f"{indent}{label}{lead_mark}  {sid}  "
            f"· {toolmodel} · {task} · {age} · subagents {sub}"
        )
        for child in agent.get("children") or []:
            emit_named(child, depth=depth + 1, is_lead=False)

    apps_root_path = channel_paths.apps_root()

    for app_name, root_agents in groups.items():
        lines.append(f"\n[{app_name}]")

        # Resolve the designated lead session_id for this app (authoritative).
        # Falls back to None when lead.json is absent or the lease has expired.
        designated_lead_sid: str | None = None
        try:
            channel_dir = apps_root_path / app_name
            if is_lease_valid(channel_dir):
                doc = read_lead(channel_dir)
                if doc and isinstance(doc.get("lead"), dict):
                    designated_lead_sid = doc["lead"].get("session_id") or None
        except Exception:  # noqa: BLE001 — never let roster rendering fail
            pass

        def _is_lead(agent: dict) -> bool:
            sid = agent.get("session_id") or ""
            if designated_lead_sid is not None:
                # Authoritative: exactly the designated session_id.
                return sid == designated_lead_sid
            # Orchestrator heuristic: agent has live children OR has spawned.
            return bool(agent.get("children")) or agent.get("spawned_total", 0) > 0

        for agent in root_agents:
            emit_named(agent, depth=0, is_lead=_is_lead(agent))

    return "\n".join(lines)


def _main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="roster",
        description="Rally Point roster — cross-channel live agent view.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "named", "json"],
        default="text",
        help="Output format: text (tree, default), named (grouped by app with "
             "per-tool sequence numbers), or json.",
    )
    parser.add_argument(
        "--app", default=None,
        help="Filter to one app/channel slug (default: all channels).",
    )
    parser.add_argument(
        "--stale-secs", type=int, default=DEFAULT_STALE_SECS,
        metavar="SECS",
        help=f"Liveness window in seconds (default {DEFAULT_STALE_SECS}).",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Include stale sessions (default: live only).",
    )
    args = parser.parse_args()

    data = build_roster(
        app=args.app,
        stale_secs=args.stale_secs,
        include_stale=args.all,
    )

    fmt = args.format
    if fmt == "json":
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
    elif fmt == "named":
        sys.stdout.write(render_named(data) + "\n")
    else:
        sys.stdout.write(render_text(data) + "\n")


if __name__ == "__main__":
    _main()
