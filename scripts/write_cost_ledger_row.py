#!/usr/bin/env python3
"""Append a single row to ~/.bookmark/cost-ledger.jsonl.

Called by the orchestrator at each implementer (or other subagent) dispatch
boundary to record measured wall-clock, status, and per-dispatch task_id.
The ledger already exists and is shared with ollama-mcp etc. — we match its
row shape (`ts`, `source`, `model`, `task_id`, `latency_ms`, `est_cost_usd`)
and add build-loop-specific fields alongside.

Contract:
  stdout      -> nothing on success
  stderr      -> human-readable log lines (errors only)
  exit 0      -> row appended
  exit 1      -> validation error (bad args, missing required field, wrong type)
  exit 2      -> filesystem error (lock timeout, disk full, permission denied)

Atomicity: fcntl.flock(LOCK_EX) on a sidecar .lock file alongside the JSONL.
Append-only — never rewrites existing rows.

Zero dependencies. Python 3.11+.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOCK_TIMEOUT_S = 10
DEFAULT_LEDGER_PATH = Path.home() / ".bookmark" / "cost-ledger.jsonl"

VALID_DISPATCH_MODES = {"fan-out", "inline", "self-recursive"}
VALID_STATUSES = {
    "fixed", "completed", "partial", "blocked", "scope_breach",
    "deferred_architecture", "plan_malformed", "evidence_stale",
    "needs_dependency", "failed", "concurrent_modification_detected",
    "dispatched",  # row written at dispatch time, before return
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def iso_utc(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_row(args: argparse.Namespace) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ts": iso_utc(),
        "source": "build-loop",
        "agent": args.agent,
        "task_id": args.task_id,
        "model": args.model,
        "status": args.status,
        "dispatch_mode": args.dispatch_mode,
        "files_changed_count": args.files_changed_count,
        "tokens_estimate": args.tokens_estimate,
        "tokens_source": args.tokens_source,
        "est_cost_usd": None,
    }
    if args.wall_clock_seconds is not None:
        row["wall_clock_seconds"] = args.wall_clock_seconds
        row["latency_ms"] = int(args.wall_clock_seconds * 1000)
    if args.started_at:
        row["started_at"] = args.started_at
    if args.completed_at:
        row["completed_at"] = args.completed_at
    if args.run_id:
        row["run_id"] = args.run_id
    if args.chunk_id:
        row["chunk_id"] = args.chunk_id
    return row


def append_row(ledger_path: Path, row: dict[str, Any]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
    line = json.dumps(row, separators=(",", ":")) + "\n"
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    with open(lock_path, "a+") as lock_fh:
        while True:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"lock timeout after {LOCK_TIMEOUT_S}s on {lock_path}"
                    )
                time.sleep(0.05)
        try:
            with open(ledger_path, "a", encoding="utf-8") as ledger_fh:
                ledger_fh.write(line)
                ledger_fh.flush()
                os.fsync(ledger_fh.fileno())
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--agent", required=True, help="Subagent type (e.g. 'implementer', 'scope-auditor')")
    p.add_argument("--task-id", required=True, help="Unique per-dispatch task identifier")
    p.add_argument("--model", required=True, help="Resolved model identifier (e.g. claude-sonnet-4-6)")
    p.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    p.add_argument("--dispatch-mode", required=True, choices=sorted(VALID_DISPATCH_MODES))
    p.add_argument("--files-changed-count", type=int, default=None)
    p.add_argument("--tokens-estimate", type=int, default=None)
    p.add_argument("--tokens-source", default="envelope", choices=["envelope", "usage", "unknown"])
    p.add_argument("--wall-clock-seconds", type=float, default=None)
    p.add_argument("--started-at", default=None, help="ISO8601 dispatch timestamp")
    p.add_argument("--completed-at", default=None, help="ISO8601 return timestamp")
    p.add_argument("--run-id", default=None, help="state.json.execution.run_id")
    p.add_argument("--chunk-id", default=None, help="Plan chunk identifier")
    p.add_argument(
        "--ledger-path",
        default=str(DEFAULT_LEDGER_PATH),
        help="Override default ledger location (testing/CI use only)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1

    try:
        row = build_row(args)
    except Exception as exc:
        log(f"build_row failed: {exc}")
        return 1

    try:
        append_row(Path(args.ledger_path), row)
    except TimeoutError as exc:
        log(f"filesystem error: {exc}")
        return 2
    except OSError as exc:
        log(f"filesystem error: {exc}")
        return 2
    except Exception as exc:
        log(f"unexpected error: {exc}")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
