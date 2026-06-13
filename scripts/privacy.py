#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""privacy.py — reusable secret/PII deny-pattern scanner.

The canonical pattern list lives in templates/memory/manifest.json privacy.deny_patterns
(the memory public-seed allowlist). Extension checks reuse it so the two surfaces
never drift. Pure stdlib.
"""
from __future__ import annotations
import json, re
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_MANIFEST = HERE.parent / "templates" / "memory" / "manifest.json"


def load_default_patterns(manifest_path: Path | None = None) -> list[str]:
    path = manifest_path or _MANIFEST
    data = json.loads(path.read_text())
    pats = data.get("privacy", {}).get("deny_patterns", [])
    return [p for p in pats if isinstance(p, str)]


def scan_text(text: str, patterns: list[str]) -> list[dict[str, Any]]:
    """Return [{pattern, match}] for every deny-pattern hit. Invalid regexes are skipped."""
    hits: list[dict[str, Any]] = []
    for pat in patterns:
        try:
            rx = re.compile(pat)
        except re.error:
            continue
        for m in rx.finditer(text):
            hits.append({"pattern": pat, "match": m.group(0)})
    return hits


def scan_file(path: Path, patterns: list[str] | None = None) -> list[dict[str, Any]]:
    pats = patterns if patterns is not None else load_default_patterns()
    return scan_text(path.read_text(errors="ignore"), pats)
