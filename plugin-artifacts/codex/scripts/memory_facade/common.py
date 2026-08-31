#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for memory_facade sub-modules.

Stdlib only. Imported by every memory_facade/*.py module via relative import.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Frontmatter regex used by both decisions and lessons backends.
DECISION_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_LESSON_FRONTMATTER_RE = DECISION_FRONTMATTER_RE


def _parse_iso(ts: Any) -> Optional[float]:
    """Best-effort parse of an ISO-8601 timestamp into a float (Unix seconds)."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return ts / 1000.0 if ts > 1e12 else float(ts)
    if not isinstance(ts, str):
        return None
    s = ts.strip().rstrip("Z")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, AttributeError):
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, AttributeError):
            return None


def _legacy_q_match(text: str, query: str) -> bool:
    """Preserve the original token-OR substring recall filter."""
    if not query:
        return True
    haystack = (text or "").lower()
    tokens = [t for t in query.lower().split() if t]
    if not tokens:
        return True
    return any(t in haystack for t in tokens)


def _q_match(text: str, query: str, *, mode: Optional[str] = None) -> bool:
    """Case-insensitive token-OR match. Empty query matches everything.

    A query is split into whitespace tokens; the text matches if it contains
    ANY token. The previous full-phrase substring test (`query in text`) silently
    dropped every result for realistic multi-word goal queries — a natural-language
    goal like "background snapshot handoff context" never appears verbatim in a
    decision's id+title+tags, so canonical recall returned 0 on every real run
    (audit 2026-05-31). Token-OR is the minimal fix that makes recall actually
    surface stored decisions/lessons; the merge layer ranks/dedups downstream.

    ``BUILD_LOOP_MEMORY_MATCH=boundary`` opts into the ranker's word-boundary
    matcher. All other values preserve the legacy filter so recall cannot fail
    closed if the optional boundary path has an internal error.
    """
    legacy_result = _legacy_q_match(text, query)
    try:
        selected_mode = mode if mode is not None else os.environ.get(
            "BUILD_LOOP_MEMORY_MATCH", "legacy"
        )
        if selected_mode != "boundary":
            return legacy_result

        # Keep candidate filtering aligned with the ranker's query and word
        # matching rules. Import lazily so a ranker import failure cannot break
        # the hot recall path.
        from memory_rank import MIN_TOKEN_LEN, STOPWORDS, _WORD_RE, _term_hit, tokenize

        terms = tokenize(query)
        if not terms:
            raw_terms = _WORD_RE.findall((query or "").lower())
            # Stopword-only queries retain the established match-all contract.
            # A short non-stopword (for example, ``ai``) is deliberately a
            # non-match: allowing it through would reintroduce substring
            # accidents such as ``ai`` matching ``main``.
            return not any(
                len(term) < MIN_TOKEN_LEN and term not in STOPWORDS
                for term in raw_terms
            )
        words = set(tokenize(text))
        return any(_term_hit(term, words) for term in terms)
    except Exception:
        return legacy_result


def _read_jsonl(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    rows: List[Dict[str, Any]] = []
    reasons: List[str] = []
    if not path.is_file():
        return rows, reasons
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return rows, [f"index_read_error: {path.name} {e}"]
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as e:
            reasons.append(f"index_parse_error: {path.name}:{lineno} {e}")
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows, reasons
