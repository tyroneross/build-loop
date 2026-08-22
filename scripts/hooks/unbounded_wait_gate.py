#!/usr/bin/env python3
"""Reject a wait that has no condition.

WHY THIS EXISTS
---------------
2026-07-27, atomize-ai security run: a dispatched orchestrator needed to wait on
reviewer subagents, and wrote a `while true; do sleep 30; done` placeholder as
the wait. Nothing in that loop could ever end it. It ran ~100 minutes of wall
clock, was killed on timeout, and fired a SECOND spurious completion
notification for a run that had already finished — so the parent had to
re-verify a finished build to establish that nothing had changed.

The defect is not "sleep" and not "loop". It is **waiting on a duration instead
of a condition**. A wait whose only exit is the harness timeout is never the
right shape: the harness already re-invokes an agent when tracked work finishes,
so the loop is pure waste even when it happens to be short.

The rejection also names the resume primitive THIS host actually has, resolved
through ``scripts/wake_scheduler.py`` (Claude Code gets ``ScheduleWakeup``; Codex,
Cursor, Gemini and unknown hosts get the poll watcher). Before 2026-08-22 the gate
offered one host-agnostic snippet, so the only self-resume advice build-loop ever
gave assumed a Claude-only harness feature.

build-loop already ships the correct idiom (agents/architecture-scout.md:180):

    for i in $(seq 1 30); do
      pgrep -f "<the thing being waited on>" >/dev/null || break
      sleep 1
    done

Bounded iterations, and a condition that can break out early. This gate blocks
the unbounded shapes and names that idiom in the rejection.

CONTRACT
--------
stdin  : Claude Code PreToolUse hook JSON.
stdout : JSON (always `{}` — this gate speaks through stderr + exit code).
exit 0 : nothing to say.
exit 2 : blocked; stderr explains and shows the bounded rewrite.

Fails OPEN on any parse problem. A gate that cannot read the command must never
manufacture a block.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# A loop header that never terminates on its own.
_INFINITE_HEADER = re.compile(
    r"""(?:^|[;&|]|\bdo\b|\bthen\b|\s)\s*
        (?: while \s+ true \b
          | while \s+ :                 # `:` is not a word char — no \b here
          | until \s+ false \b
          | for \s* \(\( \s* ;\s* ;\s* \)\)
        )""",
    re.VERBOSE | re.IGNORECASE,
)

# Something that can end the loop from the inside.
_ESCAPE = re.compile(r"\b(?:break|return|exit)\b|\bpkill\b", re.IGNORECASE)

_SLEEP = re.compile(r"\bsleep\s+([0-9]+(?:\.[0-9]+)?)\b", re.IGNORECASE)

# A bare sleep this long is a duration-wait, not a pause. Chosen so the common
# short settle (`sleep 2` after starting a dev server) stays untouched.
_LONG_BARE_SLEEP_SECONDS = 120.0

_BOUNDED_IDIOM = """  for i in $(seq 1 30); do
    <condition that becomes false when the work is done> || break
    sleep 1
  done"""


def _host_resume_hint(env: dict[str, str] | None = None) -> str | None:
    """Name the resume primitive this host actually has, or None.

    Delegates to ``scripts/wake_scheduler.py`` so the host matrix lives in exactly
    one place. Called with no ``next_action``, ``plan_wake`` is pure stdlib — no
    subprocess, no file IO — which is what lets a PreToolUse gate afford it.

    Fails open (returns None) on any error: a gate that cannot resolve the host
    still has a correct host-agnostic answer to give, and must never hard-fail.
    """
    try:
        import importlib.util

        scripts_dir = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            "wake_scheduler", scripts_dir / "wake_scheduler.py"
        )
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        saved = os.environ.copy()
        try:
            if env is not None:
                os.environ.clear()
                os.environ.update(env)
            plan = mod.plan_wake(None, mod.EXTERNAL, desired_seconds=1200)
        finally:
            if env is not None:
                os.environ.clear()
                os.environ.update(saved)

        directive = plan.get("directive")
        host = plan.get("host")
        if not directive or not host:
            return None
        return f"On this host ({host}) the resume primitive is:\n\n  {directive}"
    except Exception:
        return None


def _command(payload: dict) -> str:
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if tool and tool != "Bash":
        return ""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command")
    return command if isinstance(command, str) else ""


def evaluate(command: str, env: dict[str, str] | None = None) -> str | None:
    """Return a rejection message, or None when the command is fine.

    ``env`` overrides host detection for the resume hint; production callers omit
    it and the real environment decides.
    """
    if not command or "sleep" not in command.lower():
        return None

    message = None
    if _INFINITE_HEADER.search(command) and not _ESCAPE.search(command):
        message = (
            "This loop waits on a duration, not a condition — nothing in it can "
            "end it, so it runs until the harness kills it.\n\n"
            "If you are waiting on work the harness tracks (a subagent, a "
            "background Bash job), do not poll at all: you are re-invoked when "
            "it finishes.\n\n"
            "If you must poll something the harness cannot see, bound it and "
            "give it a real exit condition:\n\n" + _BOUNDED_IDIOM
        )
    else:
        longest = max((float(m) for m in _SLEEP.findall(command)), default=0.0)
        if longest >= _LONG_BARE_SLEEP_SECONDS:
            message = (
                f"`sleep {longest:g}` is a duration-wait. A sleep that long is "
                "standing in for a condition you have not written.\n\n"
                "Wait on the condition instead:\n\n" + _BOUNDED_IDIOM
            )

    if message is None:
        return None

    hint = _host_resume_hint(env)
    if hint:
        message += "\n\nTo resume later instead of waiting now, do not hand-roll a\n"
        message += "host-specific call. " + hint
    return message


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0  # fail open

    try:
        command = _command(payload if isinstance(payload, dict) else {})
        message = evaluate(command)
    except Exception:
        print("{}")
        return 0  # fail open

    print("{}")
    if message:
        sys.stderr.write("[build-loop] unbounded wait rejected.\n\n" + message + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
