#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Poll coordination status and print only state transitions."""
from __future__ import annotations

import argparse
import errno
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = _HERE.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import coordination_status  # noqa: E402
import agent_rally  # noqa: E402

# Backstop lifetime: even if PID-based liveness ever fails (race condition,
# initial_ppid<=1 carve-out, OS oddity), the watcher self-exits after this
# many seconds. Default 4h, overridable via env so ops can tune without a
# redeploy. The race this guards against is documented in
# build-loop-memory/lessons/2026-05-31-coordination-process-leak.md.
_DEFAULT_MAX_LIFETIME_SECONDS = 14400.0
_ENV_MAX_LIFETIME = "BUILD_LOOP_WATCHER_MAX_LIFETIME_SECONDS"


def _env_max_lifetime() -> float:
    """Read env-var default for max-lifetime; fall back on parse error."""
    raw = os.environ.get(_ENV_MAX_LIFETIME)
    if raw is None:
        return _DEFAULT_MAX_LIFETIME_SECONDS
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_LIFETIME_SECONDS


def _is_parent_alive(parent_pid: int) -> bool:
    """True iff the explicit parent PID is still running.

    Used when ``--parent-pid`` is supplied (the launcher captured its own pid
    BEFORE detaching, closing the race where ``os.getppid()`` would already
    read 1 because the launcher exited during child Python startup). A
    cross-uid parent appears as ``EPERM`` from ``os.kill(pid, 0)`` — that is
    'alive but not ours', so treat it as alive (we cannot signal it but it
    exists). Any ``ProcessLookupError`` means the parent is gone.
    """
    if parent_pid <= 1:
        return True  # never trip on detached/launchd-owned watchers
    try:
        os.kill(parent_pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another uid
    except OSError as exc:
        # ESRCH = no such process (parent died)
        return exc.errno != errno.ESRCH


def _signature(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": status.get("status"),
        "required_action": status.get("required_action"),
        "revision": status.get("revision"),
        "peers": [
            (p.get("session_id"), p.get("phase"))
            for p in status.get("active_peers", [])
        ],
        "overlaps": [
            (o.get("peer"), tuple(o.get("files", [])), o.get("severity"))
            for o in status.get("overlaps", [])
        ],
        "unresolved": [
            (u.get("step"), u.get("verdict"))
            for u in status.get("unresolved", [])
        ],
        "dirty_outside_owned": status.get("dirty_outside_owned", []),
        "direct_inbox_unread_count": status.get("direct_inbox_unread_count", 0),
        "broadcast_inbox_unread_count": status.get("broadcast_inbox_unread_count", 0),
        "inbox_unread_count": status.get("inbox_unread_count", 0),
        "inbox_latest_messages": [
            (
                msg.get("source"),
                msg.get("id"),
                msg.get("requires_ack"),
                msg.get("preview"),
            )
            for msg in status.get("inbox_latest_messages", [])
        ],
        "task_heartbeat": {
            "health": (status.get("task_heartbeat") or {}).get("health"),
            "missed_count": (status.get("task_heartbeat") or {}).get("missed_count"),
            "expected_ref": (status.get("task_heartbeat") or {}).get("expected_ref"),
            "latest_id": ((status.get("task_heartbeat") or {}).get("latest") or {}).get("id"),
        },
    }


def _change_revisions(status: dict[str, Any]) -> list[int]:
    return [int(c.get("revision", 0)) for c in status.get("new_changes", [])]


def _is_orphaned(initial_ppid: int, current_ppid: int) -> bool:
    """True when the watcher's owning session has exited.

    A per-session coordination watcher whose parent is gone is garbage: on
    macOS/Linux an orphan is reparented (to launchd/init, pid 1), so a changed
    parent pid means the owner died. Such watchers must self-exit so they cannot
    accumulate across sessions and churn git (build-loop-memory
    lessons/2026-05-31-coordination-process-leak.md: ~112 leaked, blocking
    git via index.lock). This only stops a dead-owner monitor — it never
    refuses a write or coordination signal (rally never-block charter).

    A watcher launched detached (initial ppid already <= 1, e.g. a launchd
    service) has no owning session to outlive, so it never trips.
    """
    if initial_ppid <= 1:
        return False
    return current_ppid != initial_ppid


def _wake_due_event(args: argparse.Namespace) -> dict[str, Any] | None:
    """Return a watcher event when Rally has a due standby for this tool."""
    envelope = agent_rally.build_wake_due_envelope(args.workdir, args.tool)
    due = (((envelope.get("data") or {}).get("wake-due") or {}).get("due") or [])
    if not due:
        return None
    return {
        "event": "rally_wake_due",
        "tool": args.tool,
        "due": due,
        "suggested_commands": [
            item.get("suggested_command")
            for item in due
            if item.get("suggested_command")
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interval", type=float, default=3.0)
    p.add_argument("--iterations", type=int, default=0, help="0 = forever")
    p.add_argument("--jsonl", action="store_true")
    p.add_argument("--workdir", default=".")
    p.add_argument("--session-id", required=True)
    p.add_argument("--tool", default="claude_code")
    p.add_argument(
        "--files-in-flight",
        default=None,
        help="Comma-separated files this watcher session is currently touching.",
    )
    p.add_argument("--owned-file", action="append", default=[])
    p.add_argument("--owned-files", default=None)
    p.add_argument("--owned-files-csv", default=None)
    p.add_argument("--coordination-file", default=None)
    p.add_argument("--since-revision", type=int, default=None)
    p.add_argument("--max-changes", type=int, default=20)
    p.add_argument(
        "--task-ref",
        default=None,
        help="Expected active task/claim/run ref for task-heartbeat health.",
    )
    p.add_argument(
        "--task-heartbeat-grace-seconds",
        type=int,
        default=coordination_status.task_heartbeat.DEFAULT_GRACE_SECONDS,
        help="Grace window after next_check_in_at before a heartbeat is stale.",
    )
    p.add_argument(
        "--baseline-current",
        action="store_true",
        help="Treat the current state as already seen and emit only future changes.",
    )
    p.add_argument(
        "--exit-on-change",
        action="store_true",
        help="Exit 0 after emitting a changed state; useful for wake-on-change wrappers.",
    )
    p.add_argument(
        "--exit-on-wake-due",
        action="store_true",
        help="Exit 0 after emitting a due Rally standby; host-portable wake tier.",
    )
    p.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help=(
            "Explicit PID of the spawning session to liveness-check each loop. "
            "Closes the race where os.getppid() reads 1 because the hook "
            "exited before this child's main started. The launcher captures "
            "its own pid via os.getpid() BEFORE Popen and passes it here. "
            "When omitted, the legacy _is_orphaned(getppid()) check is used "
            "(rally watch / build-orchestrator --interval N path)."
        ),
    )
    p.add_argument(
        "--max-lifetime-seconds",
        type=float,
        default=None,
        help=(
            "Absolute lifetime backstop. The watcher self-exits after this "
            "many seconds even if every other liveness check says alive. "
            "Defaults to the BUILD_LOOP_WATCHER_MAX_LIFETIME_SECONDS env var "
            f"or {_DEFAULT_MAX_LIFETIME_SECONDS:g}s (4h)."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    initial_ppid = os.getppid()
    parent_pid = args.parent_pid
    max_lifetime = (
        args.max_lifetime_seconds
        if args.max_lifetime_seconds is not None
        else _env_max_lifetime()
    )
    start_monotonic = time.monotonic()
    last_sig = None
    if args.baseline_current:
        last_sig = _signature(coordination_status.build_status(args))
    count = 0
    while True:
        # Liveness checks (cheapest-first), each strong enough to stand alone.
        # PRIMARY: explicit launcher PID (closes the hook-exit race).
        if parent_pid is not None and not _is_parent_alive(parent_pid):
            return 0
        # BACKSTOP: absolute lifetime cap, OS-time-independent (monotonic).
        if time.monotonic() - start_monotonic >= max_lifetime:
            return 0
        # LEGACY GUARD: defense-in-depth, preserved for callers that don't
        # pass --parent-pid (rally watch, --interval N orchestrator path).
        if _is_orphaned(initial_ppid, os.getppid()):
            return 0
        if args.exit_on_wake_due:
            wake_event = _wake_due_event(args)
            if wake_event:
                if args.jsonl:
                    print(json.dumps(wake_event, separators=(",", ":")), flush=True)
                else:
                    print(json.dumps(wake_event, indent=2, sort_keys=True), flush=True)
                return 0
        status = coordination_status.build_status(args)
        sig = _signature(status)
        if sig != last_sig:
            event = {
                "event": "coordination_state_changed",
                "status": status["status"],
                "required_action": status["required_action"],
                "revision": status["revision"],
                "active_peers": status["active_peers"],
                "overlaps": status["overlaps"],
                "unresolved": status["unresolved"],
                "dirty_outside_owned": status["dirty_outside_owned"],
                "direct_inbox_unread_count": status.get(
                    "direct_inbox_unread_count", 0
                ),
                "broadcast_inbox_unread_count": status.get(
                    "broadcast_inbox_unread_count", 0
                ),
                "inbox_unread_count": status.get("inbox_unread_count", 0),
                "inbox_latest_messages": status.get("inbox_latest_messages", []),
                "task_heartbeat": status.get("task_heartbeat", {}),
                "new_change_revisions": _change_revisions(status),
            }
            if args.jsonl:
                print(json.dumps(event, separators=(",", ":")), flush=True)
            else:
                print(json.dumps(event, indent=2, sort_keys=True), flush=True)
            last_sig = sig
            if args.exit_on_change:
                return 0
        count += 1
        if args.iterations and count >= args.iterations:
            return 0
        time.sleep(max(args.interval, 0.1))


if __name__ == "__main__":
    raise SystemExit(main())
