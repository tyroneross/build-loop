#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Relevance scoring for memory recall results.

WHY THIS EXISTS
---------------
`memory_facade.recall()` merged every backend's candidates and sorted them by
``_recency_ts`` alone::

    merged.sort(key=lambda x: (x.get("_recency_ts") or 0), reverse=True)

Relevance was never computed. Measured against the real store on 2026-08-31
across six real queries drawn from runtime telemetry, the top-ranked result
matched **zero** query terms in 5 of 6 cases (1 of 2 terms in the sixth), while
a strictly better match sat inside the same result set every time. Recall was
returning the newest document that survived a loose filter.

The filter is loose because `_q_match` is a token-OR **substring** test with no
word boundaries, no stopwords, and no minimum token length: the token ``ai``
matches ``main``, ``domain``, ``explain``, ``chain``, and ``detail``. Combined
with recency-only ordering, a document that matched one accidental substring
outranked a document that matched eight real terms.

WHAT THIS CHANGES
-----------------
Ordering only. This module never filters -- every candidate `recall()` found is
still returned, so this cannot reduce recall. It reorders them so the most
relevant surface first, which is what `limit` truncation then keeps.

SCORING
-------
score = sum over matched terms of (field_weight * idf), normalised, times a
bounded recency nudge.

- **idf**: rare terms count more than common ones, computed over the candidate
  set itself. Stops a shared boilerplate word ("project", "build") from
  carrying a match.
- **field_weight**: a hit in id/title outranks a hit in tags, which outranks a
  hit in body. Title terms are what the author chose to name the thing.
- **recency**: a bounded multiplier in [1.0, 1.15]. It can break a tie between
  comparably relevant results and can never overturn a real relevance gap,
  which is all recency was ever fit to do.

**No separate coverage multiplier, and that was measured, not assumed.** The
first draft multiplied by coverage^2 (fraction of query terms matched). Swept
against real runtime queries at three relevance thresholds, dropping it was
better or tied at every point -- P@3 0.515 -> 0.636 and MRR 0.531 -> 0.575 at
threshold 0.5, with P@1 unchanged. The summation over matched terms already
encodes breadth, so a coverage factor double-penalises a document that matches
several terms but not all. Re-check this if the field weights change.

Word-boundary matching is used throughout, with a prefix fallback so
"migration" still matches "migrations". Stopwords and tokens under 3 characters
are dropped from the query before scoring.

Stdlib only. Pure functions -- no I/O, no globals.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Sequence

# Deliberately small. A domain stoplist that grows to hundreds of words starts
# deleting real query intent ("state", "run", "memory" are all meaningful here).
STOPWORDS = frozenset("""
a an and are as at be been but by for from has have how in into is it its of on
or that the their then there these this to was were what when where which who
will with your you we our us if not no do does did can could should would
""".split())

MIN_TOKEN_LEN = 3

# Field weights. Order matters more than the exact values; these were chosen so
# a title hit cannot be outweighed by two body hits on the same term.
W_TITLE = 3.0
W_TAGS = 2.0
W_BODY = 1.0

TITLE_FIELDS = ("title", "id", "canonical_id", "slug", "name")
# `snippet` is load-bearing: the content backend returns the matched body text
# there, and it is the ONLY body evidence any row carries. Omitting it ranked
# content rows on their title alone, so widening retrieval flooded the top-k
# with rows whose actual match evidence was invisible -- measured 2026-08-31:
# precision@10 COLLAPSED 0.075 -> 0.013 as retrieval widened 10 -> 200.
BODY_FIELDS = ("path", "legacy_path", "legacy_id", "status", "summary",
               "body", "text", "why_durable", "reason", "snippet", "excerpt")

_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    """Lowercase word tokens, stopwords and short tokens removed."""
    return [
        t for t in _WORD_RE.findall((text or "").lower())
        if len(t) >= MIN_TOKEN_LEN and t not in STOPWORDS
    ]


def query_terms(query: str) -> List[str]:
    """DISTINCT query terms, order preserved."""
    seen: set[str] = set()
    out: List[str] = []
    for t in tokenize(query):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _field_text(row: Dict[str, Any], fields: Sequence[str]) -> str:
    return " ".join(str(row.get(f) or "") for f in fields)


