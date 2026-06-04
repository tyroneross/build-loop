# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""CLI entry point: ``python3 -m scripts.worktree_reaper``.

Also runnable as ``python3 scripts/worktree_reaper/__main__.py``.

Exit codes:
  0 — completed (including dry-run; errors[] may be non-empty)
  2 — invalid arguments

Hard rule: this CLI never fails the parent process for a per-folder error.
A leaked worktree is already a problem; a reaper that exits non-zero would
turn a recoverable leak into a cron-disrupting outage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add this package's parent (scripts/) so the package-relative import works
# whether the user runs `python3 -m worktree_reaper` from scripts/ or invokes
# `__main__.py` directly.
_PKG_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _PKG_DIR.parent
for _d in (str(_PKG_DIR), str(_SCRIPTS_DIR)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from worktree_reaper.reaper import (  # type: ignore  # noqa: E402
    DEFAULT_MIN_AGE_HOURS,
    reap_worktrees,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Reap leaked build-loop run worktrees under .build-loop/worktrees/. "
            "Bundle-then-remove is always-on for safety. Active runs and recent "
            "(< --min-age-hours) folders are protected."
        )
    )
    p.add_argument(
        "--workdir",
        default=".",
        help="Repository root containing .build-loop/worktrees/ (default: .).",
    )
    p.add_argument(
        "--min-age-hours",
        type=float,
        default=DEFAULT_MIN_AGE_HOURS,
        help=(
            "Skip worktrees younger than this many hours. Default "
            f"{DEFAULT_MIN_AGE_HOURS}h — protects active iterations while "
            "still catching crashed runs from the same day."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify everything but perform no destructive actions.",
    )
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print the result as JSON to stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    workdir = Path(args.workdir).resolve()

    result = reap_worktrees(
        workdir,
        min_age_hours=args.min_age_hours,
        dry_run=args.dry_run,
    )

    dr_tag = " [DRY RUN]" if result.dry_run else ""
    summary = (
        f"worktree_reaper{dr_tag} "
        f"bundled_and_removed={len(result.bundled_and_removed)} "
        f"removed_orphan={len(result.removed_orphan)} "
        f"skipped_active={len(result.skipped_active)} "
        f"skipped_too_young={len(result.skipped_too_young)} "
        f"errors={len(result.errors)}"
    )
    print(summary, file=sys.stderr)

    if args.json_output:
        print(json.dumps(result.to_dict(), indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
