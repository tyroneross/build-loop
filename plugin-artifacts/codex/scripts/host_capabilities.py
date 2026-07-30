#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Host capability resolver — the single source of truth for what wakeup/resume
primitives the current coding host can actually use.

Build-loop runs under many hosts (Claude Code, Codex, Gemini, opencode, Cursor, …).
Self-resume must NOT assume a Claude-only feature: `ScheduleWakeup` is one adapter
among several. This module answers, per host, which tiers exist so `wake_scheduler`
can pick the best AVAILABLE one and degrade gracefully everywhere.

Tiers (capability flags):
- auto_reinvoke   : harness re-invokes the agent when tracked sub-work finishes (event-driven, free)
- schedule_wakeup : harness-native timed self-wake within a live session/daemon (Claude Code only)
- cron            : cloud routine that survives full shutdown (Claude Code CronCreate)
- os_scheduler    : launchd/systemd timed re-run (any host on a host OS)
- poll            : long-running adaptive watcher (any host; the universal fallback)

stdout = one JSON envelope; exit 0 always. Pure / stdlib-only.

Usage:
    python3 scripts/host_capabilities.py [--tool <id>] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Capability matrix. poll + os_scheduler are universal (any host on a real OS).
# auto_reinvoke / schedule_wakeup / cron are harness-native and host-specific.
_CAPS: dict[str, dict[str, bool]] = {
    "claude_code": {"auto_reinvoke": True,  "schedule_wakeup": True,  "cron": True,  "os_scheduler": True, "poll": True},
    "codex":       {"auto_reinvoke": False, "schedule_wakeup": False, "cron": False, "os_scheduler": True, "poll": True},
    "gemini":      {"auto_reinvoke": False, "schedule_wakeup": False, "cron": False, "os_scheduler": True, "poll": True},
    "opencode":    {"auto_reinvoke": False, "schedule_wakeup": False, "cron": False, "os_scheduler": True, "poll": True},
    "cursor":      {"auto_reinvoke": False, "schedule_wakeup": False, "cron": False, "os_scheduler": True, "poll": True},
    # Safe universal fallback: assume only the cross-host primitives exist.
    "unknown":     {"auto_reinvoke": False, "schedule_wakeup": False, "cron": False, "os_scheduler": True, "poll": True},
}

KNOWN_HOSTS = tuple(h for h in _CAPS if h != "unknown")

# Preference order, best → worst, for an UNATTENDED long wait that need not
# survive a full shutdown. wake_scheduler narrows this by wait_kind.
_PREFERENCE = ("auto_reinvoke", "schedule_wakeup", "cron", "os_scheduler", "poll")


def normalize_tool(tool_id: str | None) -> str | None:
    """`claude_code:audit` / `codex-01` → base host id."""
    if not tool_id:
        return None
    base = tool_id.split(":", 1)[0]
    base = base.split("-", 1)[0] if base not in _CAPS else base
    base = base.strip().lower().replace(" ", "_")
    # common aliases
    alias = {"claude": "claude_code", "claudecode": "claude_code", "cc": "claude_code", "gpt": "codex"}
    return alias.get(base, base)


def detect_host(tool_id: str | None = None, env: dict[str, str] | None = None) -> str:
    """Resolve the current host. Order: explicit tool_id → env markers → unknown.
    Caller-supplied tool_id is authoritative (rally already threads it everywhere)."""
    env = os.environ if env is None else env
    norm = normalize_tool(tool_id)
    if norm in _CAPS:
        return norm
    # Env heuristics — only used when no explicit tool id resolves.
    if any(k.startswith("CODEX") for k in env):
        return "codex"
    if env.get("CLAUDE_CODE") or env.get("CLAUDECODE") or "CLAUDE_PLUGIN_ROOT" in env:
        return "claude_code"
    if any(k.startswith("GEMINI") for k in env):
        return "gemini"
    return "unknown"


def capabilities(host_id: str) -> dict[str, bool]:
    return dict(_CAPS.get(host_id, _CAPS["unknown"]))


def wake_tiers(host_id: str) -> list[str]:
    """Available tiers for this host, best → worst (general preference)."""
    caps = capabilities(host_id)
    return [t for t in _PREFERENCE if caps.get(t)]


def resolve(tool_id: str | None = None, env: dict[str, str] | None = None) -> dict:
    host = detect_host(tool_id, env)
    return {
        "host": host,
        "known": host in KNOWN_HOSTS,
        "capabilities": capabilities(host),
        "wake_tiers": wake_tiers(host),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Resolve host wakeup/resume capabilities.")
    p.add_argument("--tool", default=None, help="Coding-host tool id (e.g. claude_code, codex)")
    p.add_argument("--json", action="store_true", help="Emit JSON")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    out = resolve(args.tool)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"host={out['host']} (known={out['known']})")
        print(f"tiers={out['wake_tiers']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
