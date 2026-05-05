#!/usr/bin/env python3
"""Decision rot detector — flag decisions older than threshold-days.

Scans `.episodic/decisions/*.md` (NOT `_history/`, NOT `_review/`).
For each decision: parse frontmatter; compute age from `last_validated`
if present, else fall back to `date`. Emit JSON list of stale entries.

Used by `/knowledge:review` for the rot section.

Output (stdout): JSON array of objects:
  [{"id": "0001", "date": "2026-01-01", "primary_tag": "testing",
    "entity": "build-loop:foo", "age_days": 124,
    "validated_basis": "date" | "last_validated", "path": "..."}, ...]

Exit codes:
  0 success
  1 validation error (bad arg)
  2 filesystem error
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from write_decision import parse_frontmatter, list_decisions  # type: ignore  # noqa: E402


def parse_iso_date(s: str) -> datetime | None:
    """Parse a YYYY-MM-DD or full ISO-8601 timestamp into a tz-aware datetime."""
    if not s:
        return None
    try:
        # YYYY-MM-DD
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        # ISO 8601 with optional Z
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def detect_rot(workdir: Path, threshold_days: int) -> list[dict]:
    decisions_dir = workdir / ".episodic" / "decisions"
    if not decisions_dir.exists():
        return []
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for f in list_decisions(decisions_dir):
        text = f.read_text(encoding="utf-8")
        fm = parse_frontmatter(text) or {}
        if fm.get("status") in ("superseded", "rejected"):
            continue
        last_validated = fm.get("last_validated")
        date_field = fm.get("date")
        basis = "date"
        ts = parse_iso_date(last_validated) if last_validated else None
        if ts is not None:
            basis = "last_validated"
        else:
            ts = parse_iso_date(date_field)
        if ts is None:
            # Unparseable; skip but don't fail the run
            continue
        age = (now - ts).days
        if age >= threshold_days:
            out.append({
                "id": fm.get("id"),
                "date": date_field,
                "last_validated": last_validated,
                "validated_basis": basis,
                "primary_tag": fm.get("primary_tag"),
                "entity": fm.get("entity"),
                "title": fm.get("title"),
                "age_days": age,
                "path": str(f.relative_to(workdir)),
            })
    out.sort(key=lambda r: r["age_days"], reverse=True)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Detect decisions older than threshold (rot detection)")
    p.add_argument("--workdir", default=".", help="Project root containing .episodic/")
    p.add_argument(
        "--threshold-days",
        type=int,
        default=90,
        help="Age in days at or above which a decision is flagged as stale (default 90)",
    )
    args = p.parse_args(argv)

    if args.threshold_days < 0:
        print("validation error: --threshold-days must be >= 0", file=sys.stderr)
        return 1

    workdir = Path(args.workdir).resolve()
    try:
        rows = detect_rot(workdir, args.threshold_days)
    except OSError as e:
        print(f"filesystem error: {e}", file=sys.stderr)
        return 2

    json.dump(rows, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
