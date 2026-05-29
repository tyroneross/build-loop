#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Append-only log of build-loop-memory writes for cross-session discovery.

Companion to memory_writer.py. Every memory write/update/delete in
the selected canonical build-loop-memory lane appends one row to that
lane's `INDEX.jsonl`. Active sessions can `tail` this log between phases
to see what siblings have learned and decide whether to incorporate it
into the current build.

This is the discovery side of the multi-session model. Concurrent-presence
detection is owned by Rally Point presence (scripts/rally_point/presence.py)
so two sessions can see each other and avoid clobbering each other's
WORK. Memory INDEX lets them propagate each other's LEARNINGS.

Row schema (one JSON object per line):
  {
    "ts": "ISO8601 UTC",
    "run_id": "run_<UTC>_<hash>",
    "action": "write" | "update" | "delete",
    "file": "<rel-path inside memory dir>",
    "sha256": "<hex of file content, '' on delete>",
    "source_repo": "<repo url or abs path, optional>",
    "source_workdir": "<workdir abs path, optional>",
    "source_host": "claude_code" | "codex" | ...
  }

Concurrency:
  fcntl.flock(LOCK_EX) on a sidecar .lock file (same pattern as
  write_run_entry.py + write_cost_ledger_row.py). Append is the only
  mutation; the log is never rewritten.

Stdlib only. Python 3.11+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
import sys
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from _paths import top_level_lessons_dir  # type: ignore  # noqa: E402
from atomic_io import LockedFile, atomic_write_bytes  # type: ignore  # noqa: E402

LOCK_TIMEOUT_S = 10
DEFAULT_INDEX_DIR = top_level_lessons_dir()
INDEX_FILENAME = "INDEX.jsonl"

VALID_ACTIONS = frozenset({"write", "update", "delete"})


def iso_utc(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(stamp: str) -> datetime | None:
    try:
        normalized = stamp.replace("Z", "+00:00") if stamp.endswith("Z") else stamp
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _hash_file(path: Path) -> str:
    """Stable sha256 hex of file content. Returns '' on missing/error."""
    if not path.exists():
        return ""
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _index_path(index_dir: Path) -> Path:
    return index_dir / INDEX_FILENAME


def append_row(
    index_dir: Path,
    *,
    run_id: str,
    action: str,
    file_rel: str,
    sha256: str | None = None,
    source_repo: str | None = None,
    source_workdir: str | None = None,
    source_host: str | None = None,
) -> dict:
    """Append one row to INDEX.jsonl. Returns the row written.

    `sha256` is computed automatically when None and the absolute file
    exists at <index_dir>/<file_rel>. For 'delete' actions, sha256 should
    be the LAST known hash (caller passes explicitly) or '' if unknown.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"action must be one of {sorted(VALID_ACTIONS)}; got {action!r}")
    index_dir.mkdir(parents=True, exist_ok=True)
    if sha256 is None:
        abs_path = index_dir / file_rel
        sha256 = _hash_file(abs_path) if action != "delete" else ""
    row: dict[str, Any] = {
        "ts": iso_utc(),
        "run_id": run_id,
        "action": action,
        "file": file_rel,
        "sha256": sha256,
    }
    if source_repo is not None:
        row["source_repo"] = source_repo
    if source_workdir is not None:
        row["source_workdir"] = source_workdir
    if source_host is not None:
        row["source_host"] = source_host

    log_path = _index_path(index_dir)
    line = (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")
    with LockedFile(log_path, timeout_s=LOCK_TIMEOUT_S):
        existing = log_path.read_bytes() if log_path.exists() else b""
        atomic_write_bytes(log_path, existing + line)
    return row


def tail(
    index_dir: Path,
    *,
    since: str | None = None,
    exclude_run_id: str | None = None,
    file_filter: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Return rows from INDEX.jsonl, optionally filtered.

    `since` — ISO8601; only rows with ts > since are returned.
    `exclude_run_id` — filter out rows from this run (so a session sees
                       only OTHER sessions' writes).
    `file_filter` — substring match against the row's file field.
    `limit` — return at most this many MOST RECENT rows.
    """
    log_path = _index_path(index_dir)
    if not log_path.exists():
        return []
    cutoff = parse_iso(since) if since else None
    out: list[dict] = []
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if exclude_run_id and row.get("run_id") == exclude_run_id:
                    continue
                if file_filter and file_filter not in (row.get("file") or ""):
                    continue
                if cutoff is not None:
                    row_ts = parse_iso(row.get("ts", ""))
                    if row_ts is None or row_ts <= cutoff:
                        continue
                out.append(row)
    except OSError:
        return []
    if limit is not None and len(out) > limit:
        out = out[-limit:]
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_append(args: argparse.Namespace) -> int:
    try:
        row = append_row(
            Path(args.index_dir),
            run_id=args.run_id,
            action=args.action,
            file_rel=args.file,
            sha256=args.sha256,
            source_repo=args.source_repo,
            source_workdir=args.source_workdir,
            source_host=args.source_host,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except TimeoutError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(row, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
    return 0


def _cli_tail(args: argparse.Namespace) -> int:
    rows = tail(
        Path(args.index_dir),
        since=args.since,
        exclude_run_id=args.exclude_run_id,
        file_filter=args.file_filter,
        limit=args.limit,
    )
    if args.json:
        json.dump(rows, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
    else:
        if not rows:
            print("(no rows)")
        for r in rows:
            print(
                f"{r.get('ts')} | {r.get('action')} | {r.get('file')} "
                f"| run={r.get('run_id')} | host={r.get('source_host', '?')}"
            )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--index-dir",
        default=str(DEFAULT_INDEX_DIR),
        help="Override default canonical memory lane (testing).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append", help="Append a memory-write row")
    a.add_argument("--run-id", required=True)
    a.add_argument("--action", required=True, choices=sorted(VALID_ACTIONS))
    a.add_argument("--file", required=True, help="Relative path inside index-dir")
    a.add_argument("--sha256", default=None, help="Override; auto-computed when omitted")
    a.add_argument("--source-repo", default=None)
    a.add_argument("--source-workdir", default=None)
    a.add_argument(
        "--source-host", default=None,
        choices=["claude_code", "codex", "gemini", "other"],
    )
    a.add_argument("--json", action="store_true")

    t = sub.add_parser("tail", help="Read rows from INDEX.jsonl")
    t.add_argument("--since", default=None, help="ISO8601; only newer rows")
    t.add_argument("--exclude-run-id", default=None)
    t.add_argument("--file-filter", default=None, help="Substring match")
    t.add_argument("--limit", type=int, default=None)
    t.add_argument("--json", action="store_true")

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dispatch = {"append": _cli_append, "tail": _cli_tail}
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
