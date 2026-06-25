#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Load the two-axis model taxonomy (segment x capability-tier) and expose it as the single source of truth for model selection.
#   application: meta
#   status: active
"""Two-axis model taxonomy loader — the SINGLE source of truth.

build-loop selects models on two orthogonal axes:

  * SEGMENT  — the work role / primary output (Generative Reasoning, Agentic
               Execution, Representation/Retrieval, Realtime Interaction,
               Perception/Input Understanding, Generative Media,
               Governance/Evaluation).
  * TIER     — a 7-rung CAPABILITY ladder: T0 (experimental/restricted
               frontier), T1 (ultra-frontier), T2 (frontier), T3 (balanced
               workhorse), T4 (efficient near-frontier), T5 (utility/nano/edge),
               T-S (specialist infrastructure, off the capability ladder).

The taxonomy lives as DATA in ``references/model-taxonomy.json``. This module
loads it once and exposes constants + helpers so every other script imports
symbols rather than re-parsing the JSON — there is exactly ONE tier/segment
vocabulary in the codebase (KISS/DRY). ``model_overrides.py`` re-exports its
tier constants from here.

Back-compat: the legacy tier tokens ``frontier/thinking/code/pattern`` map onto
ladder rungs ``T1/T2/T3/T4``. ``normalize_tier`` folds either vocabulary to a
ladder rung so existing config, plan frontmatter, ``route_decision``, and every
existing test keep resolving to the same models.

Selection policy (Hybrid): per ``(segment, tier)`` there is an ORDERED
preferred-model list (order = capability rank, honoring Accuracy>Speed>Cost).
The resolver picks the highest-ranked AVAILABLE + host-reachable id; ties /
equal-or-unranked candidates are broken by release recency (newer wins).
``released(model_id)`` exposes the date used for that tiebreak.
"""
from __future__ import annotations

import datetime as _dt
import functools
import json
from pathlib import Path
from typing import Any

# references/model-taxonomy.json sits one level up from scripts/.
_TAXONOMY_PATH = Path(__file__).resolve().parents[1] / "references" / "model-taxonomy.json"

# The four legacy tier tokens, kept as a public surface for back-compat.
LEGACY_TIER_TOKENS = ("frontier", "thinking", "code", "pattern")