def _tag_text(row: Dict[str, Any]) -> str:
    tags = row.get("tags") or []
    if isinstance(tags, (list, tuple)):
        return " ".join(str(t) for t in tags)
    return str(tags)


def _term_hit(term: str, words: set[str]) -> bool:
    """Word-boundary match, with a prefix fallback for simple morphology.

    Exact word first ("migration" == "migration"). Then prefix, so "migration"
    matches "migrations" and "deploy" matches "deployment" -- but "ai" can never
    match "main", which the old substring test allowed.
    """
    if term in words:
        return True
    return any(w.startswith(term) or term.startswith(w) for w in words if len(w) >= MIN_TOKEN_LEN)


def _row_fields(row: Dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    title = set(tokenize(_field_text(row, TITLE_FIELDS)))
    tags = set(tokenize(_tag_text(row)))
    body = set(tokenize(_field_text(row, BODY_FIELDS)))
    return title, tags, body


def _idf(terms: Sequence[str], rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """Inverse document frequency over the candidate set.

    A term present in every candidate carries no discriminating information, so
    it is weighted toward zero rather than counted as a match for everyone.
    """
    n = max(len(rows), 1)
    field_sets = [_row_fields(r) for r in rows]
    idf: Dict[str, float] = {}
    for t in terms:
        df = sum(1 for (ti, ta, bo) in field_sets if _term_hit(t, ti | ta | bo))
        idf[t] = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
    return idf


def _recency_nudge(row: Dict[str, Any], newest: float, oldest: float) -> float:
    """Bounded multiplier in [1.0, 1.15]. Cannot overturn real relevance."""
    ts = row.get("_recency_ts") or 0
    if not ts or newest <= oldest:
        return 1.0
    return 1.0 + 0.15 * ((ts - oldest) / (newest - oldest))


def score_row(row: Dict[str, Any], terms: Sequence[str], idf: Dict[str, float],
              newest: float, oldest: float) -> float:
    if not terms:
        return _recency_nudge(row, newest, oldest)
    title, tags, body = _row_fields(row)
    weighted = 0.0
    matched = 0
    total_idf = sum(idf.get(t, 1.0) for t in terms) or 1.0
    for t in terms:
        w = 0.0
        if _term_hit(t, title):
            w = W_TITLE
        elif _term_hit(t, tags):
            w = W_TAGS
        elif _term_hit(t, body):
            w = W_BODY
        if w:
            matched += 1
            weighted += w * idf.get(t, 1.0)
    if not matched:
        return 0.0
    # Normalised by the best attainable score (every term hit in the title), so
    # values stay comparable across queries of different length.
    return (weighted / (W_TITLE * total_idf)) * _recency_nudge(row, newest, oldest)


def rank(rows: Iterable[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """Return rows ordered most-relevant first. Never drops a row.

    An empty query degrades to recency order, which is the correct behaviour for
    "show me what is new" and matches the previous default.
    """
    items = list(rows)
    if not items:
        return items
    terms = query_terms(query)
    ts_values = [r.get("_recency_ts") or 0 for r in items]
    newest, oldest = max(ts_values), min(ts_values)
    if not terms:
        return sorted(items, key=lambda r: r.get("_recency_ts") or 0, reverse=True)
    idf = _idf(terms, items)
    scored = [
        (score_row(r, terms, idf, newest, oldest), r.get("_recency_ts") or 0, i, r)
        for i, r in enumerate(items)
    ]
    # Sort key: relevance, then recency, then original order for full determinism.
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return [r for _s, _ts, _i, r in scored]


def explain(row: Dict[str, Any], query: str,
            rows: Sequence[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """Per-row scoring breakdown, for eval and debugging."""
    terms = query_terms(query)
    pool = list(rows) if rows else [row]
    ts_values = [r.get("_recency_ts") or 0 for r in pool]
    idf = _idf(terms, pool)
    title, tags, body = _row_fields(row)
    hits = {
        t: ("title" if _term_hit(t, title)
            else "tags" if _term_hit(t, tags)
            else "body" if _term_hit(t, body)
            else None)
        for t in terms
    }
    matched = [t for t, f in hits.items() if f]
    return {
        "terms": terms,
        "matched": matched,
        "coverage": round(len(matched) / len(terms), 4) if terms else None,
        "fields": hits,
        "score": round(score_row(row, terms, idf,
                                 max(ts_values, default=0),
                                 min(ts_values, default=0)), 6),
    }
