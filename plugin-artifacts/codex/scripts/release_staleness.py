#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Detect the failure mode "a week passed with merged commits and no release was cut".

WHY THIS EXISTS. Nothing in this repo could observe its own release cadence. Every
publish workflow triggered on `release: types: [published]`, nothing ever created a
release, and so main reached 0.42.5 while the newest tag and the newest GitHub
release both sat at v0.36.4 — 416 commits and roughly two months behind, with no
check anywhere that would say so. Automating the cut (release-please plus a weekly
merge) fixes the cause; this fixes the blindness. An automation that silently stops
working is worse than a manual step somebody remembers to do.

WHAT COUNTS AS STALE. Age alone is not staleness: a quiet week SHOULD produce no
release, and firing on that would train the reader to ignore the signal. Stale means
BOTH that shippable work is sitting unreleased AND that it has sat too long:

    stale  <=>  commits_since_tag > 0  AND  (age_days > max_age_days
                                             OR commits_since_tag >= max_commits)

The commit ceiling is the second arm because a burst of 200 commits inside the
window is also a cadence failure, just a faster one. No release tag at all is stale
by definition once any commit exists.

Release tags are `v<semver>`. `archive/*` and `rescue/*` tags are housekeeping
markers, not releases, and are excluded — counting them would have reported this
repo as fresh throughout the entire two-month outage.

Exit codes: 0 fresh, 1 stale, 2 the check itself could not run (no git, not a repo).
Pure stdlib. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

#: A release tag, and only a release tag. Anchored so `archive/2026-08-25/...` and
#: `rescue/peer-b0ad360` cannot match — both exist in this repo's tag list.
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

DEFAULT_MAX_AGE_DAYS = 10
DEFAULT_MAX_COMMITS = 50


class CheckError(RuntimeError):
    """The check could not run — distinct from a stale verdict."""


def _git(workdir: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", workdir, *args],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise CheckError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def newest_release_tag(workdir: str) -> str | None:
    """Newest `v<semver>` tag by semver order, not by creation date.

    Creation date lies after a backfill: a v0.36.4 tag re-created today would
    outrank a genuinely newer v0.42.5. Version order is the durable key.
    """
    try:
        raw = _git(workdir, "tag", "--list", "v*")
    except CheckError:
        return None
    best: tuple[tuple[int, int, int], str] | None = None
    for line in raw.splitlines():
        m = RELEASE_TAG.match(line.strip())
        if not m:
            continue
        key = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if best is None or key > best[0]:
            best = (key, line.strip())
    return best[1] if best else None


def evaluate(
    workdir: str,
    branch: str = "HEAD",
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_commits: int = DEFAULT_MAX_COMMITS,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    tag = newest_release_tag(workdir)

    if tag is None:
        total = int(_git(workdir, "rev-list", "--count", branch) or "0")
        return {
            "stale": total > 0,
            "tag": None,
            "commits_since_tag": total,
            "age_days": None,
            "max_age_days": max_age_days,
            "max_commits": max_commits,
            "reason": (
                f"no release tag exists and {total} commit(s) are on {branch}"
                if total else "no release tag and no commits — nothing to release"
            ),
        }

    commits = int(_git(workdir, "rev-list", "--count", f"{tag}..{branch}") or "0")
    tagged_at = datetime.fromisoformat(_git(workdir, "log", "-1", "--format=%cI", tag))
    age_days = (now - tagged_at).total_seconds() / 86400.0

    if commits == 0:
        reason = f"{tag} is the head of {branch} — nothing has landed since the last release"
    elif age_days > max_age_days:
        reason = (
            f"{commits} commit(s) have sat unreleased for {age_days:.1f} days "
            f"since {tag} (limit {max_age_days} days)"
        )
    elif commits >= max_commits:
        reason = (
            f"{commits} commit(s) unreleased since {tag} — at or over the "
            f"{max_commits}-commit ceiling even though only {age_days:.1f} days have passed"
        )
    else:
        reason = (
            f"{commits} commit(s) queued since {tag}, {age_days:.1f} days old — "
            f"inside the {max_age_days}-day / {max_commits}-commit window"
        )

    return {
        "stale": commits > 0 and (age_days > max_age_days or commits >= max_commits),
        "tag": tag,
        "commits_since_tag": commits,
        "age_days": round(age_days, 2),
        "max_age_days": max_age_days,
        "max_commits": max_commits,
        "reason": reason,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--branch", default="HEAD")
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    ap.add_argument("--max-commits", type=int, default=DEFAULT_MAX_COMMITS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        result = evaluate(
            args.workdir, args.branch, args.max_age_days, args.max_commits
        )
    except CheckError as exc:
        print(f"release_staleness: cannot run — {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{'STALE' if result['stale'] else 'FRESH'} — {result['reason']}")
    return 1 if result["stale"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
