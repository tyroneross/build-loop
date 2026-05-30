#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Deterministic check for completed-but-uncommitted tracked work.

CLI:
  commit_state_check.py --workdir <repo> [--hook] [--json]

Exit codes: always 0 (fail-soft, never blocks).

--hook mode: silent when clean; prints one advisory reminder line when
tracked changes are detected (per hook-design rule: silent unless actionable).

--json mode: emits the full envelope as JSON.

Default (neither flag): prints summary string only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _git_status_porcelain(workdir: str) -> list[str] | None:
    """Run `git status --porcelain` and return lines, or None on error."""
    try:
        result = subprocess.run(
            ["git", "-C", workdir, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout.splitlines()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _classify(lines: list[str]) -> dict[str, object]:
    """Classify porcelain v1 output.

    Porcelain v1 format: XY <path>
      X = index (staged) status, Y = working-tree (unstaged) status
      '?' = untracked, '!' = ignored

    We count ONLY tracked files (X or Y is not '?' and not '!').
    Untracked-only noise (both X and Y are '?') does not count.
    """
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []

    for line in lines:
        if len(line) < 4:
            continue
        x = line[0]
        y = line[1]
        path = line[3:]

        if x == "?" and y == "?":
            untracked.append(path)
            continue
        if x == "!" and y == "!":
            # ignored — skip
            continue

        # At least one of X/Y is a real tracked change.
        if x not in ("?", "!", " "):
            staged.append(path)
        if y not in ("?", "!", " "):
            unstaged.append(path)

    tracked_changed = sorted(set(staged) | set(unstaged))
    has_uncommitted = bool(tracked_changed)

    if has_uncommitted:
        n = len(tracked_changed)
        summary = (
            f"{n} tracked file{'s' if n != 1 else ''} changed and not committed"
            " — commit before ending the turn"
        )
    else:
        summary = "clean"

    return {
        "has_uncommitted_tracked": has_uncommitted,
        "tracked_changed": tracked_changed,
        "staged": staged,
        "unstaged": unstaged,
        "untracked_count": len(untracked),
        "summary": summary,
    }


def check(workdir: str) -> dict[str, object]:
    """Return the commit-state envelope for *workdir*."""
    lines = _git_status_porcelain(workdir)
    if lines is None:
        return {
            "has_uncommitted_tracked": False,
            "tracked_changed": [],
            "staged": [],
            "unstaged": [],
            "untracked_count": 0,
            "summary": "not a git repo / git unavailable",
        }
    return _classify(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check for uncommitted tracked changes in a git repo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workdir",
        default=".",
        help="Path to the git repository (default: current directory).",
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help=(
            "Stop-hook mode: silent when clean, prints one reminder line when "
            "tracked changes exist. Always exits 0."
        ),
    )
    parser.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        help="Emit full JSON envelope to stdout.",
    )
    args = parser.parse_args(argv)

    workdir = str(Path(args.workdir).resolve())
    result = check(workdir)

    if args.emit_json:
        print(json.dumps(result, indent=2))
        return 0

    if args.hook:
        # Silent when clean (hook-design rule). One advisory line when dirty.
        if result["has_uncommitted_tracked"]:
            print(result["summary"])
        return 0

    # Default: print summary
    print(result["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
