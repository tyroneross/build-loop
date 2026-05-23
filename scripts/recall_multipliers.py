#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Quality, recency, and combined-score multipliers for hybrid recall.

Phase A chunk 5. Lifted from `~/ObsidianVault/tools/scripts/vault_vector.py`
(lines 270, 482-545) with two build-loop-specific deviations documented
inline.

Sources:
  - is_temporal_query()         vault_vector.py:289
  - normalize_scores()          vault_vector.py:499
  - combined_rerank_score()     vault_vector.py:508
  - quality_multiplier()        vault_vector.py:538

Build-loop deviations vs vault_vector.py:
  1. Standard lex weight bumped 0.40 → 0.50.
     Rationale: build-loop's queries are identifier-dense ("find_dead",
     "Chunk 8", "embedding_model_version"). The wiki's 0.40 was tuned
     for natural-language paragraph queries; build-loop sits closer to
     the identifier-heavy split the research entry calls out.
     Reference: research entry build-loop-search-architecture
     §"Updated parameter table (local-only)" — `Rerank weight (lex)
     0.40 standard / 0.50 identifier-heavy`.

  2. Recency uses exponential decay on `last_updated` (frontmatter or
     valid_from) with half-life 90 days, vs the vault's year-bucket
     scoring. Build-loop's decisions are dated; the wiki's are
     year-buckets.

PPR (PageRank) is a Phase B placeholder — caller passes 0.0 here.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Temporal-query detection (lifted verbatim from vault_vector.py + tightened)
# ---------------------------------------------------------------------------

# Build-loop addition: "just" (as in "just committed") + "yesterday"
# fire the temporal weights too. The vault doesn't have these because
# its decisions don't get queried by recency the same way build-loop's
# do (build-loop's iterate loop frequently asks "what did I just decide").
TEMPORAL_QUERY_RE = re.compile(
    r"\b(latest|recent|recently|today|now|current|currently|just|yesterday|"
    r"newest|new|last|past|fresh)\b",
    re.IGNORECASE,
)


def is_temporal_query(query: str) -> bool:
    """True if the query mentions recency-implying language.

    Used to switch from standard rerank weights (more lex, less recency)
    to temporal weights (less lex, more recency).
    """
    if not query:
        return False
    return bool(TEMPORAL_QUERY_RE.search(query))


# ---------------------------------------------------------------------------
# Score normalization
# ---------------------------------------------------------------------------

def normalize_scores(values: Mapping[str, float]) -> dict[str, float]:
    """Min-max normalize to [0, 1]. All-equal inputs collapse to 1.0.

    Lifted verbatim from vault_vector.py:499. Used to put cosine,
    lexical, and PageRank legs onto a common scale before the weighted
    blend in `combined_rerank_score`.
    """
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    if abs(hi - lo) < 1e-12:
        return {k: 1.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


# ---------------------------------------------------------------------------
# Recency
# ---------------------------------------------------------------------------

RECENCY_HALF_LIFE_DAYS = 90.0
"""Half-life of the exponential recency decay. After 90 days a row's
recency contribution is 0.5; after 180 days, 0.25; etc. Tunable but
should be left at 90 unless we have data showing build-loop's lessons
go stale faster or slower than that."""


def recency_score(last_updated: datetime | str | None, *, now: datetime | None = None) -> float:
    """Exponential decay on row age. 1.0 fresh; ~0.5 at 90 days; ~0.06 at 1 year.

    Args:
      last_updated: datetime, ISO-8601 string, or None.
      now:          Override for tests.

    Returns:
      Float in [0.0, 1.0]. Missing/unparseable dates → 0.35 (the same
      neutral value vault_vector.py uses for undated rows; mid-range
      so ranking neither boosts nor punishes).
    """
    if last_updated is None:
        return 0.35
    if isinstance(last_updated, str):
        try:
            # Python's fromisoformat handles '2026-05-06T08:04:34.210547+00:00'
            last_updated = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        except ValueError:
            return 0.35
    if not isinstance(last_updated, datetime):
        return 0.35

    if now is None:
        now = datetime.now(timezone.utc)
    if last_updated.tzinfo is None:
        last_updated = last_updated.replace(tzinfo=timezone.utc)

    age_days = max(0.0, (now - last_updated).total_seconds() / 86400.0)
    return float(0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS))


# ---------------------------------------------------------------------------
# Combined rerank score
# ---------------------------------------------------------------------------

# Standard weights (build-loop tuning): identifier-dense queries get more
# weight on the lexical leg. See module docstring for rationale.
STANDARD_W_COS = 0.45
STANDARD_W_LEX = 0.50  # bumped from vault's 0.40
STANDARD_W_PPR = 0.07
STANDARD_W_RECENCY = 0.08

# Temporal weights (vault_vector.py defaults preserved). Recency-leaning.
TEMPORAL_W_COS = 0.42
TEMPORAL_W_LEX = 0.35
TEMPORAL_W_PPR = 0.07
TEMPORAL_W_RECENCY = 0.16


def combined_rerank_score(
    *,
    pid: str,
    query: str,
    cos_norm: Mapping[str, float],
    lex_norm: Mapping[str, float],
    ppr_norm: Mapping[str, float],
    recency: float,
) -> float:
    """Blend cos / lex / PPR / recency into a single rerank signal.

    Switches between standard and temporal weights based on
    `is_temporal_query(query)`.
    """
    if is_temporal_query(query):
        return (
            TEMPORAL_W_COS * cos_norm.get(pid, 0.0)
            + TEMPORAL_W_LEX * lex_norm.get(pid, 0.0)
            + TEMPORAL_W_PPR * ppr_norm.get(pid, 0.0)
            + TEMPORAL_W_RECENCY * recency
        )
    return (
        STANDARD_W_COS * cos_norm.get(pid, 0.0)
        + STANDARD_W_LEX * lex_norm.get(pid, 0.0)
        + STANDARD_W_PPR * ppr_norm.get(pid, 0.0)
        + STANDARD_W_RECENCY * recency
    )


