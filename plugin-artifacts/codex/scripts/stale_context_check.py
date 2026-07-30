#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
stale_context_check.py — detect context/handoff docs that have drifted from the codebase.

For each matched context doc (git-tracked), reports how many commits have landed
since the doc was last touched and how many calendar days have passed.  Flags the
doc as stale when either threshold is exceeded.

Time basis: repo HEAD commit unix timestamp ("now") so results are deterministic
in tests regardless of wall-clock.

CLI
---
    stale_context_check.py --workdir <repo> [--globs <glob> ...] \
        [--commits-threshold N] [--days-threshold N] [--json]

Exit code: always 0 (fail-soft).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_GLOBS: list[str] = [
    "ORCHESTRATION.md",
    "HANDOFF*.md",
    "HANDOVER*.md",
    "CONTINUATION*.md",
    "*RETROSPECTIVE*.md",
    "docs/**/*context*.md",
    ".build-loop/context/*.md",
    "*-handoff.md",
]

DEFAULT_COMMITS_THRESHOLD = 20
DEFAULT_DAYS_THRESHOLD = 14


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _repo_head_time(workdir: Path) -> int | None:
    """Return the unix commit time of HEAD, or None on failure."""
    r = _run_git(["log", "-1", "--format=%ct", "HEAD"], workdir)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _tracked_files(workdir: Path) -> set[str]:
    """Return all git-tracked file paths relative to workdir."""
    r = _run_git(["ls-files"], workdir)
    if r.returncode != 0:
        return set()
    return {line for line in r.stdout.splitlines() if line}


def _last_commit_info(workdir: Path, rel_path: str) -> tuple[str, int] | None:
    """Return (commit_hash, unix_time) for the last commit touching rel_path."""
    r = _run_git(["log", "-1", "--format=%H|%ct", "--", rel_path], workdir)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    parts = r.stdout.strip().split("|")
    if len(parts) != 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


def _commits_since(workdir: Path, commit_hash: str) -> int | None:
    """Return count of commits reachable from HEAD but not from commit_hash."""
    r = _run_git(["rev-list", "--count", f"{commit_hash}..HEAD"], workdir)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Glob matching (case-insensitive, repo-relative paths)
# ---------------------------------------------------------------------------

def _matches_any_glob(rel_path: str, globs: list[str]) -> bool:
    """Return True if rel_path matches any glob (case-insensitive)."""
    lower = rel_path.lower()
    for pattern in globs:
        pat_lower = pattern.lower()
        # fnmatch handles simple wildcards; for `**` paths we also try matching
        # just the filename component so `docs/**/*context*.md` works portably.
        if fnmatch.fnmatch(lower, pat_lower):
            return True
        # Also match against the filename alone for patterns like *context*.md
        filename = Path(rel_path).name.lower()
        fname_pattern = Path(pat_lower).name
        if "**" in pat_lower and fnmatch.fnmatch(filename, fname_pattern):
            return True
    return False


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def check(
    workdir: Path,
    globs: list[str],
    commits_threshold: int,
    days_threshold: int,
) -> dict[str, Any]:
    """Run the stale-context check and return the result dict."""
    errors: list[str] = []

    # Resolve "now" as HEAD commit time for determinism.
    now_ts = _repo_head_time(workdir)
    if now_ts is None:
        errors.append("Could not read HEAD commit time; days_since will be 0")
        now_ts = 0

    tracked = _tracked_files(workdir)
    if not tracked:
        errors.append("No git-tracked files found (is --workdir a git repo?)")

    # Find tracked files that match a glob.
    candidates: list[str] = sorted(
        p for p in tracked if _matches_any_glob(p, globs)
    )

    docs: list[dict[str, Any]] = []
    for rel_path in candidates:
        info = _last_commit_info(workdir, rel_path)
        if info is None:
            # Tracked but git log returned nothing — skip gracefully.
            errors.append(f"Could not retrieve commit info for {rel_path}")
            continue

        last_commit, last_ct = info
        commits_since = _commits_since(workdir, last_commit)
        if commits_since is None:
            errors.append(f"Could not count commits since {last_commit[:8]} for {rel_path}")
            commits_since = 0

        days_since = (now_ts - last_ct) / 86400.0

        reasons: list[str] = []
        if commits_since >= commits_threshold:
            reasons.append(f"commits_since={commits_since} >= {commits_threshold}")
        if days_since >= days_threshold:
            reasons.append(f"days_since={days_since:.1f} >= {days_threshold}")

        stale = bool(reasons)
        docs.append(
            {
                "path": rel_path,
                "last_commit": last_commit,
                "commits_since": commits_since,
                "days_since": round(days_since, 2),
                "stale": stale,
                "reason": "; ".join(reasons) if reasons else "",
            }
        )

    stale_docs = [d for d in docs if d["stale"]]
    stale_count = len(stale_docs)

    return {"docs": docs, "stale_count": stale_count, "errors": errors}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect stale context/handoff docs in a git repo."
    )
    parser.add_argument(
        "--workdir",
        required=True,
        help="Path to the git repository root.",
    )
    parser.add_argument(
        "--globs",
        nargs="+",
        default=None,
        help="Glob patterns for context docs (repo-relative, case-insensitive). "
        "Overrides built-in defaults.",
    )
    parser.add_argument(
        "--commits-threshold",
        type=int,
        default=DEFAULT_COMMITS_THRESHOLD,
        help=f"Flag doc stale when commits_since >= N (default {DEFAULT_COMMITS_THRESHOLD}).",
    )
    parser.add_argument(
        "--days-threshold",
        type=int,
        default=DEFAULT_DAYS_THRESHOLD,
        help=f"Flag doc stale when days_since >= N (default {DEFAULT_DAYS_THRESHOLD}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Emit JSON to stdout.",
    )
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    globs = args.globs if args.globs is not None else DEFAULT_GLOBS

    result = check(
        workdir=workdir,
        globs=globs,
        commits_threshold=args.commits_threshold,
        days_threshold=args.days_threshold,
    )

    # Human summary → stderr.
    stale_count = result["stale_count"]
    if stale_count > 0:
        stale_paths = [d["path"] for d in result["docs"] if d["stale"]]
        print(
            f"[STALE CONTEXT] {stale_count} doc(s) may be out of sync: "
            + ", ".join(stale_paths),
            file=sys.stderr,
        )
    else:
        print("[STALE CONTEXT] context docs current", file=sys.stderr)

    if args.output_json:
        print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
