#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Architecture freshness state manager — Chunk 4 of architecture-awareness.

CLI flags:
  --check           Print one of {fresh, stale, missing, fresh-but-old}; exit 0.
  --mark-stale      Set state.json.architecture.stale=true (atomic).
  --file PATH       Append PATH to staleFiles[] (dedup, cap 50). Used with --mark-stale.
  --mark-fresh      Set stale=false, clear staleFiles, set lastFreshAt.
  --generation      Print the current invalidation generation.
  --if-generation N Only mark fresh if generation N remains current.
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

State updates hold atomic_io.LockedFile across read/modify/write, matching other
state.json writers. Each invalidation advances architecture.generation; a scan
clears stale state only if its starting generation is still current.

Freshness rules (--check):
  missing         — manifest.json absent OR .build-loop/architecture/ missing.
  stale           — state.json.architecture.stale=true OR manifest age > 24h.
  fresh-but-old   — manifest age 1h..24h AND state not stale (advisory).
  fresh           — manifest age < 1h AND state not stale.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from atomic_io import LockedFile, atomic_write_bytes

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
    """Publish state while the caller holds its shared sidecar lock."""
    atomic_write_bytes(path, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())


def _load_state(state_path: Path, *, for_update: bool = False) -> Optional[Dict[str, Any]]:
    if not state_path.exists():
        return {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(state, dict):
            return state
    except (json.JSONDecodeError, OSError):
        pass
    # A read-only freshness check can tolerate absent metadata. A writer may
    # not replace an unreadable or malformed state containing unrelated data.
    return None if for_update else {}


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

    state = _load_state(workdir / STATE_FILE_REL) or {}
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


def _generation(arch: Dict[str, Any]) -> int:
    value = arch.get("generation", 0)
    return value if type(value) is int and value >= 0 else 0


def cmd_generation(workdir: Path) -> int:
    """Snapshot the invalidation generation before a scan starts."""
    state_path = workdir / STATE_FILE_REL
    with LockedFile(state_path):
        state = _load_state(state_path, for_update=True)
        if state is None:
            raise ValueError("cannot capture architecture generation from invalid state")
        return _generation(_arch_block(state))


def cmd_mark_stale(workdir: Path, file_rel: Optional[str]) -> bool:
    state_path = workdir / STATE_FILE_REL
    with LockedFile(state_path):
        state = _load_state(state_path, for_update=True)
        if state is None:
            return False
        arch = _arch_block(state).copy()
        arch["stale"] = True
        # Increment even for an already-stale file: a scan may have read its
        # previous contents before this edit completed.
        arch["generation"] = _generation(arch) + 1
        arch.setdefault("staleSince", _iso_now())
        if not arch.get("staleSince"):
            arch["staleSince"] = _iso_now()

        files = list(arch.get("staleFiles") or [])
        if file_rel:
            if file_rel not in files:
                files.append(file_rel)
            if len(files) > STALE_FILES_CAP:
                files = files[-STALE_FILES_CAP:]
        arch["staleFiles"] = files

        state["architecture"] = arch
        _atomic_write_json(state_path, state)
        return True


def cmd_mark_fresh(workdir: Path, expected_generation: Optional[int] = None) -> bool:
    """Clear only the invalidations a background scan could have observed.

    Explicit callers may omit the generation to assert an independently
    completed scan. Background workers always supply their starting generation.
    """
    state_path = workdir / STATE_FILE_REL
    with LockedFile(state_path):
        state = _load_state(state_path, for_update=True)
        if state is None:
            return False
        arch = _arch_block(state).copy()
        if expected_generation is not None and _generation(arch) != expected_generation:
            return False
        arch["stale"] = False
        arch["staleFiles"] = []
        arch["lastFreshAt"] = _iso_now()
        arch.pop("staleSince", None)
        state["architecture"] = arch
        _atomic_write_json(state_path, state)
        return True


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
    parser.add_argument("--generation", action="store_true", help="Print the current invalidation generation.")
    parser.add_argument("--if-generation", type=int, help="Only mark fresh if this generation is still current.")
    parser.add_argument("--file", dest="file_rel", default=None, help="Relative path to add to staleFiles[].")
    parser.add_argument("--lockfile", action="store_true", help="Print lockfile path and exit.")
    parser.add_argument("--workdir", default=None, help="Override cwd (default: $PWD).")
    parser.add_argument("--no-fire", action="store_true", help="No-op flag for symmetry with hook contract.")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve() if args.workdir else Path.cwd().resolve()

    actions = sum([args.check, args.mark_stale, args.mark_fresh, args.lockfile, args.generation])
    if actions != 1:
        parser.error("specify exactly one of --check, --mark-stale, --mark-fresh, --lockfile, --generation")

    if args.if_generation is not None and (not args.mark_fresh or args.if_generation < 0):
        parser.error("--if-generation requires --mark-fresh and a nonnegative value")
    if args.generation:
        print(cmd_generation(workdir))
        return 0
    if args.lockfile:
        print(cmd_lockfile(workdir))
        return 0
    if args.check:
        print(cmd_check(workdir))
        return 0
    if args.mark_stale:
        return 0 if cmd_mark_stale(workdir, args.file_rel) else 1
    if args.mark_fresh:
        return 0 if cmd_mark_fresh(workdir, args.if_generation) else 1
    return 1  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
