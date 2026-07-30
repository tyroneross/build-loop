#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""TAXONOMY.md loader + tag validation.

Loads the small slice of `.semantic/TAXONOMY.md` the writer needs (decision
tags + source attribution), falling back to conservative defaults so the
writer still works in a fresh tree. Behaviour is identical to the historical
flat-module `load_taxonomy`/`validate_tags`.
"""
from __future__ import annotations

import re
from pathlib import Path

from constants import VALID_SOURCES, VALID_STATUSES


def _parse_section_bullets(
    lines: list[str],
    *,
    is_start: "callable",  # type: ignore[valid-type]
) -> set[str]:
    """Collect ``- `name``` bullet identifiers from one ``##`` section.

    ``is_start(line)`` decides where the section begins; the section ends at the
    next ``## `` heading. Mirrors the per-section scan the flat module ran
    twice (once for tags, once for sources).
    """
    found: set[str] = set()
    in_section = False
    for line in lines:
        if is_start(line):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            m = re.match(r"^- `([a-z][a-z0-9-]*)`", line)
            if m:
                found.add(m.group(1))
    return found


def load_taxonomy(workdir: Path) -> dict[str, set[str]]:
    """Return {tags, primary_tags, confidences, sources, statuses}.

    Reads `.semantic/TAXONOMY.md` if present. Falls back to conservative
    defaults so the writer still works in a fresh tree (the test fixture
    seeds its own TAXONOMY).
    """
    tax_path = workdir / ".semantic" / "TAXONOMY.md"
    defaults = {
        "tags": {
            "architecture",
            "data",
            "ui",
            "infra",
            "tooling",
            "process",
            "security",
            "performance",
            "testing",
        },
        "primary_tags": {
            "architecture",
            "data",
            "ui",
            "infra",
            "tooling",
            "process",
            "security",
            "performance",
            "testing",
        },
        "sources": set(VALID_SOURCES),
        "statuses": set(VALID_STATUSES),
    }
    if not tax_path.exists():
        return defaults
    text = tax_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Parse the bullet items in §1 (Decision tags).
    tags = _parse_section_bullets(
        lines,
        is_start=lambda line: line.startswith("## 1.")
        or line.lower().startswith("## 1. decision tags"),
    )
    if tags:
        defaults["tags"] = tags
        defaults["primary_tags"] = set(tags)
    # Parse §6 (Source attribution) for sources.
    sources = _parse_section_bullets(
        lines,
        is_start=lambda line: line.startswith("## 6.") or "Source attribution" in line,
    )
    if sources:
        defaults["sources"] = sources
    return defaults


def validate_tags(tags: list[str], primary_tag: str, taxonomy: dict[str, set[str]]) -> None:
    if primary_tag not in taxonomy["primary_tags"]:
        raise ValueError(
            f"primary_tag {primary_tag!r} not in taxonomy. Allowed: {sorted(taxonomy['primary_tags'])}"
        )
    for t in tags:
        if t.startswith("proposed:"):
            continue
        if t not in taxonomy["tags"]:
            raise ValueError(
                f"tag {t!r} not in taxonomy and not prefixed `proposed:`. Allowed: {sorted(taxonomy['tags'])}"
            )
