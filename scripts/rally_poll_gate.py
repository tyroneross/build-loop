#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""rally_poll_gate.py — enforce poll-after-post for rally handoffs (all agents).

Rally is pull-only: posting a handoff does NOT notify you when the target
answers — you only learn by PULLING the room. A pull is one fetch; polling is
pulling on an interval until ack-or-timeout. Without this gate an agent posts a
handoff and walks away, and "awaiting ack" is indistinguishable from "the peer
is asleep and will never answer" (observed: a handoff to an idle codex sat
unread while the run moved on).

This makes the discipline executable + enforceable so EVERY agent (Claude,
Codex, …) does the same, instead of each one remembering:

  - `check`  — gate. Exit 3 if you have any UNRESOLVED handoff YOU authored.
               Wire into before-complete / Phase D Closeout so a run cannot
               cleanly finish while it owns an unanswered ask. Session-agnostic
               (works whether or not the target was ever an injectable session)
               — this is what `rally inject --require-ack` cannot cover.
  - `wait`   — poll the room every `--interval` s until your handoff(s) resolve
               or `--timeout` s elapses. On timeout: exit 4 so the caller falls
               to its declared fallback_plan instead of blocking forever.

Fail-open on the FETCH (a rally/CLI outage must not wedge a build — exit 0 with
a warning), fail-closed on the FINDING (an unresolved self-handoff is a real
gate hit — exit 3). `--room-json <path|->` injects room JSON for tests.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def mine_open(open_handoffs: list[dict[str, Any]], tool: str) -> list[dict[str, Any]]:
    """Pure: handoffs in the room's open list that THIS tool authored.

    A handoff fact carries `tool` (author) and `target` (recipient). Mine = the
    ones I posted, regardless of who they target.
    """
    out = []
    for h in open_handoffs or []:
        if isinstance(h, dict) and h.get("tool") == tool:
            out.append(h)
    return out


def _load_room_json(source: str) -> dict[str, Any]:
    text = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text()
    return json.loads(text)


def fetch_room(workdir: Path, room_json: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """Return (room_dict, error). Fail-open: on any fetch error return (None, msg)."""
    if room_json is not None:
        try:
            return _load_room_json(room_json), None
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"room-json unreadable: {exc}"
    try:
        proc = subprocess.run(
            ["rally", "room", "--json"],
            cwd=str(workdir), capture_output=True, text=True, timeout=20, check=False,
        )
        if proc.returncode != 0:
            return None, f"rally room exited {proc.returncode}: {proc.stderr.strip()[:200]}"
        return json.loads(proc.stdout), None
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        return None, f"rally room failed: {exc}"


def _open_handoffs(room: dict[str, Any]) -> list[dict[str, Any]]:
    oh = room.get("data", {}).get("room", {}).get("open_handoffs", [])
    return oh if isinstance(oh, list) else []


def _check(tool: str, workdir: Path, room_json: str | None) -> tuple[int, dict[str, Any]]:
    room, err = fetch_room(workdir, room_json)
    if err:  # fail-open on fetch — never wedge a build over a telemetry/coord outage
        return 0, {"ok": True, "warning": err, "mine_open": [], "gated": False}
    mine = mine_open(_open_handoffs(room), tool)
    if mine:
        return 3, {
            "ok": False,
            "gated": True,
            "mine_open": [{"event_id": h.get("event_id"), "target": h.get("target"),
                           "subject": h.get("subject")} for h in mine],
            "advice": "PULL the room and resolve these before completing: "
                      "rally recent --json | rally room --json; if the target is idle, "
                      "fall to your declared fallback_plan.",
        }
    return 0, {"ok": True, "gated": False, "mine_open": []}


def _wait(tool: str, workdir: Path, event_id: str | None, timeout: float,
          interval: float, room_json: str | None) -> tuple[int, dict[str, Any]]:
    """Poll until my open handoffs (or a specific event_id) resolve, or timeout."""
    deadline = time.monotonic() + timeout
    polls = 0
    while True:
        room, err = fetch_room(workdir, room_json)
        polls += 1
        if not err:
            mine = mine_open(_open_handoffs(room), tool)
            if event_id is not None:
                mine = [h for h in mine if h.get("event_id") == event_id]
            if not mine:
                return 0, {"ok": True, "resolved": True, "polls": polls}
        if time.monotonic() >= deadline or room_json is not None:
            # room_json is a static test fixture — never loop on it.
            return 4, {"ok": False, "resolved": False, "polls": polls,
                       "reason": "timeout", "advice": "fall to declared fallback_plan"}
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="Gate: exit 3 if you have unresolved self-authored handoffs.")
    c.add_argument("--tool", required=True)
    c.add_argument("--workdir", default=".")
    c.add_argument("--room-json", default=None, help="Inject room JSON (path or '-') for tests.")

    w = sub.add_parser("wait", help="Poll until your handoff(s) resolve or timeout (exit 4).")
    w.add_argument("--tool", required=True)
    w.add_argument("--workdir", default=".")
    w.add_argument("--event-id", default=None, help="Wait on one handoff; default: all mine.")
    w.add_argument("--timeout", type=float, default=300.0)
    w.add_argument("--interval", type=float, default=30.0)
    w.add_argument("--room-json", default=None, help="Inject room JSON (path or '-') for tests.")

    args = p.parse_args(argv)
    workdir = Path(args.workdir).expanduser().resolve()

    if args.cmd == "check":
        code, env = _check(args.tool, workdir, args.room_json)
    else:
        code, env = _wait(args.tool, workdir, args.event_id, args.timeout, args.interval, args.room_json)
    print(json.dumps(env, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
