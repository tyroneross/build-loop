#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""run_loc_delta.py — net-LOC delta for a build-loop run report (observability, no gate).

The user's standing "no additional code" goal needs a visible metric. This computes
lines added / deleted / net + files changed / created over a run's diff range, so the
Review-G report can surface growth and Phase 6 can watch the trend. There is NO gate —
this is pure data.

Usage
-----
  # Over a commit range (the run's first parent .. HEAD):
  python3 scripts/run_loc_delta.py --workdir <repo> --range <base>..<head> --json
  python3 scripts/run_loc_delta.py --workdir <repo> --range <base>..<head>   # markdown line

  # Over the working tree (uncommitted + staged) when no range is known:
  python3 scripts/run_loc_delta.py --workdir <repo> --working --json

Output
------
JSON (``--json``)::

  {"added": 120, "deleted": 18, "net": 102, "files_changed": 5,
   "files_created": 2, "files_deleted": 0, "range": "abc..def"}

Markdown (default) — the exact line the report embeds under ``## Net LOC``::

  +120 / -18 (net +102) across 5 files (2 created, 0 deleted) — range abc..def

Contract
--------
Fail-open: any git error → exit 0 with ``error`` set in JSON / a ``_(loc delta
unavailable: ...)_`` markdown line. A broken metric never blocks a report. Zero deps.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def _numstat_args(*, range_spec: str | None, working: bool) -> list[str]:
    """Build the ``git diff`` argument list for the requested mode.

    - range mode: ``git diff --numstat <range>``
    - working mode: ``git diff --numstat HEAD`` (uncommitted + tracked changes, staged or not)
    """
    if working:
        return ["diff", "--numstat", "HEAD"]
    assert range_spec is not None
    return ["diff", "--numstat", range_spec]


def _name_status_args(*, range_spec: str | None, working: bool) -> list[str]:
    if working:
        return ["diff", "--name-status", "HEAD"]
    assert range_spec is not None
    return ["diff", "--name-status", range_spec]


def compute(workdir: Path, *, range_spec: str | None, working: bool) -> dict:
    """Return the LOC-delta envelope. Never raises — fail-open with ``error`` set."""
    label = range_spec if range_spec else "working-tree"
    try:
        numstat = _git(_numstat_args(range_spec=range_spec, working=working), workdir)
        namestatus = _git(_name_status_args(range_spec=range_spec, working=working), workdir)
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return {
            "added": 0, "deleted": 0, "net": 0,
            "files_changed": 0, "files_created": 0, "files_deleted": 0,
            "range": label, "error": str(exc),
        }

    added = 0
    deleted = 0
    files_changed = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files_changed += 1
        # Binary files report "-\t-\t<path>"; count them as a changed file, 0 LOC.
        try:
            added += int(parts[0])
        except ValueError:
            pass
        try:
            deleted += int(parts[1])
        except ValueError:
            pass

    files_created = 0
    files_deleted = 0
    for line in namestatus.splitlines():
        status = line.split("\t", 1)[0].strip()
        if status.startswith("A"):
            files_created += 1
        elif status.startswith("D"):
            files_deleted += 1

    return {
        "added": added,
        "deleted": deleted,
        "net": added - deleted,
        "files_changed": files_changed,
        "files_created": files_created,
        "files_deleted": files_deleted,
        "range": label,
        "error": None,
    }


def to_markdown(env: dict) -> str:
    """One-line report fragment for the ``## Net LOC`` section."""
    if env.get("error"):
        return f"_(loc delta unavailable: {env['error']})_"
    net = env["net"]
    sign = "+" if net >= 0 else ""
    return (
        f"+{env['added']} / -{env['deleted']} (net {sign}{net}) across "
        f"{env['files_changed']} files ({env['files_created']} created, "
        f"{env['files_deleted']} deleted) — range {env['range']}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_loc_delta", description=__doc__)
    ap.add_argument("--workdir", required=True, help="repo root")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--range", dest="range_spec", help="git range, e.g. base..head")
    mode.add_argument("--working", action="store_true", help="diff the working tree vs HEAD")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the markdown line")
    args = ap.parse_args(argv)

    env = compute(Path(args.workdir).resolve(), range_spec=args.range_spec, working=args.working)
    if args.json:
        print(json.dumps(env, indent=2, sort_keys=True))
    else:
        print(to_markdown(env))
    return 0  # fail-open: always 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
