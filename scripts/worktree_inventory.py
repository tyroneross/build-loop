#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""worktree_inventory.py — what a worktree removal would actually delete.

`git status --short` names tracked changes and untracked files but says nothing
about GITIGNORED ones, and `git worktree remove --force` deletes the whole
directory. An operator approving a removal on a "tool caches only" reading is
therefore approving a set the inventory never showed them — the ignored files
could equally be a `.env`, a local SQLite database, or an unapplied patch.

This module lists the ignored set alongside the tracked and untracked ones,
classifies every ignored entry, and states plainly whether the evidence supports
a caches-only characterization. It NEVER mutates anything: one read-only
`git status` call per worktree.

CLI::

    python3 scripts/worktree_inventory.py --path <worktree> [--matching] [--json]

Exit 0 = inventory produced (including "nothing to report").
Exit 1 = the path is not a readable Git worktree.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# First match wins. Patterns are matched against every path segment and against
# the full relative path, so `a/b/__pycache__/c.pyc` classifies as tool_cache.
_CLASSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Anything that could carry unreproducible work or a credential is checked
    # FIRST: a false "cache" reading is the failure this module exists to stop.
    (
        "potentially_valuable",
        (
            ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.keystore",
            "*credential*", "*secret*", "*.sqlite", "*.sqlite3", "*.db", "*.sql",
            "*.dump", "*.patch", "*.diff", ".build-loop", ".rally", ".bookmark",
            ".claude", ".codex", "*.bak", "*.orig", "TODO*", "NOTES*",
        ),
    ),
    (
        "tool_cache",
        (
            "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
            ".tox", ".gradle", ".DS_Store", ".ipynb_checkpoints", ".eslintcache",
            ".turbo", ".parcel-cache", ".sass-cache", "*.pyc", "*.pyo",
        ),
    ),
    (
        "dependency",
        (
            "node_modules", ".venv", "venv", "vendor", "Pods", ".bundle",
            "bower_components", ".pnpm-store", ".yarn",
        ),
    ),
    (
        "build_output",
        (
            "dist", "build", ".build", "target", "out", ".next", "coverage",
            "htmlcov", "DerivedData", "*.o", "*.so", "*.dylib", "*.a", "*.class",
        ),
    ),
    ("log", ("*.log", "logs", "nohup.out")),
)

# Classes whose deletion is genuinely reproducible. Anything outside this set
# blocks a caches-only characterization.
_REPRODUCIBLE = {"tool_cache", "dependency", "build_output", "log"}

CLASS_NAMES = tuple(name for name, _ in _CLASSES) + ("unclassified",)


def classify(rel_path: str) -> str:
    """Return the class of one ignored path. Unknown shapes stay unclassified."""
    text = rel_path.rstrip("/")
    segments = [seg for seg in text.split("/") if seg]
    candidates = [text] + segments
    for name, patterns in _CLASSES:
        for pattern in patterns:
            for candidate in candidates:
                if candidate == pattern or fnmatch.fnmatch(candidate, pattern):
                    return name
    return "unclassified"


def _status_entries(path: Path, *, matching: bool) -> tuple[list[tuple[str, str]], str | None]:
    """Return [(xy_code, path), ...] from one read-only `git status` call."""
    args = ["git", "-C", str(path), "status", "--porcelain=v1", "-z", "--ignored=traditional"]
    if matching:
        # Traditional mode collapses an ignored directory to `scratch/` unless
        # every file is requested; `--ignored=matching` collapses it either way.
        args.append("--untracked-files=all")
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
    except (OSError, ValueError) as exc:
        return [], f"git status failed: {exc}"
    if proc.returncode != 0:
        return [], (proc.stderr or proc.stdout).strip() or "git status failed"

    entries: list[tuple[str, str]] = []
    fields = [f for f in proc.stdout.split("\0")]
    i = 0
    while i < len(fields):
        field = fields[i]
        if not field:
            i += 1
            continue
        # porcelain=v1 emits `XY<space>path`: the code is fixed-width two chars.
        code = field[:2]
        rel = field[3:]
        entries.append((code, rel))
        # Rename/copy entries carry the original path as the next NUL field.
        if code and code[0] in ("R", "C"):
            i += 1
        i += 1
    return entries, None


