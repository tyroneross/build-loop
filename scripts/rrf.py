#!/usr/bin/env python3
"""Reciprocal Rank Fusion for hybrid retrieval.

Phase A chunk 3. Direct lift of Example App's `rrfFuse`
(`example-app/lib/search/retrieval/pipeline-rag.ts:52-80`).

Reference: Cormack, Clarke, Buettcher 2009 — for an item appearing in
legs L_i at rank r_i (1-indexed):

    score(item) = sum_i  1 / (k + r_i)

with k=60 the canonical default. Items missing from a leg contribute 0
from that leg. Result is a single ranked list — no normalization
required across legs because RRF only uses rank position, not absolute
score.

Why RRF (not weighted blend):
  - No tuning surface — k=60 is a one-knob solution.
  - Works across heterogeneous score scales (cosine 0..1 vs ts_rank
    0..0.5 vs PageRank 0..0.001) because rank ∈ {1,2,3,...} regardless.
  - Robust to one leg returning fewer results than another (the missing
    items just contribute 0 to that leg's term).

Identity rules: results across legs are deduped by `id` (the canonical
semantic_facts UUID, stringified). When the same row appears in
multiple legs, the first-seen record's metadata (subject, object,
confidence, etc.) wins; only the `score` is overwritten with the RRF
score so downstream sorters see a uniform 0..~0.033 range.

Public API:
    rrf_fuse(legs, k=60, id_key='id', limit=None) -> list[dict]
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

DEFAULT_K = 60
"""Cormack/Clarke/Buettcher 2009 canonical smoothing constant. Higher
values flatten the per-rank contribution curve (good when leg orderings
disagree often); lower values amplify rank-1 wins. 60 is the published
default and is what Example App ships."""


def rrf_fuse(
    legs: Sequence[Sequence[dict[str, Any]]],
    k: int = DEFAULT_K,
    id_key: str = "id",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Fuse multiple ranked lists into one via Reciprocal Rank Fusion.

    Args:
      legs:    Sequence of ranked result lists. Each inner list is in
               descending-relevance order (index 0 = rank 1).
      k:       RRF smoothing constant (default 60). Higher = flatter.
      id_key:  Field on each dict used to dedupe across legs. Defaults
               to 'id' (semantic_facts UUID).
      limit:   Optional cap on the returned list size. None = return all.

    Returns:
      List of dicts sorted by RRF score descending. Each dict carries
      its original fields plus an overwritten `score` field set to the
      RRF score. (The pre-RRF score is preserved as `_leg_score` if it
      existed under the `score` key.)

    Behavior on edge cases:
      - Empty `legs` → returns `[]`.
      - Items with falsy/missing id_key are skipped (logged silently).
      - Duplicate items within the same leg are accepted; each rank
        contributes independently (matches Example App's loop semantics).
        Callers that don't want this should dedupe before passing in.
    """
    if not legs:
        return []

    score_by_id: dict[str, float] = {}
    first_seen: dict[str, dict[str, Any]] = {}

    for leg in legs:
        for rank, item in enumerate(leg or ()):
            if not isinstance(item, dict):
                continue
            raw_id = item.get(id_key)
            if not raw_id:
                continue
            sid = str(raw_id)
            # 1-indexed rank in the RRF formula; `rank` is 0-indexed.
            contribution = 1.0 / (k + rank + 1)
            score_by_id[sid] = score_by_id.get(sid, 0.0) + contribution
            if sid not in first_seen:
                # Snapshot once so downstream consumers always see consistent
                # subject/object/metadata regardless of which leg won.
                first_seen[sid] = dict(item)

    fused: list[dict[str, Any]] = []
    for sid, rrf_score in score_by_id.items():
        original = first_seen[sid]
        merged = dict(original)
        # Preserve the pre-RRF score (typically cosine_sim or ts_rank)
        # under `_leg_score` for debugging / --stats observability.
        if "score" in merged:
            merged["_leg_score"] = merged["score"]
        merged["score"] = rrf_score
        fused.append(merged)

    fused.sort(key=lambda r: r["score"], reverse=True)

    if limit is not None and limit >= 0:
        return fused[:limit]
    return fused


def annotate_leg_membership(
    fused: Iterable[dict[str, Any]],
    legs: Sequence[Sequence[dict[str, Any]]],
    id_key: str = "id",
    leg_names: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Add a `legs` field to each fused result listing which input legs
    contained it. Useful for --stats observability (so the user can see
    "this hit came from vector AND sparse" vs "vector only").

    Returns a NEW list; does not mutate inputs. Order preserved.
    """
    if leg_names is None:
        leg_names = [f"leg_{i}" for i in range(len(legs))]
    if len(leg_names) != len(legs):
        raise ValueError(
            f"leg_names length {len(leg_names)} != legs length {len(legs)}"
        )

    leg_id_sets: list[set[str]] = []
    for leg in legs:
        ids: set[str] = set()
        for item in leg or ():
            if isinstance(item, dict):
                v = item.get(id_key)
                if v:
                    ids.add(str(v))
        leg_id_sets.append(ids)

    out: list[dict[str, Any]] = []
    for r in fused:
        sid = str(r.get(id_key) or "")
        membership = [
            name for name, idset in zip(leg_names, leg_id_sets) if sid in idset
        ]
        merged = dict(r)
        merged["legs"] = membership
        out.append(merged)
    return out
