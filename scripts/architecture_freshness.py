#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Architecture freshness state manager — Chunk 4 of architecture-awareness.

CLI flags:
  --check           Print one of {fresh, stale, missing, fresh-but-old}; exit 0.
  --mark-stale      Set state.json.architecture.stale=true (atomic).
  --file PATH       Append PATH to staleFiles[] (dedup, cap 50). Used with --mark-stale.
  --mark-fresh      Set stale=false, clear staleFiles, set lastFreshAt.
  --lockfile        Print path to single-flight lockfile and exit.
  --workdir PATH    Override cwd (for tests).
  --no-fire         (informational; respected by callers — this script never
                    spawns subprocesses, so the flag is a no-op here. Present
                    for symmetry with the hook contract.)

Single-flight semantics:
  An advisory `fcntl.flock` lock is acquired on
  ``.build-loop/architecture/.scan.lock`` when a *scan* is to fire. The mark
  operations DO NOT acquire the lock — they only mutate `state.json` so the
  orchestrator and concurrent hooks see consistent staleness even while a scan
  is in flight. The hook shell scripts use `flock`/`python -c` to acquire the
  lock around the actual scan invocation.

Atomic writes via temp file + os.rename. Race-safe against orchestrator reads.

Freshness rules (--check):
  missing         — manifest.json absent OR .build-loop/architecture/ missing.
  stale           — state.json.architecture.stale=true OR manifest age > 24h.
  fresh-but-old   — manifest age 1h..24h AND state not stale (advisory).
  fresh           — manifest age < 1h AND state not stale.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ARCH_DIR_REL = ".build-loop/architecture"
STATE_FILE_REL = ".build-loop/state.json"
MANIFEST_NAME = "manifest.json"
LOCKFILE_NAME = ".scan.lock"
STALE_FILES_CAP = 50
STALE_THRESHOLD_S = 24 * 3600  # 24h
OLD_THRESHOLD_S = 3600          # 1h


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON atomically via temp file + os.rename within the same dir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_state(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _arch_block(state: Dict[str, Any]) -> Dict[str, Any]:
    block = state.get("architecture")
    if not isinstance(block, dict):
        block = {}
    return block


def cmd_check(workdir: Path) -> str:
    arch_dir = workdir / ARCH_DIR_REL
    manifest = arch_dir / MANIFEST_NAME
    if not arch_dir.exists() or not manifest.exists():
        return "missing"

    state = _load_state(workdir / STATE_FILE_REL)
    arch = _arch_block(state)
    if arch.get("stale") is True:
        return "stale"

    # Use manifest mtime as the freshness signal (also robust against missing
    # `generated_at` keys in older schemas).
    try:
        mtime = manifest.stat().st_mtime
    except OSError:
        return "missing"
    age = time.time() - mtime
    if age > STALE_THRESHOLD_S:
        return "stale"
    if age > OLD_THRESHOLD_S:
        return "fresh-but-old"
    return "fresh"


def cmd_mark_stale(workdir: Path, file_rel: Optional[str]) -> None:
    state_path = workdir / STATE_FILE_REL
    state = _load_state(state_path)
    arch = _arch_block(state).copy()
    arch["stale"] = True
    arch.setdefault("staleSince", _iso_now())
    if not arch.get("staleSince"):
        arch["staleSince"] = _iso_now()

    files = list(arch.get("staleFiles") or [])
    if file_rel:
        if file_rel not in files:
            files.append(file_rel)
        # Cap to prevent unbounded growth on long-running sessions.
        if len(files) > STALE_FILES_CAP:
            files = files[-STALE_FILES_CAP:]
    arch["staleFiles"] = files

    state["architecture"] = arch
    _atomic_write_json(state_path, state)


def cmd_mark_fresh(workdir: Path) -> None:
    state_path = workdir / STATE_FILE_REL
    state = _load_state(state_path)
    arch = _arch_block(state).copy()
    arch["stale"] = False
    arch["staleFiles"] = []
    arch["lastFreshAt"] = _iso_now()
    arch.pop("staleSince", None)
    state["architecture"] = arch
    _atomic_write_json(state_path, state)


def cmd_lockfile(workdir: Path) -> str:
    return str(workdir / ARCH_DIR_REL / LOCKFILE_NAME)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="architecture_freshness",
        description="Architecture freshness state manager (Chunk 4).",
    )
    parser.add_argument("--check", action="store_true", help="Print fresh|stale|missing|fresh-but-old.")
    parser.add_argument("--mark-stale", action="store_true", help="Mark state.json.architecture.stale=true.")
    parser.add_argument("--mark-fresh", action="store_true", help="Mark fresh; clear staleFiles.")
    parser.add_argument("--file", dest="file_rel", default=None, help="Relative path to add to staleFiles[].")
    parser.add_argument("--lockfile", action="store_true", help="Print lockfile path and exit.")
    parser.add_argument("--workdir", default=None, help="Override cwd (default: $PWD).")
    parser.add_argument("--no-fire", action="store_true", help="No-op flag for symmetry with hook contract.")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve() if args.workdir else Path.cwd().resolve()

    actions = sum([args.check, args.mark_stale, args.mark_fresh, args.lockfile])
    if actions != 1:
        parser.error("specify exactly one of --check, --mark-stale, --mark-fresh, --lockfile")

    if args.lockfile:
        print(cmd_lockfile(workdir))
        return 0
    if args.check:
        print(cmd_check(workdir))
        return 0
    if args.mark_stale:
        cmd_mark_stale(workdir, args.file_rel)
        return 0
    if args.mark_fresh:
        cmd_mark_fresh(workdir)
        return 0
    return 1  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
