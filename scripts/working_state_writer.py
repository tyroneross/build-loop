#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""working_state_writer.py — per-step execution observability for build-loop.

Per plan §15.2: agents write super-concise progress state during execution so
the user (and downstream agents) can see "where is the build right now?" at any
moment. Two complementary files at `.build-loop/working-state/`:

  current.json   — overwritten on every write; latest snapshot only
  log.jsonl      — append-only history (terse keys to keep size bounded)

Trigger events (per `agents/implementer.md` write protocol):
  - new file opened for Edit/Write
  - task transition (current_task_id → next_task_id)
  - blocked_external (waiting on CI, network, user)
  - chunk boundary (orchestrator writes at dispatch_chunk / return_chunk)
  - budget check-in (budget_check.py)

NOT every line edit — too noisy. Granularity is "new file" or "new task".

Atomic write via tmpfile + os.replace. Stdlib only. Sub-2ms typical.

Exit codes:
  0 — write succeeded (or current.json wrote and log append failed gracefully)
  1 — invalid --status value OR current.json write failed (callers may ignore;
      writes are informational and the build never depends on them)

The writer is fire-and-forget for callers — agents should run it but proceed
regardless of exit code. The non-zero return exists for visibility only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
VALID_STATUS = {
    # implementer-side
    "editing", "reading", "planning", "testing",
    "committing", "blocked_external", "idle",
    # orchestrator-side (per references/m-series-protocol.md §M2 sidecar)
    "dispatching", "awaiting_return", "phase_transition", "completed",
}
DEFAULT_JSONL_MAX_MB = 10


def iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".tmp.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def append_jsonl(target: Path, row: dict) -> None:
    """Append a single JSON row. No locking — implementers are single-writer
    per chunk; orchestrator interleaves only at chunk boundaries which never
    collide in time."""
    target.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    with open(target, "ab") as f:
        f.write(line)


def truncate_jsonl_if_needed(target: Path, max_mb: int = DEFAULT_JSONL_MAX_MB) -> None:
    """Rolling truncate — when log.jsonl exceeds max_mb, keep only the most
    recent 50% by line count. Idempotent. Cheap (only runs the count when
    size exceeded)."""
    if not target.exists():
        return
    if target.stat().st_size <= max_mb * 1024 * 1024:
        return
    try:
        with open(target, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
    except OSError:
        return
    keep_from = len(all_lines) // 2
    kept = all_lines[keep_from:]
    atomic_write_bytes(target, ("".join(kept)).encode("utf-8"))


def build_state(args: argparse.Namespace) -> dict[str, Any]:
    """Construct the full current.json snapshot from CLI args."""
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "agent": args.agent,
        "updated_at": iso_utc(),
    }
    # Optional fields — omit when empty so the snapshot stays terse.
    optional = {
        "run_id": args.run_id,
        "chunk_id": args.chunk_id,
        "current_task_id": args.current_task_id,
        "current_task_summary": args.current_task_summary,
        "current_file": args.current_file,
        "current_file_line_range": args.current_file_line_range,
        "next_task_id": args.next_task_id,
        "next_task_summary": args.next_task_summary,
        "status": args.status,
        "elapsed_in_chunk_s": args.elapsed_in_chunk_s,
        "blocked_reason": args.blocked_reason,
    }
    for k, v in optional.items():
        if v is not None and v != "":
            state[k] = v
    return state


def build_log_row(state: dict[str, Any]) -> dict[str, Any]:
    """Compact one-line representation for log.jsonl. Single-letter keys
    where the meaning is obvious; full keys for less-common fields."""
    row: dict[str, Any] = {
        "t": state["updated_at"],
        "agent": state["agent"],
    }
    # Map full -> terse keys for size economy
    short_map = {
        "current_task_id": "task",
        "current_file": "file",
        "next_task_id": "next",
        "status": "status",
        "chunk_id": "chunk",
        "blocked_reason": "blocked",
    }
    for full, short in short_map.items():
        if full in state:
            row[short] = state[full]
    return row


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Per-step working-state writer for build-loop (plan §15.2).")
    p.add_argument("--workdir", required=True, help="Project root containing .build-loop/")
    p.add_argument("--agent", required=True, help="Writing agent id, e.g. implementer:c1")
    p.add_argument("--run-id", default=None, help="Active run_id")
    p.add_argument("--chunk-id", default=None, help="Active chunk id, e.g. c1")
    p.add_argument("--current-task-id", default=None, help="Plan task ID, e.g. T-3")
    p.add_argument("--current-task-summary", default=None, help="Short summary of current task (≤ 200 chars)")
    p.add_argument("--current-file", default=None, help="Path of file currently being worked")
    p.add_argument("--current-file-line-range", default=None, help="Line range like '42-80'")
    p.add_argument("--next-task-id", default=None, help="Next plan task ID")
    p.add_argument("--next-task-summary", default=None, help="Short summary of next task")
    p.add_argument("--status", default=None, choices=sorted(VALID_STATUS) + [None], help="Current status")
    p.add_argument("--elapsed-in-chunk-s", type=int, default=None, help="Seconds since chunk dispatch")
    p.add_argument("--blocked-reason", default=None, help="When status=blocked_external")
    p.add_argument("--no-log", action="store_true", help="Skip log.jsonl append (current.json only)")
    p.add_argument("--max-jsonl-mb", type=int, default=DEFAULT_JSONL_MAX_MB, help="log.jsonl rolling cap")
    args = p.parse_args(argv)

    if args.status and args.status not in VALID_STATUS:
        print(f"working_state_writer: invalid --status {args.status!r}", file=sys.stderr)
        return 1
    if args.current_task_summary and len(args.current_task_summary) > 200:
        args.current_task_summary = args.current_task_summary[:197] + "..."
    if args.next_task_summary and len(args.next_task_summary) > 200:
        args.next_task_summary = args.next_task_summary[:197] + "..."

    workdir = Path(args.workdir).resolve()
    ws_dir = workdir / ".build-loop" / "working-state"
    current_path = ws_dir / "current.json"
    log_path = ws_dir / "log.jsonl"

    state = build_state(args)
    try:
        atomic_write_bytes(current_path, (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    except OSError as e:
        print(f"working_state_writer: write failed: {e}", file=sys.stderr)
        return 1

    if not args.no_log:
        row = build_log_row(state)
        try:
            append_jsonl(log_path, row)
            truncate_jsonl_if_needed(log_path, args.max_jsonl_mb)
        except OSError as e:
            print(f"working_state_writer: log append failed: {e}", file=sys.stderr)
            # current.json write succeeded; partial success is fine
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
