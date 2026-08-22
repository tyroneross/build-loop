#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""memory_receipt_report.py — is the memory receipt safe to enforce yet?

The gate ships warn-only because a noisy gate is worse than no gate. Its
trigger was calibrated on a HISTORICAL replay (112 commits, 59% -> 24%), which
is a proxy: it reconstructs what would have fired. This reads the LIVE ledger
that `audit_before_commit.py` appends to on every real commit and reports what
actually fired.

Two numbers decide enforcement:

  fire rate        how often the receipt is required at all. Too high and the
                   packet becomes wallpaper.
  satisfaction     of the commits where it was required, how many recorded a
                   read AND a write. This starts near zero by construction --
                   nobody was writing memory before the gate existed -- so a
                   RISING satisfaction rate is the signal that the habit has
                   formed and enforcement will not simply block everything.

Enforcing while satisfaction is ~0 converts a warning into a wall. The
recommendation below refuses on that basis, not on fire rate alone.

Stdlib only. Read-only. Python 3.11+.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from collections import Counter
from pathlib import Path

# Enough commits that a rate is not one bad afternoon.
MIN_SAMPLE = 20
# Above this, the packet is background noise rather than a signal.
MAX_HEALTHY_FIRE_RATE = 0.35
# Below this, enforcement blocks work instead of prompting it.
MIN_SATISFACTION_TO_ENFORCE = 0.60


def ledger_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "build-loop" / "memory-receipt-ledger.jsonl"


def load(path: Path, days: int | None) -> list[dict]:
    if not path.is_file():
        return []
    cutoff = None
    if days:
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if cutoff is not None:
            try:
                ts = _dt.datetime.strptime(row.get("ts", ""), "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=_dt.timezone.utc
                )
            except ValueError:
                continue
            if ts < cutoff:
                continue
        rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    required = [r for r in rows if r.get("required")]
    satisfied = [r for r in required if r.get("satisfied")]
    read_only = [r for r in required if r.get("read") and not r.get("write")]
    fire_rate = (len(required) / total) if total else 0.0
    satisfaction = (len(satisfied) / len(required)) if required else 0.0

    reasons: list[str] = []
    if total < MIN_SAMPLE:
        reasons.append(f"only {total} commits observed; need >= {MIN_SAMPLE}")
    if fire_rate > MAX_HEALTHY_FIRE_RATE:
        reasons.append(
            f"fires on {fire_rate:.0%} of commits (> {MAX_HEALTHY_FIRE_RATE:.0%}); "
            "narrow the trigger before enforcing"
        )
    if satisfaction < MIN_SATISFACTION_TO_ENFORCE:
        reasons.append(
            f"only {satisfaction:.0%} of required commits are satisfied "
            f"(< {MIN_SATISFACTION_TO_ENFORCE:.0%}); enforcing now blocks work "
            "instead of prompting it"
        )

    return {
        "commits_observed": total,
        "receipt_required": len(required),
        "fire_rate": round(fire_rate, 4),
        "satisfied": len(satisfied),
        "satisfaction_rate": round(satisfaction, 4),
        "read_but_no_write": len(read_only),
        "top_triggering_paths": Counter(
            h for r in required for h in (r.get("lane_hits") or [])
        ).most_common(8),
        "repos": Counter(r.get("repo") for r in rows).most_common(8),
        "safe_to_enforce": not reasons,
        "blockers": reasons,
    }


def render(summary: dict, path: Path) -> str:
    if not summary["commits_observed"]:
        return (
            f"No observations yet at {path}.\n"
            "The ledger fills as commits land in any repo whose pre-commit hook "
            "runs audit_before_commit.py.\n"
        )
    lines = [
        "## Memory receipt — live measurement",
        "",
        f"- commits observed: **{summary['commits_observed']}**",
        f"- receipt required: **{summary['receipt_required']}** "
        f"({summary['fire_rate']:.0%} of commits)",
        f"- satisfied (read AND write): **{summary['satisfied']}** "
        f"({summary['satisfaction_rate']:.0%} of required)",
        f"- read but never wrote: **{summary['read_but_no_write']}**",
        "",
    ]
    if summary["top_triggering_paths"]:
        lines.append("Triggering paths:")
        lines += [f"  - {p} × {n}" for p, n in summary["top_triggering_paths"]]
        lines.append("")
    if summary["safe_to_enforce"]:
        lines += [
            "**Safe to enforce.** Set `BUILDLOOP_ENFORCE_MEMORY=1` or "
            "`sessionPrefs.enforceMemoryReceipt: true`.",
        ]
    else:
        lines.append("**Not safe to enforce yet:**")
        lines += [f"  - {r}" for r in summary["blockers"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, help="only count observations from the last N days")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ledger", type=Path, help="override the ledger path")
    args = parser.parse_args(argv)

    path = args.ledger or ledger_path()
    summary = summarize(load(path, args.days))
    print(json.dumps(summary, indent=2) if args.json else render(summary, path), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
