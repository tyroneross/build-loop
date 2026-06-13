#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""extensions_check.py — deterministic pre-approval checks for a learned skill."""
from __future__ import annotations
import re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from privacy import scan_file, load_default_patterns  # noqa: E402

NS = re.compile(r"^ext-[a-z0-9]+-")

def _frontmatter(text: str) -> dict | None:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m: return None
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1); fm[k.strip()] = v.strip()
    return fm

def check_skill(skill_md: Path, core_descriptions: list[str]) -> list[dict]:
    """Return [{code, detail}]. Empty = clean. codes: schema|namespace|privacy|trigger-overlap."""
    issues: list[dict] = []
    text = skill_md.read_text(errors="ignore")
    fm = _frontmatter(text)
    if not fm or "name" not in fm or "description" not in fm:
        issues.append({"code": "schema", "detail": "missing/invalid frontmatter (need name + description)"})
        return issues
    if not NS.match(fm["name"]):
        issues.append({"code": "namespace", "detail": f"name must match ext-<slug>-... got {fm['name']!r}"})
    for hit in scan_file(skill_md, load_default_patterns()):
        issues.append({"code": "privacy", "detail": f"deny-pattern hit: {hit['match']!r}"})
    desc_words = set(re.findall(r"[a-z]{4,}", fm["description"].lower()))
    for core in core_descriptions:
        overlap = desc_words & set(re.findall(r"[a-z]{4,}", core.lower()))
        if len(overlap) >= 4:
            issues.append({"code": "trigger-overlap", "detail": f"high description overlap with a core skill: {sorted(overlap)[:6]}"})
            break
    return issues