# ---------------------------------------------------------------------------
# Quality multiplier (compounding)
# ---------------------------------------------------------------------------

QUALITY_TENTATIVE_MULT = 0.7
"""Confidence label `tentative` (or numeric confidence < 0.5) multiplies
the combined score by 0.7. Compounding with status mults below."""

QUALITY_DRAFT_MULT = 0.7
"""Status `draft` multiplies by 0.7. Compounds with low-confidence
mult, so a tentative+draft row → 0.49× — a meaningful demotion."""

QUALITY_SUPERSEDED_MULT = 0.3
"""Status `superseded` multiplies by 0.3. Heavier demotion than draft
because superseded means a newer decision exists; we still want it in
the result set as an audit trail, but it should rarely beat the live
decision in ranking."""


def quality_multiplier(row: Mapping[str, Any]) -> float:
    """Return the rerank multiplier for a row based on confidence + status.

    Reads:
      - row['confidence']                 (numeric 0.0..1.0)
      - row['metadata']['confidence']     (label: 'explicit' | 'confirmed' |
                                           'inferred' | 'tentative' | 'assumed')
      - row['status']                     ('active' | 'draft' | 'superseded' | ...)

    Compounding: a tentative draft → 0.7 × 0.7 = 0.49. Superseded draft →
    0.7 × 0.3 = 0.21.

    Defensive against missing fields — never raises, treats absence as
    no-penalty.
    """
    mult = 1.0

    # Status check.
    status = (row.get("status") or "").lower().strip()
    if status == "draft":
        mult *= QUALITY_DRAFT_MULT
    elif status == "superseded":
        mult *= QUALITY_SUPERSEDED_MULT

    # Numeric confidence column.
    conf_num = row.get("confidence")
    if isinstance(conf_num, (int, float)) and conf_num < 0.5:
        mult *= QUALITY_TENTATIVE_MULT

    # Metadata-bag confidence label.
    md = row.get("metadata") or {}
    if isinstance(md, str):
        # JSONB sometimes round-trips as a string; tolerate.
        try:
            import json
            md = json.loads(md)
        except (ValueError, TypeError):
            md = {}
    conf_label = ""
    if isinstance(md, Mapping):
        conf_label = (md.get("confidence") or "").lower().strip()
    if conf_label in ("tentative", "assumed"):
        # Compound only if not already counted by numeric < 0.5 (avoids
        # double-penalizing a row that has both signals).
        if not (isinstance(conf_num, (int, float)) and conf_num < 0.5):
            mult *= QUALITY_TENTATIVE_MULT

    return mult


# ---------------------------------------------------------------------------
# Convenience: apply multipliers to a fused result list
# ---------------------------------------------------------------------------

def apply_multipliers(
    rows: list[dict[str, Any]],
    query: str,
    *,
    now: datetime | None = None,
    recency_field: str = "valid_from",
    ppr: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Apply quality + recency multipliers to a list of fused/reranked rows.

    Phase B: now also blends a normalised PageRank prior when `ppr` is
    provided (recall_graph.pagerank_prior output). Phase A passed `None`
    here, leaving the W_PPR weight inert; with `ppr` populated the
    weight kicks in additively in the same shape as recency.

    Mutation contract: returns a NEW list of NEW dicts. Each row gets:
      - `_quality_mult`   : the quality multiplier applied
      - `_recency_score`  : the recency component (0..1)
      - `_ppr_score`      : the PageRank prior component (0..1) when ppr given
      - `_temporal_query` : bool, true if temporal weights were used
      - `score`           : original score × quality mult, blended with
                            recency and (optionally) PPR via the
                            temporal-aware standard/temporal weights.
    """
    if not rows:
        return []
    is_temporal = is_temporal_query(query)
    w_recency = TEMPORAL_W_RECENCY if is_temporal else STANDARD_W_RECENCY
    w_ppr = TEMPORAL_W_PPR if is_temporal else STANDARD_W_PPR

    # Normalise PPR per-result-set so the additive blend stays bounded
    # to roughly the recency scale. Without this the long-tailed PPR
    # distribution would let a high-PageRank node sweep the ranking.
    ppr_norm: dict[str, float] = {}
    if ppr:
        # Restrict to ids actually in the result set so we min-max over
        # the *visible* candidates, not the whole graph.
        present = {str(r.get("id")): float(ppr.get(str(r.get("id")), 0.0)) for r in rows}
        ppr_norm = normalize_scores(present)

    out: list[dict[str, Any]] = []
    for r in rows:
        merged = dict(r)
        # Recency.
        last_updated = r.get(recency_field)
        rec = recency_score(last_updated, now=now)
        merged["_recency_score"] = rec
        merged["_temporal_query"] = is_temporal

        # PPR component (0 when no ppr passed).
        rid = str(r.get("id") or "")
        ppr_component = ppr_norm.get(rid, 0.0) if ppr_norm else 0.0
        merged["_ppr_score"] = ppr_component

        # Quality.
        qmult = quality_multiplier(r)
        merged["_quality_mult"] = qmult

        base = float(r.get("score", 0.0))
        # Blend recency + PPR in additively (same shape as
        # combined_rerank_score but with already-blended cos+lex
        # collapsed into `base`).
        # Apply quality multiplier last so the recency/PPR boost can't
        # rescue a superseded row.
        blended = base + (w_recency * rec) + (w_ppr * ppr_component)
        merged["score"] = blended * qmult
        out.append(merged)

    out.sort(key=lambda x: x["score"], reverse=True)
    return out
