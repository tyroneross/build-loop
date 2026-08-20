#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""PreToolUse:Bash hook — route privileged commands through the broker.

Without this hook, ``privileged_broker.py`` is a library nobody calls: an agent
that types ``sfltool dumpbtm`` still gets an anonymous dialog and still retries
into a second one. This hook is the point where the policy binds.

BEHAVIOUR
  no privileged segment      → ``{}`` (silent pass-through; the common case)
  already brokered           → allow
  privileged, broker present → DENY with a redirect: the reason names the exact
                               brokered command to re-issue, with a purpose slot
  privileged, broker missing → ALLOW + coverage-gap receipt. A hook that cannot
                               coordinate must not block the work; it must say
                               out loud that this invocation went unattributed.

Deny here is a REDIRECT, not a refusal: the same work runs, one line different,
with a named request and a shared authorization. CLAUDE.md permits a blocking
hook for an explicit safety gate, and privilege escalation is one. The registry
match is exact (argv, not substring), so a false positive is a near-impossibility
rather than a tolerated cost.

Kill switch: ``BUILD_LOOP_HOOKS=off`` or ``BUILD_LOOP_PRIVILEGED_HOOK=off``.
Exit code is always 0 — under the Claude Code hook contract a non-zero exit means
"the hook broke", not "deny".
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or HERE.parent.parent)
BROKER = PLUGIN_ROOT / "scripts" / "privileged_broker.py"

# A command that is ALREADY going through the broker must not be redirected again.
BROKER_MARKERS = ("privileged_broker.py", "privileged_broker request")


def emit(payload: dict | None = None) -> None:
    print(json.dumps(payload if payload is not None else {}))
    sys.exit(0)


def decide(decision: str, reason: str) -> None:
    emit({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    })


def redirect_command(segment: dict, cwd: str) -> str:
    """The brokered form of one privileged segment, ready to paste."""
    parts = [
        "python3", str(BROKER), "request",
        "--purpose", "<one plain sentence: why this is needed>",
        "--task-id", "${BUILD_LOOP_TASK_ID:-$$}",
        "--repo", cwd or "$PWD",
        "--initiating-app", "${BUILD_LOOP_INITIATING_APP:-Claude Code}",
        "--argv", *segment["argv"],
    ]
    return " ".join(shlex.quote(p) if " " in p else p for p in parts)


def main() -> None:
    if os.environ.get("BUILD_LOOP_HOOKS") == "off" or os.environ.get("BUILD_LOOP_PRIVILEGED_HOOK") == "off":
        emit()

    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        emit()

    command = (event.get("tool_input") or {}).get("command") or ""
    cwd = event.get("cwd") or ""
    if not command.strip():
        emit()

    if any(marker in command for marker in BROKER_MARKERS):
        decide("allow", "already routed through the privileged-command broker")

    if not BROKER.is_file():
        emit()  # nothing to route to; stay out of the way entirely

    # `classify` exits 0 when it finds privileged segments and 1 when it finds
    # none. Any other exit is a MALFUNCTION and must not be read as "clean" —
    # treating a failed check as a negative result is precisely the confusion
    # that turned one refused read into two password dialogs on 2026-08-20.
    try:
        proc = subprocess.run(
            [sys.executable, str(BROKER), "classify", "--command", command, "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode not in (0, 1):
            raise RuntimeError(f"classify exited {proc.returncode}")
        segments = json.loads(proc.stdout or "{}").get("privileged_segments", [])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # The classifier failed. Record the blind spot; never block on it.
        _gap("classifier_unavailable", command)
        decide("allow", "build-loop could not classify this command for privilege; "
                        "proceeding UNATTRIBUTED and a coverage-gap receipt was written")

    if not segments:
        emit()

    first = segments[0]
    risk = "MUTATING — changes host state" if first["mutating"] else "read-only"
    lines = [
        f"This command runs {len(segments)} privileged command(s); macOS will ask for an "
        "administrator password showing only the binary name.",
        "",
        f"  {' '.join(first['argv'])}",
        f"  scope {first['scope']} · {risk} · trust {first['trust_domain']}",
        "",
        "Re-issue it through the broker so the request is named, coalesced with any "
        "identical read-only request already in flight, and recorded:",
        "",
        f"  {redirect_command(first, cwd)}",
        "",
        "Replace <one plain sentence: why this is needed> with the real reason — the "
        "broker refuses a request with no purpose, and that sentence is what appears "
        "next to the password dialog.",
    ]
    if len(segments) > 1:
        lines.append("")
        lines.append("Other privileged segments in this command: "
                     + ", ".join(" ".join(s["argv"]) for s in segments[1:]))
    decide("deny", "\n".join(lines))


def _gap(reason: str, command: str) -> None:
    """Write a coverage-gap receipt without importing the broker (it may be broken)."""
    root = Path(os.environ.get("BUILD_LOOP_PRIVILEGED_ROOT") or (Path.home() / ".build-loop" / "privileged"))
    receipt = {
        "schema": "buildloop.privileged.coverage_gap/1",
        "reason": reason,
        "risk_class": "unknown",
        "behavior": "proceed_uncoalesced_unrecorded",
        "detail": command[:500],
        "unattributed_possible": True,
        "pid": os.getpid(),
        "source": "pre_bash_privileged hook",
    }
    try:
        root.mkdir(parents=True, exist_ok=True)
        with (root / "gaps.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - a hook must never break the tool call
        emit()
