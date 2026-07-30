# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""CLI entry point for report-only worktree discovery and explicit delegation.

Both forms are supported:
  python3 -m scripts.worktree_reaper
  python3 scripts/worktree_reaper/__main__.py

Exit codes:
  0 — report completed, or explicit act completed without errors
  1 — explicit act lacked owner release or finalization reported errors
  2 — invalid arguments
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _PKG_DIR.parent
_REPO_DIR = _SCRIPTS_DIR.parent
for _d in (str(_REPO_DIR), str(_SCRIPTS_DIR), str(_PKG_DIR)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from worktree_reaper.reaper import (  # type: ignore  # noqa: E402
    DEFAULT_MIN_AGE_HOURS,
    reap_worktrees,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report attributable stale run worktrees. Mutation is opt-in and "
            "delegates to collapse_run only after explicit owner release."
        )
    )
    parser.add_argument(
        "--workdir",
        default=".",
        help="Repository root containing .build-loop/worktrees (default: .)",
    )
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=DEFAULT_MIN_AGE_HOURS,
        help=f"Skip worktrees younger than this (default: {DEFAULT_MIN_AGE_HOURS}h)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit report-only mode (also the default when --act is absent)",
    )
    parser.add_argument(
        "--act",
        action="store_true",
        help="Delegate eligible candidates to strict collapse_run finalization",
    )
    parser.add_argument(
        "--owner-released",
        action="store_true",
        help="Positive authority that each selected worktree owner released it",
    )
    parser.add_argument("--json", dest="json_output", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = reap_worktrees(
        Path(args.workdir).resolve(),
        min_age_hours=args.min_age_hours,
        dry_run=(args.dry_run or not args.act),
        act=args.act,
        owner_released=args.owner_released,
    )

    tag = " [REPORT-ONLY]" if result.dry_run else " [ACT]"
    print(
        f"worktree_reaper{tag} "
        f"candidates={len(result.candidates)} "
        f"finalized={len(result.bundled_and_removed)} "
        f"skipped_active={len(result.skipped_active)} "
        f"skipped_unattributed={len(result.skipped_unattributed)} "
        f"skipped_unmerged={len(result.skipped_unmerged)} "
        f"errors={len(result.errors)}",
        file=sys.stderr,
    )
    if args.json_output:
        print(json.dumps(result.to_dict(), indent=2))

    if args.act and (not args.owner_released or result.errors):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
