#!/usr/bin/env python3
"""Poll coordination status and print only state transitions.

Use during active coding when a cheap 2-5 second sensor loop is useful. The
watcher does not interpret or resolve coordination state; it emits compact JSONL
events that an agent can decide to read only when status changes to warn/blocked.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import coordination_status  # noqa: E402


def _signature(status: dict) -> dict:
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
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interval", type=float, default=3.0)
    p.add_argument("--iterations", type=int, default=0, help="0 = forever")
    p.add_argument("--jsonl", action="store_true")
    # Reuse coordination_status args.
    p.add_argument("--workdir", default=".")
    p.add_argument("--session-id", required=True)
    p.add_argument("--owned-file", action="append", default=[])
    p.add_argument("--owned-files", default=None)
    p.add_argument("--owned-files-csv", default=None)
    p.add_argument("--coordination-file", default=None)
    p.add_argument("--since-revision", type=int, default=None)
    p.add_argument("--max-changes", type=int, default=20)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    last_sig = None
    count = 0
    while True:
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
            }
            if args.jsonl:
                print(json.dumps(event, separators=(",", ":")), flush=True)
            else:
                print(json.dumps(event, indent=2, sort_keys=True), flush=True)
            last_sig = sig
        count += 1
        if args.iterations and count >= args.iterations:
            return 0
        time.sleep(max(args.interval, 0.1))


if __name__ == "__main__":
    raise SystemExit(main())
