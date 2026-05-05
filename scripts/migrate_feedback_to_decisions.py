#!/usr/bin/env python3
"""One-shot migrate `.build-loop/feedback.md` → `.episodic/decisions/`.

Each line of feedback.md follows the date-stamped pattern:
  YYYY-MM-DD | <title> | <body...>

For each line, write a MADR with:
  confidence: confirmed         (these are post-hoc lessons that landed)
  source: migration
  primary_tag: heuristically inferred from body keywords (defaults to `process`)
  entity: build-loop

Idempotent at the line level: re-running won't duplicate (it skips lines whose
title slug + date already exist as a decision).

Exit codes: 0 success, 1 validation error, 2 filesystem.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from write_decision import slugify, list_decisions, parse_frontmatter  # type: ignore  # noqa: E402

# Lightweight keyword → primary_tag mapping. We pick the first match.
TAG_HINTS = [
    ("testing", "testing"),
    ("test", "testing"),
    ("hook", "tooling"),
    ("script", "tooling"),
    ("infra", "infra"),
    ("deploy", "infra"),
    ("ci", "infra"),
    ("performance", "performance"),
    ("latency", "performance"),
    ("budget", "performance"),
    ("security", "security"),
    ("auth", "security"),
    ("schema", "data"),
    ("database", "data"),
    ("migration", "data"),
    ("ui", "ui"),
    ("design", "ui"),
    ("component", "ui"),
    ("architecture", "architecture"),
    ("phase", "process"),
    ("review", "process"),
    ("orchestrator", "process"),
    ("workflow", "process"),
]


def infer_primary_tag(title: str, body: str) -> str:
    blob = (title + " " + body).lower()
    for needle, tag in TAG_HINTS:
        if needle in blob:
            return tag
    return "process"


def parse_feedback_lines(text: str) -> list[tuple[str, str, str]]:
    """Return list of (date, title, body) tuples."""
    out: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        # Format: YYYY-MM-DD | title | body...
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+?)\s*\|\s*(.*)$", line)
        if not m:
            continue
        date, title, body = m.group(1), m.group(2).strip(), m.group(3).strip()
        out.append((date, title, body))
    return out


def already_migrated(decisions_dir: Path, date: str, title: str) -> bool:
    target_slug = slugify(title)
    for f in list_decisions(decisions_dir):
        # Filename: NNNN-YYYY-MM-DD-slug.md
        m = re.match(r"^\d{4}-(\d{4}-\d{2}-\d{2})-(.+)\.md$", f.name)
        if not m:
            continue
        if m.group(1) == date and m.group(2) == target_slug:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Migrate feedback.md → MADR files")
    p.add_argument("--workdir", default=".", help="Project root")
    p.add_argument(
        "--feedback",
        default=".build-loop/feedback.md",
        help="Feedback file path (relative to workdir or absolute)",
    )
    p.add_argument("--no-db", action="store_true", help="Pass --no-db to write_decision (default ON for migrations)")
    p.add_argument("--db", action="store_true", help="Enable DB dual-write during migration")
    args = p.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    fb = Path(args.feedback)
    if not fb.is_absolute():
        fb = workdir / fb
    if not fb.exists():
        print(f"validation error: feedback file not found at {fb}", file=sys.stderr)
        return 1

    decisions_dir = workdir / ".episodic" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)

    entries = parse_feedback_lines(fb.read_text(encoding="utf-8"))
    if not entries:
        print(f"validation error: no parseable feedback entries in {fb}", file=sys.stderr)
        return 1

    written = 0
    skipped = 0
    write_decision_path = HERE / "write_decision.py"

    db_flag = "--db" if args.db else "--no-db"

    for date, title, body in entries:
        if already_migrated(decisions_dir, date, title):
            print(f"skip: already migrated {date} | {title!r}", file=sys.stderr)
            skipped += 1
            continue

        primary_tag = infer_primary_tag(title, body)
        # Migrated lessons are independent post-hoc records, not competing
        # decisions about a single topic. Use the slug as the entity so each
        # entry has a unique topic identity (primary_tag + entity).
        entity_slug = slugify(title) or f"feedback-{date}"
        cmd = [
            sys.executable,
            str(write_decision_path),
            "--workdir", str(workdir),
            "--title", title,
            "--decision", body or title,  # body becomes the "decision" body
            "--context", "Migrated from .build-loop/feedback.md (post-hoc lesson, not a forward-looking choice).",
            "--consequences", "Carried forward as a confirmed lesson; consult before re-litigating the same area.",
            "--tags", f"{primary_tag},process",
            "--primary-tag", primary_tag,
            "--entity", f"build-loop:{entity_slug}",
            "--confidence", "confirmed",
            "--status", "accepted",
            "--source", "migration",
            "--date", date,
            db_flag,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"validation error: write_decision failed for {date} | {title!r}: {result.stderr}", file=sys.stderr)
            return 1
        new_id = result.stdout.strip()
        print(f"migrated: {new_id} | {date} | {title!r}", file=sys.stderr)
        written += 1

    print(f"migrate_feedback_to_decisions: wrote {written}, skipped {skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