@functools.lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    """Load + cache the taxonomy JSON. Raises if it is missing/corrupt — the
    taxonomy is mandatory infrastructure, not an optional convenience."""
    try:
        data = json.loads(_TAXONOMY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - infra error
        raise RuntimeError(f"model taxonomy unreadable at {_TAXONOMY_PATH}: {exc}") from exc
    if not isinstance(data, dict) or "tiers" not in data or "segments" not in data:
        raise RuntimeError(f"model taxonomy malformed at {_TAXONOMY_PATH}")
    return data


def taxonomy() -> dict[str, Any]:
    """The full parsed taxonomy dict (loaded once)."""
    return _load()


# --------------------------------------------------------------------------
# Tier ladder
# --------------------------------------------------------------------------

def tier_ladder() -> tuple[str, ...]:
    """The ordered ladder, e.g. ("T0","T1","T2","T3","T4","T5","T-S")."""
    return tuple(_load()["tiers"]["order"])


def tier_rank() -> dict[str, int]:
    """{ladder_tier: capability rank}. Lower rank == higher capability.
    T-S (specialist) carries a sentinel-high rank so it never participates in
    the generative capability comparison."""
    return {
        t: d["rank"]
        for t, d in _load()["tiers"]["defs"].items()
        if not t.startswith("_") and isinstance(d, dict)
    }


def ladder_fallback() -> dict[str, str | None]:
    """{ladder_tier: next-tier-down-or-None}. The one-edge capability walk on
    the generative ladder. T-S maps to None (specialist, off-ladder)."""
    return {
        k: v for k, v in _load()["tiers"]["fallback"].items()
        if not k.startswith("_")
    }


def legacy_aliases() -> dict[str, str]:
    """{legacy_token: ladder_tier} — frontier->T1, thinking->T2, code->T3,
    pattern->T4. Doc-only ``_``-prefixed keys are excluded."""
    raw = _load()["tiers"]["legacy_aliases"]
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def is_legacy_tier(token: str | None) -> bool:
    """True iff ``token`` is one of the four legacy tier tokens."""
    return bool(token) and token in legacy_aliases()


def is_ladder_tier(token: str | None) -> bool:
    """True iff ``token`` is a canonical ladder rung (T0..T5, T-S)."""
    return bool(token) and token in set(tier_ladder())


def normalize_tier(token: str | None) -> str:
    """Fold a tier token (legacy OR ladder) to its canonical ladder rung.

    "frontier"->"T1", "thinking"->"T2", "code"->"T3", "pattern"->"T4",
    "T1"->"T1", "T-S"->"T-S". Raises ValueError on an unknown token so a typo
    fails loudly rather than silently mis-resolving."""
    if not token:
        raise ValueError("tier token is empty")
    aliases = legacy_aliases()
    if token in aliases:
        return aliases[token]
    if token in set(tier_ladder()):
        return token
    raise ValueError(
        f"unknown tier token {token!r}; expected a legacy token "
        f"{LEGACY_TIER_TOKENS} or a ladder rung {tier_ladder()}"
    )


# --------------------------------------------------------------------------
# Segments
# --------------------------------------------------------------------------

def segments() -> dict[str, Any]:
    """{segment_id: {label, subsegments, status, ...}}."""
    return dict(_load()["segments"])


def segment_status(segment: str) -> str:
    """"active" | "partial" | "dormant" for a segment, or "unknown"."""
    seg = _load()["segments"].get(segment)
    return seg.get("status", "unknown") if isinstance(seg, dict) else "unknown"


def active_segments() -> list[str]:
    """Segments with a live resolver consumer (status == active)."""
    return sorted(s for s in _load()["segments"] if segment_status(s) == "active")


# --------------------------------------------------------------------------
# Preferred lists + model metadata
# --------------------------------------------------------------------------

def preferred(segment: str, tier: str) -> list[str]:
    """Ordered preferred model ids for ``(segment, normalize_tier(tier))``.

    Returns [] when the cell is empty/absent (e.g. a dormant segment's
    non-specialist tier). ``tier`` accepts either vocabulary."""
    rung = normalize_tier(tier)
    cell = _load().get("preferred", {}).get(segment, {})
    if not isinstance(cell, dict):
        return []
    out = cell.get(rung, [])
    return [str(m) for m in out] if isinstance(out, list) else []


def model_meta(model_id: str | None) -> dict[str, Any] | None:
    """Seed-registry metadata for a model id (by id OR alias), or None.

    Lookup is case-insensitive on the id and folds known aliases to the
    canonical id first."""
    if not model_id:
        return None
    # Skip doc-only ``_``-prefixed keys (e.g. "_comment") whose value is a str.
    models = {
        k: v for k, v in _load().get("models", {}).items()
        if not k.startswith("_") and isinstance(v, dict)
    }
    key = model_id.strip()
    if key in models:
        return dict(models[key])
    low = key.lower()
    for mid, meta in models.items():
        if mid.lower() == low:
            return dict(meta)
        for alias in meta.get("aliases", []) or []:
            if str(alias).lower() == low:
                return dict(meta)
    return None


def released(model_id: str | None) -> str | None:
    """ISO release date for a model id (for the recency tiebreak), or None."""
    meta = model_meta(model_id)
    return meta.get("released") if meta else None


def _released_key(model_id: str) -> _dt.date:
    """Parse the release date to a date for sorting. Unknown/unparseable dates
    sort as the epoch (oldest) so a model with no known date never wins a
    recency tiebreak over a model that has one."""
    raw = released(model_id)
    if not raw:
        return _dt.date.min
    try:
        return _dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return _dt.date.min


def break_ties_by_recency(model_ids: list[str]) -> list[str]:
    """Stable-sort a candidate list so newer-released models come first while
    preserving the original (capability-rank) order among same-date models.

    The caller uses this ONLY among equal-rank / unranked candidates — the
    preferred-list order already encodes capability rank, which dominates.
    Newer wins; unknown-date models keep their original relative position
    behind any dated model."""
    # Python's sort is stable, so equal keys preserve input order. We sort by
    # descending date; date.min for unknowns puts them last among ties.
    return sorted(model_ids, key=_released_key, reverse=True)


def legacy_registry(token: str | None = None) -> dict[str, list[str]] | list[str]:
    """The back-compat selectable-model registry view, keyed by legacy tier token.

    Returns the full {legacy_token: [model_id, ...]} map, or one token's list
    when ``token`` is given. This preserves the legacy 4-token
    ``MODEL_REGISTRY`` contract (broader than a single capability rung) while
    keeping every model id in the one taxonomy file. Doc-only ``_``-keys excluded.
    """
    raw = _load().get("legacy_registry", {})
    full = {
        k: [str(m) for m in v]
        for k, v in raw.items()
        if not k.startswith("_") and isinstance(v, list)
    }
    if token is None:
        return full
    return full.get(token, [])


def classification_rubric() -> dict[str, str]:
    """Segment-appropriate benchmark hints for host-LLM classification."""
    return dict(_load().get("classification_rubric", {}))


# Eagerly-materialized module constants (convenience for importers that want a
# value rather than a call). These are snapshots of the cached load.
TAXONOMY = _load()
TIER_LADDER = tier_ladder()
TIER_RANK = tier_rank()
LADDER_FALLBACK = ladder_fallback()
LEGACY_ALIASES = legacy_aliases()
SEGMENTS = segments()


def main(argv: list[str] | None = None) -> int:
    """CLI: print the taxonomy summary or a specific lookup."""
    import argparse

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--segment", help="Show preferred lists for this segment.")
    p.add_argument("--tier", help="With --segment, show that cell's preferred list.")
    p.add_argument("--model", help="Show seed metadata for a model id.")
    p.add_argument("--json", action="store_true", help="Machine output.")
    args = p.parse_args(argv)

    if args.model:
        out = model_meta(args.model)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    if args.segment and args.tier:
        out = {"segment": args.segment, "tier": normalize_tier(args.tier),
               "preferred": preferred(args.segment, args.tier)}
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    if args.segment:
        out = {"segment": args.segment, "status": segment_status(args.segment),
               "preferred": _load().get("preferred", {}).get(args.segment, {})}
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    summary = {
        "schema_version": TAXONOMY.get("schema_version"),
        "tier_ladder": list(TIER_LADDER),
        "legacy_aliases": LEGACY_ALIASES,
        "segments": {s: segment_status(s) for s in SEGMENTS},
        "active_segments": active_segments(),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
