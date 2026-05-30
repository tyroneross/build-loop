#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
memory_staleness_check.py — detect when project memory has drifted behind the repo's git HEAD.

Reads the LATEST line of the project's milestone log (append-only JSONL at
``<memory-root>/projects/<slug>/milestones.jsonl``).  Each line has a ``commit``
field recording the repo HEAD sha at milestone-write time.  The check counts
commits that landed in the repo AFTER that sha; when the count reaches the
configured threshold the run is flagged stale.

CLI
---
    memory_staleness_check.py --workdir <repo> [--project <slug>]
        [--memory-root <path>] [--commits-threshold N] [--json]

Exit code: always 0 (fail-soft).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_COMMITS_THRESHOLD = 5
DEFAULT_MEMORY_ROOT = Path.home() / "dev" / "git-folder" / "build-loop-memory"


# ---------------------------------------------------------------------------
# Git helpers — reused pattern from stale_context_check.py
# ---------------------------------------------------------------------------

def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _repo_head(workdir: Path) -> str | None:
    """Return the full sha of HEAD, or None on failure."""
    r = _run_git(["rev-parse", "HEAD"], workdir)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.strip()


def _commits_since(workdir: Path, commit_hash: str) -> int | None:
    """Return count of commits reachable from HEAD but not from commit_hash."""
    r = _run_git(["rev-list", "--count", f"{commit_hash}..HEAD"], workdir)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _is_git_repo(workdir: Path) -> bool:
    r = _run_git(["rev-parse", "--git-dir"], workdir)
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Milestone helpers
# ---------------------------------------------------------------------------

def _last_milestone_commit(milestones_path: Path) -> str | None:
    """Return the ``commit`` field from the LAST line of the JSONL file.

    Returns None when the file is absent, empty, or the last line has no
    ``commit`` key.
    """
    if not milestones_path.exists():
        return None
    last_line: str | None = None
    try:
        with milestones_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last_line = line
    except OSError:
        return None
    if last_line is None:
        return None
    try:
        obj = json.loads(last_line)
    except json.JSONDecodeError:
        return None
    return obj.get("commit") or None


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def check(
    workdir: Path,
    slug: str,
    memory_root: Path,
    commits_threshold: int,
) -> dict[str, Any]:
    """Run the memory-staleness check and return the result dict."""

    # --- guard: non-git workdir ---
    if not _is_git_repo(workdir):
        return {
            "slug": slug,
            "memory_as_of_commit": None,
            "repo_head": None,
            "commits_stale": 0,
            "stale": False,
            "reason": "workdir is not a git repository",
        }

    milestones_path = memory_root / "projects" / slug / "milestones.jsonl"

    memory_as_of_commit = _last_milestone_commit(milestones_path)

    # --- no baseline yet ---
    if memory_as_of_commit is None:
        return {
            "slug": slug,
            "memory_as_of_commit": None,
            "repo_head": _repo_head(workdir),
            "commits_stale": 0,
            "stale": False,
            "reason": "no milestone baseline yet",
        }

    repo_head = _repo_head(workdir)
    if repo_head is None:
        return {
            "slug": slug,
            "memory_as_of_commit": memory_as_of_commit,
            "repo_head": None,
            "commits_stale": 0,
            "stale": False,
            "reason": "could not read repo HEAD",
        }

    commits_stale = _commits_since(workdir, memory_as_of_commit)
    if commits_stale is None:
        # commit sha not in repo history (detached, shallow, or bad sha)
        return {
            "slug": slug,
            "memory_as_of_commit": memory_as_of_commit,
            "repo_head": repo_head,
            "commits_stale": 0,
            "stale": False,
            "reason": f"could not count commits since {memory_as_of_commit[:8]} (shallow clone or unknown sha)",
        }

    stale = commits_stale >= commits_threshold
    message = (
        f"{slug} memory is {commits_stale} commits behind HEAD — append a milestone/decision"
        if stale
        else f"{slug} memory current ({commits_stale} commits since last milestone)"
    )

    return {
        "slug": slug,
        "memory_as_of_commit": memory_as_of_commit,
        "repo_head": repo_head,
        "commits_stale": commits_stale,
        "stale": stale,
        "message": message,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect when project memory has drifted behind the repo's git HEAD."
    )
    parser.add_argument(
        "--workdir",
        required=True,
        help="Path to the git repository root.",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Project slug.  Defaults to the repo directory name.",
    )
    parser.add_argument(
        "--memory-root",
        default=None,
        help=f"Root of the build-loop-memory tree (default: {DEFAULT_MEMORY_ROOT}).",
    )
    parser.add_argument(
        "--commits-threshold",
        type=int,
        default=DEFAULT_COMMITS_THRESHOLD,
        help=f"Flag memory stale when commits_stale >= N (default {DEFAULT_COMMITS_THRESHOLD}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Emit JSON to stdout.",
    )
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    slug = args.project or workdir.name
    memory_root = Path(args.memory_root).resolve() if args.memory_root else DEFAULT_MEMORY_ROOT

    result = check(
        workdir=workdir,
        slug=slug,
        memory_root=memory_root,
        commits_threshold=args.commits_threshold,
    )

    # Human summary → stderr.
    if result.get("stale"):
        print(
            f"[MEMORY STALE] {result['message']}",
            file=sys.stderr,
        )
    else:
        reason = result.get("reason") or result.get("message") or "memory current"
        print(f"[MEMORY OK] {reason}", file=sys.stderr)

    if args.output_json:
        print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
