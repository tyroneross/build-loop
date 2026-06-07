#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Deterministic capability classification for Phase 1 prior-art injection.

Maps a free-text task intent (e.g. "build semantic search for the docs site")
to one or more **capability tags** (e.g. ``semantic-search``, ``rag``,
``embeddings``). The output is the input contract for ``prior_art.py``: a
list of capability tags + the extracted query terms used for retrieval.

Design (P4 of bl-memory-overhaul-plan):

* **Host-LLM compliant** — no vendor API call. Returns structured data that
  the host coding agent's LLM can refine if it wants; the default
  deterministic path is always usable when no host LLM is available.
* **KISS** — a keyword index of capability synonyms; longest-phrase match
  wins, multi-tag emission allowed. Stdlib-only.
* **Absence-tolerant** — an unknown intent returns an empty list; never
  raises. Phase 1 must never block on classification.

Public API::

    classify(text: str) -> list[str]               # capability tags
    extract_terms(text: str) -> list[str]          # search terms (incl. raw tokens)
    classify_envelope(text: str) -> dict           # {capabilities, terms, confidence}

The synonym table is intentionally small and curated — every entry must earn
its place against an observed cross-project capability that recurs in this
fleet (semantic-search, auth, rate-limiting, telemetry, ...). Grow only when
recurrence warrants it.
"""
from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------
# Capability synonym index.
#
# Each entry: { tag -> list[ phrase | (phrase, weight) ] }.
# Phrases are matched case-insensitively as whole substrings (word-boundary
# safe). Longer phrases score higher (length-weighted) so "semantic search"
# beats "search" for the semantic-search tag. A `weight` override lifts a
# rare-but-strong signal phrase.
#
# Keep small. Recurrence-earned. Comment why each tag exists in this fleet.
# --------------------------------------------------------------------------
CAPABILITY_SYNONYMS: dict[str, list[Any]] = {
    # Recurs across atomize-news, atomize-ai, AIDA (per overhaul plan target scenario).
    "semantic-search": [
        "semantic search",
        "vector search",
        "embedding search",
        "rag",
        "retrieval augmented generation",
        "retrieval-augmented generation",
        "similarity search",
        "nearest neighbor search",
        "knn search",
        ("hybrid retrieval", 2),
        "dense retrieval",
        "dense recall",
        ("embeddings index", 2),
        "vector index",
        "vector db",
        "vector database",
    ],
    "auth": [
        "authentication",
        "auth flow",
        "oauth",
        "sign-in",
        "sign in",
        "login",
        "session token",
        "magic link",
        "better auth",
        "supabase auth",
    ],
    "rate-limiting": [
        "rate limit",
        "rate-limit",
        "rate limiting",
        "throttling",
        "throttle",
        "request quota",
        ("token bucket", 2),
        ("leaky bucket", 2),
    ],
    "telemetry": [
        "telemetry",
        "observability",
        "opentelemetry",
        "tracing",
        "metrics pipeline",
        "instrumentation",
        ("structured logging", 1),
    ],
    "memory": [
        "agent memory",
        "memory store",
        "memory recall",
        "memory ingestion",
        "memory consolidation",
        "context memory",
        "long-term memory",
        "episodic memory",
        "build-loop-memory",
    ],
    "ui-design": [
        "ui design",
        "design system",
        "design tokens",
        "calm precision",
        "component library",
        ("design contract", 2),
    ],
    "deployment": [
        "deploy",
        "deployment pipeline",
        "ci/cd",
        "github actions",
        "release pipeline",
        ("testflight", 1),
    ],
    "background-jobs": [
        "background job",
        "background worker",
        "queue worker",
        "cron job",
        "scheduled task",
        ("job queue", 2),
    ],
    "websockets": [
        "websocket",
        "server-sent events",
        "sse",
        "realtime updates",
        "live updates",
    ],
    "file-upload": [
        "file upload",
        "image upload",
        "multipart upload",
        "presigned url",
        "s3 upload",
    ],
}


_WORD_RE = re.compile(r"[a-z0-9][a-z0-9._/\-]{2,}", re.IGNORECASE)
_BOUNDARY = re.compile(r"\W")

# Score floor for emitting a capability tag. Below this, treat as noise.
DEFAULT_SCORE_FLOOR = 2
# Cap on capabilities emitted so the prior-art lookup stays bounded.
DEFAULT_MAX_CAPABILITIES = 3


def _phrase_score(text_lower: str, phrase: str, weight: float) -> float:
    """Return ``weight * len(phrase)`` for each whole-word match, else 0.

    Whole-word boundary check keeps "search" from matching "research". We
    test the chars immediately before and after the hit are non-word.
    """
    if not phrase:
        return 0.0
    p = phrase.lower()
    plen = len(p)
    n = len(text_lower)
    score = 0.0
    start = 0
    while True:
        i = text_lower.find(p, start)
        if i < 0:
            return score
        left_ok = i == 0 or _BOUNDARY.match(text_lower[i - 1])
        right_idx = i + plen
        right_ok = right_idx >= n or _BOUNDARY.match(text_lower[right_idx])
        if left_ok and right_ok:
            score += weight * plen
        start = i + plen


def _score_capability(text_lower: str, phrases: list[Any]) -> float:
    score = 0.0
    for entry in phrases:
        if isinstance(entry, tuple):
            phrase, weight = entry
        else:
            phrase, weight = entry, 1.0
        score += _phrase_score(text_lower, phrase, float(weight))
    return score


def classify(
    text: str,
    *,
    max_capabilities: int = DEFAULT_MAX_CAPABILITIES,
    score_floor: float = DEFAULT_SCORE_FLOOR,
) -> list[str]:
    """Return ranked capability tags for ``text`` (empty list on miss).

    Never raises. Stable order: descending score, ties broken alphabetically.
    """
    if not text:
        return []
    text_lower = text.lower()
    scored: list[tuple[float, str]] = []
    for tag, phrases in CAPABILITY_SYNONYMS.items():
        s = _score_capability(text_lower, phrases)
        if s >= score_floor:
            scored.append((s, tag))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [tag for _s, tag in scored[:max_capabilities]]


def extract_terms(text: str) -> list[str]:
    """Tokenize ``text`` into search-friendly lowercase terms.

    Drops short tokens, dedupes, preserves first-seen order. Used by the
    prior-art engine to query the semantic tier (alongside the capability
    tag itself).
    """
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _WORD_RE.findall(text.lower()):
        term = m.strip("/._-")
        if len(term) < 3 or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


def classify_envelope(
    text: str,
    *,
    max_capabilities: int = DEFAULT_MAX_CAPABILITIES,
    score_floor: float = DEFAULT_SCORE_FLOOR,
) -> dict[str, Any]:
    """Return ``{capabilities, terms, confidence}`` envelope for ``text``.

    ``confidence`` is a coarse signal — "high" when at least one capability
    scored above ``2 * score_floor``, "low" when only one weak match,
    "none" on empty. Host LLMs can use this to decide whether to refine.
    """
    text_lower = (text or "").lower()
    scored: list[tuple[float, str]] = []
    for tag, phrases in CAPABILITY_SYNONYMS.items():
        s = _score_capability(text_lower, phrases)
        if s >= score_floor:
            scored.append((s, tag))
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = scored[:max_capabilities]
    capabilities = [tag for _s, tag in top]
    if not capabilities:
        confidence = "none"
    elif top and top[0][0] >= 2 * score_floor:
        confidence = "high"
    else:
        confidence = "low"
    return {
        "capabilities": capabilities,
        "terms": extract_terms(text),
        "confidence": confidence,
    }


__all__ = [
    "CAPABILITY_SYNONYMS",
    "DEFAULT_MAX_CAPABILITIES",
    "DEFAULT_SCORE_FLOOR",
    "classify",
    "classify_envelope",
    "extract_terms",
]


if __name__ == "__main__":
    import json
    import sys

    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    print(json.dumps(classify_envelope(text), indent=2))
