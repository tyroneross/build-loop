#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Global append-only update ledger for build-loop memory.

Lane-local ``INDEX.jsonl`` files are useful for sibling discovery inside a
single memory directory. This module owns the global audit trail for the whole
configured memory store:

  <memory-root>/indexes/updates.jsonl

Each row records which project/lane changed, which writer produced it, and the
repo commit that the memory update represented when available. Freshness checks
can use this ledger as the broad baseline and fall back to legacy
``milestones.jsonl`` when a project has not emitted ledger rows yet.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _paths import memory_store_root  # type: ignore  # noqa: E402
from atomic_io import LockedFile  # type: ignore  # noqa: E402

LEDGER_FILENAME = "updates.jsonl"
LOCK_TIMEOUT_S = 10
SCHEMA_VERSION = 1

VALID_ACTIONS = frozenset({
    "write",
    "update",
    "delete",
    "append",
    "migrate",
    "mark-applied",
    "supersede",
})

KNOWN_LANES = frozenset({
    "architecture",
    "debugging",
    "decisions",
    "design",
    "indexes",
    "lessons",
    "milestones",
    "product",
    "raw",
    "sources",
})


def iso_utc(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        normalized = stamp.replace("Z", "+00:00") if stamp.endswith("Z") else stamp
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def ledger_path(memory_root: Path | str | None = None) -> Path:
    root = Path(memory_root).expanduser() if memory_root is not None else memory_store_root()
    return root / "indexes" / LEDGER_FILENAME


def _hash_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _git_value(workdir: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(workdir)] + args,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        pass
    return None


def _rel_path(path: Path | str, memory_root: Path) -> str:
    raw = Path(path)
    if raw.is_absolute():
        try:
            return str(raw.resolve(strict=False).relative_to(memory_root.resolve(strict=False)))
        except ValueError:
            return str(raw)
    return str(raw)


def infer_memory_root_for_path(path: Path | str, fallback: Path | str | None = None) -> Path:
    """Infer the memory store root from a memory file path.

    Recognizes top-level lanes (``<root>/lessons/x.md``), project lanes
    (``<root>/projects/<slug>/decisions/x.md``), and milestone files
    (``<root>/projects/<slug>/milestones.jsonl``). ``fallback`` is returned
    when the path does not look like a canonical memory path.
    """
    raw = Path(path)
    if not raw.is_absolute():
        return Path(fallback) if fallback is not None else memory_store_root()
    p = raw.resolve(strict=False)
    parts = p.parts
    if "projects" in parts:
        idx = parts.index("projects")
        tail = parts[idx + 1 :]
        for pos, part in enumerate(tail):
            if part in KNOWN_LANES or part == "milestones.jsonl":
                return Path(*parts[:idx]) if idx > 0 else Path(os.sep)
        if len(tail) >= 2 and tail[-1] == "milestones.jsonl":
            return Path(*parts[:idx]) if idx > 0 else Path(os.sep)
    if p.parent.name in KNOWN_LANES:
        return p.parent.parent
    if fallback is not None:
        return Path(fallback)
    return memory_store_root()


def infer_scope(path: Path | str, memory_root: Path | str | None = None) -> tuple[str, str]:
    """Return ``(project, lane)`` inferred from a memory-root-relative path."""
    root = Path(memory_root).expanduser() if memory_root is not None else None
    rel = _rel_path(path, root) if root is not None else str(path)
    parts = Path(rel).parts
    if not parts:
        return "_global", "unknown"
    if parts[0] == "projects" and len(parts) >= 3:
        tail = parts[1:]
        for pos, part in enumerate(tail):
            if part in KNOWN_LANES:
                project = "/".join(tail[:pos]) or "_unscoped"
                return project, part
            if part == "milestones.jsonl":
                project = "/".join(tail[:pos]) or "_unscoped"
                return project, "milestones"
        return tail[0], tail[1] if len(tail) > 1 else "unknown"
    if parts[0] in KNOWN_LANES:
        return "_global", parts[0]
    return "_global", "unknown"


def _event_id(row: dict[str, Any]) -> str:
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def append_update(
    *,
    memory_root: Path | str | None = None,
    project: str | None = None,
    lane: str | None = None,
    action: str,
    path: Path | str,
    writer: str,
    run_id: str | None = None,
    source_repo: str | None = None,
    source_workdir: Path | str | None = None,
    source_commit: str | None = None,
    source_host: str | None = None,
    memory_id: str | None = None,
    summary: str | None = None,
    sha256: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one update row and return it."""
    if action not in VALID_ACTIONS:
        raise ValueError(f"action must be one of {sorted(VALID_ACTIONS)}; got {action!r}")
    if not writer:
        raise ValueError("writer is required")

    root = (
        Path(memory_root).expanduser()
        if memory_root is not None
        else (memory_store_root() if not Path(path).is_absolute() else infer_memory_root_for_path(path))
    )
    rel = _rel_path(path, root)
    inferred_project, inferred_lane = infer_scope(rel)
    final_project = project or inferred_project
    final_lane = lane or inferred_lane

    workdir_abs: str | None = None
    if source_workdir is not None:
        workdir_abs = str(Path(source_workdir).expanduser().resolve(strict=False))
        if source_repo is None:
            source_repo = _git_value(Path(workdir_abs), ["remote", "get-url", "origin"])
        if source_commit is None:
            source_commit = _git_value(Path(workdir_abs), ["rev-parse", "HEAD"])

    abs_path = (root / rel) if not Path(rel).is_absolute() else Path(rel)
    if sha256 is None and action != "delete":
        sha256 = _hash_file(abs_path)

    row: dict[str, Any] = {
        "ts": iso_utc(),
        "schema_version": SCHEMA_VERSION,
        "project": final_project,
        "lane": final_lane,
        "action": action,
        "path": rel,
        "writer": writer,
    }
    optional = {
        "run_id": run_id,
        "source_repo": source_repo,
        "source_workdir": workdir_abs,
        "source_commit": source_commit,
        "source_host": source_host,
        "memory_id": memory_id,
        "summary": summary,
        "sha256": sha256 or None,
    }
    for key, value in optional.items():
        if value is not None:
            row[key] = value
    if metadata:
        row["metadata"] = metadata
    row["event_id"] = _event_id(row)

    log_path = ledger_path(root)
    line = json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with LockedFile(log_path, timeout_s=LOCK_TIMEOUT_S):
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    return row


def tail_updates(
    memory_root: Path | str | None = None,
    *,
    project: str | None = None,
    lane: str | None = None,
    action: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    path = ledger_path(memory_root)
    if not path.exists():
        return []
    cutoff = parse_iso(since)
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if project and row.get("project") != project:
                    continue
                if lane and row.get("lane") != lane:
                    continue
                if action and row.get("action") != action:
                    continue
                if cutoff is not None:
                    row_ts = parse_iso(row.get("ts"))
                    if row_ts is None or row_ts <= cutoff:
                        continue
                rows.append(row)
    except OSError:
        return []
    if limit is not None and len(rows) > limit:
        rows = rows[-limit:]
    return rows


def latest_project_update(
    memory_root: Path | str | None,
    project: str,
    *,
    require_commit: bool = True,
) -> dict[str, Any] | None:
    rows = tail_updates(memory_root, project=project)
    for row in reversed(rows):
        if require_commit and not row.get("source_commit"):
            continue
        return row
    return None


def _metadata_arg(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--metadata-json must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("--metadata-json must be a JSON object")
    return parsed


def _cli_append(args: argparse.Namespace) -> int:
    try:
        row = append_update(
            memory_root=args.memory_root,
            project=args.project,
            lane=args.lane,
            action=args.action,
            path=args.path,
            writer=args.writer,
            run_id=args.run_id,
            source_repo=args.source_repo,
            source_workdir=args.source_workdir,
            source_commit=args.source_commit,
            source_host=args.source_host,
            memory_id=args.memory_id,
            summary=args.summary,
            sha256=args.sha256,
            metadata=_metadata_arg(args.metadata_json),
        )
    except (OSError, TimeoutError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        json.dump(row, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
    return 0


def _cli_tail(args: argparse.Namespace) -> int:
    rows = tail_updates(
        args.memory_root,
        project=args.project,
        lane=args.lane,
        action=args.action,
        since=args.since,
        limit=args.limit,
    )
    if args.json:
        json.dump(rows, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
    else:
        if not rows:
            print("(no rows)")
        for row in rows:
            print(
                f"{row.get('ts')} | {row.get('project')} | {row.get('lane')} "
                f"| {row.get('action')} | {row.get('path')} | commit={str(row.get('source_commit') or '')[:8]}"
            )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--memory-root",
        default=None,
        help="Root of the build-loop-memory store; defaults through _paths.memory_store_root().",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    append = sub.add_parser("append", help="Append one global memory update row")
    append.add_argument("--project", default=None)
    append.add_argument("--lane", default=None)
    append.add_argument("--action", required=True, choices=sorted(VALID_ACTIONS))
    append.add_argument("--path", required=True, help="Memory-root-relative path or absolute path")
    append.add_argument("--writer", required=True)
    append.add_argument("--run-id", default=None)
    append.add_argument("--source-repo", default=None)
    append.add_argument("--source-workdir", default=None)
    append.add_argument("--source-commit", default=None)
    append.add_argument(
        "--source-host",
        default=None,
        choices=["claude_code", "codex", "gemini", "other"],
    )
    append.add_argument("--memory-id", default=None)
    append.add_argument("--summary", default=None)
    append.add_argument("--sha256", default=None)
    append.add_argument("--metadata-json", default=None)
    append.add_argument("--json", action="store_true")

    tail = sub.add_parser("tail", help="Tail global memory update rows")
    tail.add_argument("--project", default=None)
    tail.add_argument("--lane", default=None)
    tail.add_argument("--action", default=None, choices=sorted(VALID_ACTIONS))
    tail.add_argument("--since", default=None)
    tail.add_argument("--limit", type=int, default=None)
    tail.add_argument("--json", action="store_true")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return {"append": _cli_append, "tail": _cli_tail}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
