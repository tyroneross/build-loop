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
    VALID_CONFIDENCE_SOURCES,
    VALID_CONFIDENCES,
    VALID_DOMAINS,
    VALID_GOALS,
    VALID_SOURCES,
    VALID_STATUSES,
    VALID_TASK_CATEGORIES,
    VALID_TOOLS,
    VALID_TYPES,
    load_taxonomy,
    parse_frontmatter,
)

REQUIRED_DECISION_KEYS = [
    # v1 base
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
    # v2 metadata (design §15) — defaults applied at write time
    "project",
    "tool",
    "model",
    "task_category",
    "author",
]

# v2 optional fields (informational; values may be null/[]).
OPTIONAL_V2_KEYS = [
    "last_validated",
    "last_accessed",
    "files_touched",
    "closing_commit",
]

# v3 optional fields (informational; design §16). Validator only enforces
# shape when present, so pre-migration files don't fail validation.
OPTIONAL_V3_KEYS = [
    "confidence_source",
    "confirmation_count",
    "valid_until",
    "causal_parent_id",
    "embedding_model_version",
    "domain",
    "goal",
]

# ISO-date-or-null pattern; allows full ISO-8601 with or without time.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:?\d{2})?)?$")


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

    # v2 metadata (design §15)
    tool = fm.get("tool")
    if tool is not None and tool not in VALID_TOOLS:
        errors.append(f"{path}: tool={tool!r} not in {sorted(VALID_TOOLS)}")
    tc = fm.get("task_category")
    if tc is not None and tc not in VALID_TASK_CATEGORIES:
        errors.append(f"{path}: task_category={tc!r} not in {sorted(VALID_TASK_CATEGORIES)}")
    for sf in ("project", "model", "author"):
        sv = fm.get(sf)
        if sv is not None and (not isinstance(sv, str) or not sv.strip()):
            errors.append(f"{path}: {sf} must be a non-empty string, got {sv!r}")
    ft = fm.get("files_touched")
    if ft is not None and not isinstance(ft, list):
        errors.append(f"{path}: files_touched must be a list, got {type(ft).__name__}")
    elif isinstance(ft, list):
        for p in ft:
            if not isinstance(p, str):
                errors.append(f"{path}: files_touched item {p!r} must be string")
    for df in ("last_validated", "last_accessed"):
        dv = fm.get(df)
        if dv not in (None, "null", "") and not _ISO_DATE_RE.match(str(dv)):
            errors.append(f"{path}: {df} must be ISO date or null, got {dv!r}")

    # v3 metadata (design §16). Treat as informational when entirely absent
    # (pre-migration files) but enforce shape when present.
    cs = fm.get("confidence_source")
    if cs is not None and cs not in VALID_CONFIDENCE_SOURCES:
        errors.append(
            f"{path}: confidence_source={cs!r} not in {sorted(VALID_CONFIDENCE_SOURCES)}"
        )
    cc = fm.get("confirmation_count")
    if cc is not None:
        # YAML-tiny parser may return int or string.
        try:
            cc_int = int(cc)
        except (TypeError, ValueError):
            errors.append(f"{path}: confirmation_count must be int >= 0, got {cc!r}")
        else:
            if cc_int < 0:
                errors.append(f"{path}: confirmation_count must be >= 0, got {cc_int}")
    vu = fm.get("valid_until")
    if vu not in (None, "null", "") and not _ISO_DATE_RE.match(str(vu)):
        errors.append(f"{path}: valid_until must be ISO date or null, got {vu!r}")
    cp = fm.get("causal_parent_id")
    if cp not in (None, "null", "") and not (isinstance(cp, str) and cp.strip()):
        errors.append(f"{path}: causal_parent_id must be a non-empty string or null, got {cp!r}")
    emv = fm.get("embedding_model_version")
    if emv is not None and (not isinstance(emv, str) or not emv.strip()):
        errors.append(
            f"{path}: embedding_model_version must be a non-empty string, got {emv!r}"
        )
    dom = fm.get("domain")
    if dom is not None and dom not in VALID_DOMAINS:
        errors.append(f"{path}: domain={dom!r} not in {sorted(VALID_DOMAINS)}")
    gl = fm.get("goal")
    if gl is not None and gl not in VALID_GOALS:
        errors.append(f"{path}: goal={gl!r} not in {sorted(VALID_GOALS)}")

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
