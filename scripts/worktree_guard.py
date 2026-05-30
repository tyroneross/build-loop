#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Guard build-loop worktree creation so worktrees never sprawl outside the canonical location.

Constants
---------
CANONICAL_WORKTREE_ROOT : str
    Relative path (from repo root) where all build-loop worktrees live.
    Value: ".build-loop/worktrees"

WORKTREE_BRANCH_PREFIX : str
    Required prefix for all build-loop worktree branches.
    Value: "bl/"

Public API
----------
assert_worktree_path(workdir, target_path)
    Raise ValueError if target_path is not under <workdir>/.build-loop/worktrees/.

canonical_worktree_path(workdir, slug) -> Path
    Return the canonical worktree path for the given slug.

canonical_branch_name(slug, chunk=None) -> str
    Return a bl/-prefixed sanitized branch name.

create_guarded_worktree(workdir, slug, *, branch=None, base="main", chunk=None, record=True) -> dict
    Create a worktree under the canonical root and (optionally) log it to state.json.
    Returns {"path": str, "branch": str, "created": bool, "error": str|null}.

CLI
---
  worktree_guard.py --workdir . --slug <s> [--chunk c] [--base main] --json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

CANONICAL_WORKTREE_ROOT = ".build-loop/worktrees"
WORKTREE_BRANCH_PREFIX = "bl/"


def _sanitize_slug(slug: str) -> str:
    """Lowercase, replace non-alnum with '-', collapse consecutive dashes."""
    slug = slug.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-")


def assert_worktree_path(workdir: Path, target_path: str) -> None:
    """Raise ValueError if target_path is not under <workdir>/.build-loop/worktrees/.

    This prevents worktrees from being created in sibling folders, .claude/worktrees,
    or any other location outside the canonical root.
    """
    canonical_root = (workdir / CANONICAL_WORKTREE_ROOT).resolve()
    resolved = Path(target_path).resolve()
    try:
        resolved.relative_to(canonical_root)
    except ValueError:
        raise ValueError(
            f"worktree path {resolved} is not under canonical root {canonical_root}; "
            f"all build-loop worktrees must live under {CANONICAL_WORKTREE_ROOT}"
        )


def canonical_worktree_path(workdir: Path, slug: str) -> Path:
    """Return <workdir>/.build-loop/worktrees/<sanitized-slug>."""
    return workdir / CANONICAL_WORKTREE_ROOT / _sanitize_slug(slug)


def canonical_branch_name(slug: str, chunk: str | None = None) -> str:
    """Return 'bl/<slug>' or 'bl/<slug>-<chunk>', sanitized.

    Both slug and chunk are lowercased; non-alnum characters become '-'.
    """
    clean_slug = _sanitize_slug(slug)
    if chunk:
        clean_chunk = _sanitize_slug(chunk)
        return f"{WORKTREE_BRANCH_PREFIX}{clean_slug}-{clean_chunk}"
    return f"{WORKTREE_BRANCH_PREFIX}{clean_slug}"


def create_guarded_worktree(
    workdir: Path,
    slug: str,
    *,
    branch: str | None = None,
    base: str = "main",
    chunk: str | None = None,
    record: bool = True,
) -> dict[str, Any]:
    """Create a git worktree under the canonical root and optionally record it.

    Parameters
    ----------
    workdir : Path
        Repository root.
    slug : str
        Short identifier for the worktree (e.g. run ID, feature name).
    branch : str | None
        Branch name to create. Defaults to canonical_branch_name(slug, chunk).
    base : str
        Base ref to branch from (default "main").
    chunk : str | None
        Optional chunk suffix appended to the default branch name.
    record : bool
        When True, calls log_decision.log_created_ref() to persist the ref in state.json.

    Returns
    -------
    dict with keys:
        path    : str   — absolute path to the created worktree
        branch  : str   — branch name
        created : bool  — True if git worktree add succeeded
        error   : str | None — error message if git failed, else None
    """
    wt_path = canonical_worktree_path(workdir, slug)
    wt_branch = branch or canonical_branch_name(slug, chunk)

    # Guard: ensure path is under canonical root (should always pass here, but
    # be defensive in case caller passed an explicit branch that altered path).
    try:
        assert_worktree_path(workdir, str(wt_path))
    except ValueError as exc:
        return {"path": str(wt_path), "branch": wt_branch, "created": False, "error": str(exc)}

    # Ensure the parent directory exists.
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["git", "worktree", "add", "-b", wt_branch, str(wt_path), base],
        cwd=str(workdir),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        error_msg = result.stderr.strip() or f"git worktree add exited with code {result.returncode}"
        return {"path": str(wt_path), "branch": wt_branch, "created": False, "error": error_msg}

    if record:
        # Import here to avoid circular dependency issues and keep this module stdlib-only
        # at the import level (log_decision is also stdlib-only).
        import importlib.util
        import os

        # Resolve log_decision relative to this script's directory.
        here = Path(__file__).parent
        spec = importlib.util.spec_from_file_location("log_decision", here / "log_decision.py")
        if spec and spec.loader:
            ld = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ld)  # type: ignore[arg-type]
            try:
                ld.log_created_ref(
                    workdir,
                    {
                        "kind": "worktree",
                        "path": str(wt_path),
                        "branch": wt_branch,
                        "review_hold": False,
                    },
                )
            except SystemExit as exc:
                # log failure is non-fatal; worktree was already created
                print(f"warning: log_created_ref failed: {exc}", file=sys.stderr)

    return {"path": str(wt_path), "branch": wt_branch, "created": True, "error": None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a guarded build-loop worktree.")
    parser.add_argument("--workdir", default=".", help="Repository root (default: .)")
    parser.add_argument("--slug", required=True, help="Short identifier for the worktree")
    parser.add_argument("--chunk", default=None, help="Optional chunk suffix for branch name")
    parser.add_argument("--base", default="main", help="Base ref to branch from (default: main)")
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="Skip writing to state.json (default: record)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON (default: human-readable)",
    )
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    result = create_guarded_worktree(
        workdir,
        args.slug,
        base=args.base,
        chunk=args.chunk,
        record=not args.no_record,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["created"]:
            print(f"created worktree: {result['path']} (branch: {result['branch']})")
        else:
            print(f"error: {result['error']}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
