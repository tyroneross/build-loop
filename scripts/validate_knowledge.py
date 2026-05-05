#!/usr/bin/env python3
"""Validate the `.episodic/` + `.semantic/` + `.procedural/` tree.

Checks per file:
  - frontmatter parseable
  - required keys present, types right
  - `tags` and `primary_tag` against TAXONOMY (proposed: prefix allowed for tags)
  - `confidence`, `status`, `source`, `type` against vocabulary
  - `supersedes`/`superseded_by` links resolve to existing decisions

Exit codes:
  0 - all files pass
  1 - validation error (one or more files failed)
  2 - filesystem error
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from write_decision import (  # type: ignore  # noqa: E402
    CONFIDENCE_ORDER,
    VALID_CONFIDENCES,
    VALID_SOURCES,
    VALID_STATUSES,
    VALID_TYPES,
    load_taxonomy,
    parse_frontmatter,
)

REQUIRED_DECISION_KEYS = [
    "id",
    "slug",
    "title",
    "type",
    "status",
    "confidence",
    "date",
    "tags",
    "primary_tag",
    "entity",
    "source",
]


def collect_decision_files(workdir: Path) -> list[Path]:
    files: list[Path] = []
    decisions_dir = workdir / ".episodic" / "decisions"
    if decisions_dir.exists():
        files.extend(sorted(decisions_dir.glob("[0-9][0-9][0-9][0-9]-*.md")))
        history = decisions_dir / "_history"
        if history.exists():
            files.extend(sorted(history.glob("*.md")))
    return files


def validate_decision_file(
    path: Path,
    taxonomy: dict[str, set[str]],
    known_ids: set[str],
) -> list[str]:
    """Return list of error messages; empty if valid."""
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if fm is None:
        return [f"{path}: missing or unparseable frontmatter"]

    for k in REQUIRED_DECISION_KEYS:
        if k not in fm:
            errors.append(f"{path}: missing required frontmatter key {k!r}")

    if fm.get("type") not in VALID_TYPES:
        errors.append(f"{path}: type={fm.get('type')!r} not in {sorted(VALID_TYPES)}")
    if fm.get("status") not in VALID_STATUSES:
        errors.append(f"{path}: status={fm.get('status')!r} not in {sorted(VALID_STATUSES)}")
    if fm.get("confidence") not in VALID_CONFIDENCES:
        errors.append(f"{path}: confidence={fm.get('confidence')!r} not in {sorted(VALID_CONFIDENCES)}")
    if fm.get("source") not in taxonomy["sources"]:
        errors.append(f"{path}: source={fm.get('source')!r} not in {sorted(taxonomy['sources'])}")

    pt = fm.get("primary_tag")
    if pt and pt not in taxonomy["primary_tags"]:
        errors.append(f"{path}: primary_tag={pt!r} not in {sorted(taxonomy['primary_tags'])}")
    if pt and isinstance(pt, str) and pt.startswith("proposed:"):
        errors.append(f"{path}: primary_tag must not use 'proposed:' prefix")

    tags = fm.get("tags") or []
    if not isinstance(tags, list):
        errors.append(f"{path}: tags must be a list, got {type(tags).__name__}")
    else:
        for t in tags:
            if not isinstance(t, str):
                errors.append(f"{path}: tag {t!r} is not a string")
                continue
            if t.startswith("proposed:"):
                continue
            if t not in taxonomy["tags"]:
                errors.append(f"{path}: tag {t!r} not in vocabulary and not 'proposed:'-prefixed")

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(fm.get("date", ""))):
        errors.append(f"{path}: date must be YYYY-MM-DD, got {fm.get('date')!r}")

    sup = fm.get("supersedes")
    if sup not in (None, "null", ""):
        if str(sup) not in known_ids:
            errors.append(f"{path}: supersedes={sup!r} does not resolve to any known decision id")
    sb = fm.get("superseded_by")
    if sb not in (None, "null", ""):
        if str(sb) not in known_ids:
            errors.append(f"{path}: superseded_by={sb!r} does not resolve to any known decision id")

    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Validate knowledge frontmatter and links")
    p.add_argument("--workdir", default=".", help="Project root")
    p.add_argument("--quiet", action="store_true", help="Suppress per-file 'ok' lines")
    args = p.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    try:
        taxonomy = load_taxonomy(workdir)
    except Exception as e:  # noqa: BLE001
        print(f"validation error: failed to load TAXONOMY: {e}", file=sys.stderr)
        return 1

    files = collect_decision_files(workdir)
    known_ids: set[str] = set()
    for f in files:
        fm = parse_frontmatter(f.read_text(encoding="utf-8")) or {}
        if fm.get("id"):
            known_ids.add(str(fm["id"]))

    total_errors: list[str] = []
    for f in files:
        errs = validate_decision_file(f, taxonomy, known_ids)
        if errs:
            total_errors.extend(errs)
        elif not args.quiet:
            print(f"ok: {f.relative_to(workdir)}", file=sys.stderr)

    if total_errors:
        for e in total_errors:
            print(e, file=sys.stderr)
        print(f"validation error: {len(total_errors)} issue(s) across {len(files)} file(s)", file=sys.stderr)
        return 1

    print(f"validate_knowledge: {len(files)} file(s) ok", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
