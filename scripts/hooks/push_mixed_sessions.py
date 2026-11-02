#!/usr/bin/env python3
"""Warn when a push carries commits from more than one agent session.

`git push` ships every commit waiting on the branch, not just the pushing
session's own. Named failure (2026-11-01, build-loop): a session's commit was
blocked by a pre-commit gate, its push ran anyway, and 14 commits from two
other Claude sessions plus 3 untrailered commits went to origin/main unreviewed;
the review that followed found 11 defects in them.

Outgoing commits are grouped by their `Claude-Session:` trailer; commits with
no trailer form one "no trailer" group (a missing trailer identifies nothing).
More than one group means the pusher is shipping work it did not make. One
group stays silent. WARN-ONLY: prints a systemMessage envelope and never
blocks. Every evaluated push is logged so precision can be measured before any
move to blocking.

Usage: push_mixed_sessions.py --repo <path> --range <upstream>..HEAD
Fail-open: any error prints nothing and exits 0.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_TRAILER_RE = re.compile(r"^Claude-Session:\s*(\S+)\s*$", re.MULTILINE)
NO_TRAILER = "no trailer"
MAX_SHOWN = 5


def _log_path() -> Path:
    override = os.environ.get("BUILD_LOOP_PUSH_SESSIONS_LOG")
    if override:
        return Path(override)
    return Path.home() / ".build-loop" / "telemetry" / "push-mixed-sessions.jsonl"


def outgoing_groups(repo: str, rng: str) -> dict[str, list[tuple[str, str]]]:
    out = subprocess.run(
        ["git", "-C", repo, "log", "--format=%h%x1f%s%x1f%B%x1e", rng],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    groups: dict[str, list[tuple[str, str]]] = {}
    for rec in filter(str.strip, out.split("\x1e")):
        sha, subject, body = (rec.strip("\n").split("\x1f") + ["", ""])[:3]
        found = _TRAILER_RE.findall(body)
        key = found[-1].rsplit("/", 1)[-1] if found else NO_TRAILER
        groups.setdefault(key, []).append((sha, subject))
    return groups


def message(groups: dict[str, list[tuple[str, str]]]) -> str:
    total = sum(len(v) for v in groups.values())
    lines = [
        f"[build-loop] This push sends {total} commits from {len(groups)} sources. "
        "git pushes every commit waiting on the branch, not only this session's. "
        "Confirm the commits you did not make were reviewed (warn-only):"
    ]
    for key, commits in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        shown = ", ".join(f"{s} {subj[:50]}" for s, subj in commits[:MAX_SHOWN])
        more = f", +{len(commits) - MAX_SHOWN} more" if len(commits) > MAX_SHOWN else ""
        lines.append(f"  - {key} ({len(commits)}): {shown}{more}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--range", required=True, dest="rng")
    args = ap.parse_args()
    try:
        groups = outgoing_groups(args.repo, args.rng)
    except Exception:  # noqa: BLE001 — never wedge a push over an advisory
        return 0
    fired = len(groups) > 1
    try:
        log = _log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "repo": args.repo, "range": args.rng, "fired": fired,
                "groups": {k: len(v) for k, v in groups.items()},
            }) + "\n")
    except OSError:
        pass
    if fired:
        print(json.dumps({"systemMessage": message(groups)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
