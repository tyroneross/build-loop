#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""self_review/__main__.py — deterministic data-gatherer for build-loop periodic self-review.

Mines recent activity for issues + efficiency signals, writes a human digest,
and enqueues candidate improvement items.  Does NO LLM calls and applies NO
code changes.  The host LLM (invoked separately by the cron wrapper) does the
reasoning and applying.

Canonical invocation:
  python3 scripts/self_review/__main__.py --mode {light|deep} [--workdir <repo>]
                                          [--days N] [--dry-run] --json

Also runnable as:
  python3 -m self_review --mode {light|deep} ...   (with scripts/ on sys.path)

Output JSON shape:
  {
    "mode": "light"|"deep",
    "window_days": int,
    "mined": {
      "corrections": [...],
      "rituals": [...],
      "sequences": [...]
    },
    "efficiency_findings": [
      {
        "kind": str,
        "signal": str,
        "evidence": str,
        "suggested_action": str,
        "severity": "HIGH"|"MEDIUM"|"LOW"
      },
      ...
    ],
    "self_simplification": [
      {
        "kind": str,
        "signal": str,
        "evidence": str,
        "suggested_action": str,
        "severity": "HIGH"|"MEDIUM"|"LOW"
      },
      ...
    ],
    "digest_path": str | null,
    "queued": [str, ...],
    "errors": [str, ...],
    "dry_run": bool
  }

  ``self_simplification`` is only populated when the workdir IS the build-loop
  repo itself (self-recursive) AND mode == "deep".  It is always present as a
  list (possibly empty) in the output.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

# When run directly (`python3 scripts/self_review/__main__.py`), sys.path[0]
# is the package directory, so flat sibling imports work.  When imported via
# `python3 -m self_review` with scripts/ on sys.path, __init__.py has already
# inserted the package dir.
from gather import run_miner
from efficiency import scan_state, scan_churn
from selfscan import is_self_recursive, scan_self_simplification
from output import render_digest, write_proposals

# Proposal cap for light mode
_LIGHT_MODE_CAP = 10


def _rank_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by severity descending."""
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(findings, key=lambda f: order.get(f.get("severity", "LOW"), 2))


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--mode",
        required=True,
        choices=["light", "deep"],
        help="light=7-day window, cap 10 proposals; deep=14-day, enqueue all",
    )
    p.add_argument(
        "--workdir",
        default=".",
        help="Project root containing .build-loop/ (default: cwd)",
    )
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override window in days (default: 7 for light, 14 for deep)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute everything but write nothing",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout (always implied; kept for compatibility)",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(argv: list[str]) -> int:
    args = parse_args(argv)

    mode: str = args.mode
    is_deep: bool = mode == "deep"
    default_days = 14 if is_deep else 7
    window_days: int = args.days if args.days is not None else default_days
    dry_run: bool = args.dry_run
    workdir = Path(args.workdir).resolve()

    errors: list[str] = []
    now = dt.datetime.now(dt.timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    created_ts = now.isoformat(timespec="seconds")

    # Step 1: Mine transcripts (fail-soft)
    mined = run_miner(workdir, window_days, errors)

    # Step 2: Efficiency scan
    efficiency_findings: list[dict[str, Any]] = []
    efficiency_findings.extend(scan_state(workdir, window_days, errors))
    efficiency_findings.extend(scan_churn(workdir, window_days, errors))
    efficiency_findings = _rank_findings(efficiency_findings)

    # Step 2b: Self-simplification scan (self-recursive + deep mode only)
    self_simplification: list[dict[str, Any]] = []
    if is_deep and is_self_recursive(workdir):
        self_simplification = scan_self_simplification(workdir, window_days, errors)

    # Step 3 + 4: Write digest + enqueue proposals (unless --dry-run)
    digest_path: str | None = None
    queued_paths: list[str] = []

    if not dry_run:
        proposals_dir = workdir / ".build-loop" / "proposals"
        cap = _LIGHT_MODE_CAP if not is_deep else None
        try:
            queued_paths = write_proposals(
                proposals_dir=proposals_dir,
                date_str=date_str,
                mode=mode,
                efficiency_findings=efficiency_findings,
                self_simplification=self_simplification,
                mined=mined,
                workdir=workdir,
                created_ts=created_ts,
                cap=cap,
            )
        except OSError as exc:
            errors.append(f"proposal write error: {exc}")

        review_dir = workdir / ".build-loop" / "self-review"
        review_dir.mkdir(parents=True, exist_ok=True)
        digest_filename = f"{date_str}-{mode}.md"
        digest_file = review_dir / digest_filename
        try:
            digest_content = render_digest(
                mode=mode,
                window_days=window_days,
                mined=mined,
                efficiency_findings=efficiency_findings,
                queued_paths=queued_paths,
                generated_at=now,
                is_deep=is_deep,
            )
            digest_file.write_text(digest_content)
            digest_path = str(digest_file)
        except OSError as exc:
            errors.append(f"digest write error: {exc}")

    # Step 6: Emit JSON to stdout
    output: dict[str, Any] = {
        "mode": mode,
        "window_days": window_days,
        "mined": {
            "corrections": mined.get("corrections") or [],
            "rituals": mined.get("rituals") or [],
            "sequences": mined.get("sequences") or [],
        },
        "efficiency_findings": efficiency_findings,
        "self_simplification": self_simplification,
        "digest_path": digest_path,
        "queued": queued_paths,
        "errors": errors,
        "dry_run": dry_run,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))

    # Human summary to stderr
    n_findings = len(efficiency_findings)
    n_mined = (
        len(mined.get("corrections") or [])
        + len(mined.get("rituals") or [])
        + len(mined.get("sequences") or [])
    )
    print(
        f"self_review: mode={mode} window={window_days}d "
        f"efficiency_findings={n_findings} mined={n_mined} "
        f"self_simplification={len(self_simplification)} "
        f"queued={len(queued_paths)} errors={len(errors)} "
        f"dry_run={dry_run}",
        file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