def inventory(path: str | Path, *, matching: bool = False) -> dict[str, Any]:
    """Inventory one worktree: tracked changes, untracked files, ignored files.

    `matching=True` expands ignored directories into individual files. The
    default collapses them (`node_modules/`), which keeps the operator packet
    readable without hiding any top-level entry.
    """
    candidate = Path(path).resolve()
    result: dict[str, Any] = {
        "path": str(candidate),
        "ok": False,
        "error": None,
        "tracked_changes": [],
        "untracked": [],
        "ignored": [],
        "ignored_by_class": {name: [] for name in CLASS_NAMES},
        "counts": {"tracked_changes": 0, "untracked": 0, "ignored": 0},
        "non_reproducible_ignored": [],
        "caches_only_claim_supported": False,
        "characterization": "worktree could not be inventoried",
        "expanded_ignored_directories": bool(matching),
    }
    if not candidate.exists():
        result["error"] = "path does not exist"
        return result

    entries, error = _status_entries(candidate, matching=matching)
    if error:
        result["error"] = error
        return result

    for code, rel in entries:
        if not rel:
            continue
        if code == "!!":
            result["ignored"].append(rel)
            result["ignored_by_class"][classify(rel)].append(rel)
        elif code == "??":
            result["untracked"].append(rel)
        else:
            result["tracked_changes"].append(f"{code} {rel}".strip())

    result["ok"] = True
    result["counts"] = {
        "tracked_changes": len(result["tracked_changes"]),
        "untracked": len(result["untracked"]),
        "ignored": len(result["ignored"]),
    }
    result["non_reproducible_ignored"] = sorted(
        entry
        for name in CLASS_NAMES
        if name not in _REPRODUCIBLE
        for entry in result["ignored_by_class"][name]
    )
    result["caches_only_claim_supported"] = not result["non_reproducible_ignored"]
    result["characterization"] = _characterize(result)
    return result


def _characterize(result: dict[str, Any]) -> str:
    """One operator-readable sentence naming what removal would delete."""
    counts = result["counts"]
    if not any(counts.values()):
        return "worktree holds no tracked changes, untracked files, or ignored files"
    parts = []
    if counts["tracked_changes"]:
        parts.append(f"{counts['tracked_changes']} tracked change(s)")
    if counts["untracked"]:
        parts.append(f"{counts['untracked']} untracked file(s)")
    if counts["ignored"]:
        parts.append(f"{counts['ignored']} ignored entry(ies)")
    head = "removal would delete " + ", ".join(parts)
    risky = result["non_reproducible_ignored"]
    if risky:
        named = ", ".join(risky[:5]) + (" ..." if len(risky) > 5 else "")
        return (
            f"{head}; {len(risky)} ignored entry(ies) are NOT reproducible tool "
            f"caches ({named}) — a caches-only characterization is unsupported"
        )
    if counts["ignored"]:
        return f"{head}; every ignored entry classifies as a reproducible cache, dependency, build output, or log"
    return head


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Inventory a worktree before removal.")
    p.add_argument("--path", required=True, help="Worktree directory to inventory")
    p.add_argument(
        "--matching",
        action="store_true",
        help="Expand ignored directories into individual files (verbose)",
    )
    p.add_argument("--json", action="store_true", help="Emit the inventory as JSON")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = inventory(args.path, matching=args.matching)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["error"]:
            print(f"error: {result['error']} ({result['path']})", file=sys.stderr)
        else:
            print(result["characterization"])
            for label, key in (
                ("tracked", "tracked_changes"),
                ("untracked", "untracked"),
                ("ignored", "ignored"),
            ):
                for entry in result[key]:
                    print(f"  [{label}] {entry}")
    return 1 if result["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
