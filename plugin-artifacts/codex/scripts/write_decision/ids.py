#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Decision discovery, slug + id allocation, topic-identity lookup.

Behaviour-identical extraction of the flat module's id/slug helpers. The id
sequence allocation (`next_id`) and canonical-id format are part of the
on-disk filename contract, so they are preserved exactly.
"""
from __future__ import annotations

import re
from pathlib import Path

from frontmatter import parse_frontmatter


def slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:80] or "decision"


def list_decisions(decisions_dir: Path) -> list[Path]:
    if not decisions_dir.exists():
        return []
    out: list[Path] = []
    for path in sorted(decisions_dir.glob("*.md")):
        if path.name.upper().startswith("INDEX") or path.name.startswith("_"):
            continue
        out.append(path)
    return out


def _used_id_from_file(f: Path) -> int | None:
    """Return the integer id a file represents, or None if it has none.

    A leading ``NNNN-`` filename prefix wins; otherwise (for ``.md`` files) the
    frontmatter ``legacy_id``/``id`` is consulted. Mirrors the per-file scan
    body of the historical `next_id`.
    """
    m = re.match(r"^(\d{4})-", f.name)
    if m:
        return int(m.group(1))
    if f.suffix != ".md":
        return None
    try:
        fm = parse_frontmatter(f.read_text(encoding="utf-8")) or {}
    except OSError:
        return None
    raw = str(fm.get("legacy_id") or fm.get("id") or "")
    if re.match(r"^\d{4}$", raw):
        return int(raw)
    return None


def next_id(decisions_dir: Path, history_dir: Path) -> str:
    used: set[int] = set()
    for d in (decisions_dir, history_dir):
        if d.exists():
            for f in d.iterdir():
                got = _used_id_from_file(f)
                if got is not None:
                    used.add(got)
    nxt = (max(used) + 1) if used else 1
    return f"{nxt:04d}"


def canonical_decision_id(project: str, slug: str, date: str, sequence: str) -> str:
    """Return a build-loop-memory canonical decision id."""
    project_slug = slugify(project or "_unscoped")
    seq_int = int(sequence) if sequence.isdigit() else 1
    return f"decision-project-{project_slug}-{slug}-{date.replace('-', '')}-{seq_int:03d}"


def find_same_topic(decisions_dir: Path, primary_tag: str, entity: str) -> "tuple[Path, dict] | None":
    for f in list_decisions(decisions_dir):
        text = f.read_text(encoding="utf-8")
        fm = parse_frontmatter(text) or {}
        if fm.get("primary_tag") == primary_tag and fm.get("entity") == entity:
            return f, fm
    return None
