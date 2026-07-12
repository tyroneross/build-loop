#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""candidate_aging.py — flag aged, undisposed enforce-from-retro candidates.

Sol audit (2026-07-11, finding 3): the retro pipeline generates enforce-from-retro
candidates for human review but nothing guarantees every candidate reaches a
disposition — the same set can accumulate indefinitely while a plan claims
closure. This is the minimal aging surface: a deterministic report line listing
candidates in ``.build-loop/proposals/enforce-from-retro/`` that are older than N
days AND still undisposed (``status`` absent / open, no checked disposition box).

Scope is deliberately narrow — it flags, nothing more. It does not triage,
promote, reject, or move anything. Surface the report line at SessionStart /
closeout so aged undisposed candidates stop being invisible.

A candidate is DISPOSED when either:
  * frontmatter ``status`` is a terminal value (adopted/rejected/promoted/...), OR
  * its ``## Disposition`` section has a checked box (``- [x] ...``).
Otherwise it is UNDISPOSED. Age = today − frontmatter ``date`` (YYYY-MM-DD),
falling back to file mtime when no date field is present.

Stdlib only. Python 3.11+. Fail-soft: any parse error → treat the file as
undisposed-with-unknown-age (mtime), never raises.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

CANDIDATE_SUBDIR = Path(".build-loop") / "proposals" / "enforce-from-retro"
DEFAULT_OLDER_THAN_DAYS = 14

# Terminal dispositions — a candidate carrying one of these is done, not aging.
TERMINAL_STATUSES = {
    "adopted", "rejected", "promoted", "done", "closed",
    "deferred", "implemented", "resolved", "wontfix", "superseded",
}
# Explicitly open statuses (informational; anything not terminal is treated open).
OPEN_STATUSES = {"", "proposed", "open", "pending", "triaged", "new", "draft"}


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal ``---`` key: value frontmatter parser (stdlib; no yaml dep)."""
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip().lower()] = v.strip()
    return fm


def _has_checked_box(text: str) -> bool:
    for line in text.splitlines():
        s = line.strip().lower()
        if s.startswith("- [x]") or s.startswith("* [x]") or s.startswith("[x]"):
            return True
    return False


def _candidate_date(fm: dict[str, str], path: Path) -> date:
    raw = fm.get("date") or fm.get("recorded_at") or fm.get("generated") or ""
    raw = raw.strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            # Try bare YYYY-MM-DD prefix.
            try:
                return datetime.strptime(raw[:10], "%Y-%m-%d").date()
            except ValueError:
                pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date()
    except OSError:
        return datetime.now(timezone.utc).date()


def _is_disposed(fm: dict[str, str], text: str) -> bool:
    status = (fm.get("status") or "").strip().lower()
    if status in TERMINAL_STATUSES:
        return True
    return _has_checked_box(text)


def scan(
    workdir: Path | str,
    *,
    older_than_days: int = DEFAULT_OLDER_THAN_DAYS,
    now: date | None = None,
) -> dict[str, Any]:
    """Scan the candidate dir. Returns a structured report; never raises."""
    workdir = Path(workdir)
    today = now or datetime.now(timezone.utc).date()
    root = workdir / CANDIDATE_SUBDIR
    aged: list[dict[str, Any]] = []
    total = 0
    undisposed = 0
    try:
        files = sorted(root.glob("*.md")) if root.is_dir() else []
    except OSError:
        files = []
    for f in files:
        total += 1
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        fm = _parse_frontmatter(text)
        if _is_disposed(fm, text):
            continue
        undisposed += 1
        cdate = _candidate_date(fm, f)
        age_days = (today - cdate).days
        if age_days >= older_than_days:
            aged.append({
                "id": fm.get("proposal_id") or f.stem,
                "path": str(f.relative_to(workdir)),
                "age_days": age_days,
                "status": fm.get("status") or "(none)",
            })
    aged.sort(key=lambda c: c["age_days"], reverse=True)
    return {
        "total_candidates": total,
        "undisposed": undisposed,
        "aged_undisposed": aged,
        "older_than_days": older_than_days,
    }


def report_line(result: dict[str, Any]) -> str:
    """One-line summary for SessionStart / closeout."""
    aged = result.get("aged_undisposed") or []
    if not aged:
        return (f"candidate-aging: 0 aged undisposed enforce-from-retro candidates "
                f"(>= {result.get('older_than_days')}d)")
    ids = ", ".join(f"{c['id']} ({c['age_days']}d)" for c in aged[:8])
    more = "" if len(aged) <= 8 else f", +{len(aged) - 8} more"
    return (f"candidate-aging: {len(aged)} aged undisposed enforce-from-retro "
            f"candidate(s) >= {result.get('older_than_days')}d: {ids}{more}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Flag aged undisposed enforce-from-retro candidates.")
    p.add_argument("--workdir", default=os.getcwd())
    p.add_argument("--older-than-days", type=int, default=DEFAULT_OLDER_THAN_DAYS)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    result = scan(Path(os.path.expanduser(args.workdir)), older_than_days=args.older_than_days)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(report_line(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
