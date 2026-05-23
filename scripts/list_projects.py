#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""list_projects.py — enumerate project-scoped memory subdirectories.

Walks ``~/.build-loop/memory/projects/`` and prints one row per project
subdirectory:

  <slug>  <md_file_count>  <last_modified_iso>

Sub-components (e.g. ``decision-doctor-cc/workers/``) appear as their own
rows. The ``_archive/`` subtree is included with an ``[archived]`` marker.

Used by ``install_memory.py --check`` for a quick rollup and by operators
who want to know what's in their global memory store without grep'ing
the filesystem by hand.

Usage:
  python3 scripts/list_projects.py
  python3 scripts/list_projects.py --json
  python3 scripts/list_projects.py --root /custom/memory/root

Exit codes:
  0 — listing complete (or projects/ absent)
  1 — read error on a project subdir
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    from _paths import project_memory_root as _project_memory_root  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001 — graceful import-failure mode
    _project_memory_root = None  # type: ignore[assignment]


def _last_mtime(dir_path: Path) -> float:
    """Return the latest mtime across .md files in ``dir_path`` (1 level).

    Returns ``0.0`` if no md files. Doesn't recurse into sub-components —
    those are reported as their own rows.
    """
    latest = 0.0
    for p in dir_path.glob("*.md"):
        try:
            ts = p.stat().st_mtime
            if ts > latest:
                latest = ts
        except OSError:
            continue
    return latest


def _format_ts(ts: float) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def walk(root: Path) -> list[dict[str, Any]]:
    """Walk a projects/ root and return one row per project (or sub-component).

    Row shape: ``{slug, md_files, last_modified_iso, archived}``.
    """
    rows: list[dict[str, Any]] = []
    if not root.is_dir():
        return rows

    # Direct subdirs of projects/ = top-level projects + _archive
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        if sub.name == "_archive":
            # Recurse one level into _archive/<slug>/
            for arch in sorted(sub.iterdir()):
                if not arch.is_dir():
                    continue
                rows.append({
                    "slug": arch.name,
                    "md_files": sum(1 for _ in arch.glob("*.md")),
                    "last_modified_ts": _last_mtime(arch),
                    "archived": True,
                })
            continue
        # Top-level project
        rows.append({
            "slug": sub.name,
            "md_files": sum(1 for _ in sub.glob("*.md")),
            "last_modified_ts": _last_mtime(sub),
            "archived": False,
        })
        # Sub-components (one level deep, skip dotfiles + _archive sentinel)
        for nested in sorted(sub.iterdir()):
            if not nested.is_dir() or nested.name.startswith("_") or nested.name.startswith("."):
                continue
            md_count = sum(1 for _ in nested.glob("*.md"))
            if md_count == 0:
                continue
            rows.append({
                "slug": f"{sub.name}/{nested.name}",
                "md_files": md_count,
                "last_modified_ts": _last_mtime(nested),
                "archived": False,
            })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--root",
        default=None,
        help="Override the projects/ root (default: ~/.build-loop/memory/projects)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    if args.root:
        root = Path(args.root).expanduser().resolve()
    else:
        if _project_memory_root is None:
            print(
                "list_projects: _paths.project_memory_root unavailable; pass --root",
                file=sys.stderr,
            )
            return 1
        root = _project_memory_root()

    if not root.is_dir():
        if args.json:
            print(json.dumps({"root": str(root), "exists": False, "projects": []}))
        else:
            print(f"list_projects: projects/ root absent: {root}")
        return 0

    rows = walk(root)
    if args.json:
        print(json.dumps({"root": str(root), "exists": True, "projects": rows}, indent=2))
        return 0

    if not rows:
        print(f"list_projects: {root} is empty")
        return 0

    # Plain text — fixed-width table
    print(f"{'slug':<40}  {'files':>5}  {'last_mod':<12}  notes")
    print(f"{'-' * 40:<40}  {'-' * 5:>5}  {'-' * 12:<12}  -----")
    for row in rows:
        notes = "[archived]" if row["archived"] else ""
        print(
            f"{row['slug']:<40}  {row['md_files']:>5}  {_format_ts(row['last_modified_ts']):<12}  {notes}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
