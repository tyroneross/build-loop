#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Classify an unknown model id into a build-loop tier — host-neutral.

When the resolver meets a model id that is neither in ``MODEL_REGISTRY`` nor the
tier-cache, its tier is unknown. This script answers "which tier is it?" without
calling any vendor API: it emits a WebSearch query plus a deterministic parsing
protocol for the HOST coding agent's own LLM to execute (Claude Code runs Claude,
Codex runs GPT, etc. — the host LLM does the interpretation, per the standing
"host coding agent is the LLM" rule), then accepts the classified tier back and
caches it. The cache is keyed by model id (any vendor), so the result is reusable
across hosts — tier-keyed, not vendor-keyed.

Two-step protocol (so a dispatch never blocks on a live web search):

  1. ``lookup <id>`` — if cached, return the cached entry (``source: cache``).
     Otherwise return ``needs_classification`` with the WebSearch query and the
     parse rubric. The host LLM runs the search, reads the rubric, decides a tier.
  2. ``record <id> --tier <tier> --provider <p> [--provenance verified]`` —
     write the verdict to ``.build-loop/model-tier-cache.json``. ``provenance``
     defaults to ``unverified``; pass ``--provenance verified`` only when the host
     confirmed the tier against a T1/T2 source. The resolver's tier-integrity
     guard refuses an ``unverified`` id for the frontier tier (a guessed tier must
     never silently raise the floor).

Second ``lookup`` of the same id is cache-only (no search). ``--refresh`` forces
re-classification by ignoring the cache on lookup.

The tier rubric mirrors ``skills/model-tiering/SKILL.md`` so the host classifies
by the same contract the rest of build-loop uses:

  - frontier : clears the thinking contract AND benchmarks above the prior-gen
               thinking ceiling on >=1 of SWE-bench Verified / ARC-AGI / GPQA.
  - thinking : SWE-bench Verified >= ~78% AND competitive on ARC-AGI / GPQA.
  - code     : SWE-bench Verified >= ~75% AND tool-use accuracy >= ~85%.
  - pattern  : fast/cheap classification + summarization; no judgment gradient.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

VALID_TIERS = {"frontier", "thinking", "code", "pattern"}
TIER_CACHE_FILENAME = "model-tier-cache.json"

CLASSIFY_RUBRIC = (
    "Classify the model into exactly one build-loop tier using current benchmarks "
    "(prefer T1 official docs / leaderboards, then T2):\n"
    "  frontier: clears the thinking contract AND benchmarks above the prior-gen "
    "thinking ceiling on >=1 of SWE-bench Verified / ARC-AGI / GPQA Diamond.\n"
    "  thinking: SWE-bench Verified >= ~78% AND competitive on ARC-AGI / GPQA.\n"
    "  code: SWE-bench Verified >= ~75% AND tool-use accuracy >= ~85%.\n"
    "  pattern: fast/cheap classify+summarize, no judgment gradient.\n"
    "Then call: classify_model_tier.py record <id> --tier <tier> --provider "
    "<vendor> --provenance verified  (use 'verified' ONLY if a T1/T2 source "
    "confirmed it; otherwise omit and it caches as unverified)."
)


def _build_loop_dir(workdir: Path) -> Path:
    return workdir.expanduser().resolve() / ".build-loop"


def cache_path(workdir: Path) -> Path:
    return _build_loop_dir(workdir) / TIER_CACHE_FILENAME


def _read_cache(workdir: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(cache_path(workdir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(workdir: Path, cache: dict[str, dict[str, Any]]) -> None:
    d = _build_loop_dir(workdir)
    d.mkdir(parents=True, exist_ok=True)
    cache_path(workdir).write_text(
        json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8"
    )


def search_query(model_id: str) -> str:
    return (
        f'"{model_id}" model SWE-bench Verified benchmark provider tier '
        f"reasoning coding capability 2026"
    )


def lookup(model_id: str, workdir: Path, refresh: bool = False) -> dict[str, Any]:
    """Return the cached tier, or a needs_classification packet for the host LLM."""
    model_id = model_id.strip()
    cache = _read_cache(workdir)
    if not refresh and model_id in cache:
        entry = dict(cache[model_id])
        entry["model"] = model_id
        entry["source"] = "cache"
        entry["status"] = "classified"
        return entry
    return {
        "model": model_id,
        "status": "needs_classification",
        "source": "search",
        "search_query": search_query(model_id),
        "rubric": CLASSIFY_RUBRIC,
        "record_hint": (
            f"classify_model_tier.py record {model_id} --tier <tier> "
            f"--provider <vendor> [--provenance verified]"
        ),
    }


def record(
    model_id: str,
    *,
    tier: str,
    provider: str,
    workdir: Path,
    provenance: str = "unverified",
    source_note: str | None = None,
) -> dict[str, Any]:
    """Cache a classification verdict. Returns the written entry."""
    model_id = model_id.strip()
    if tier not in VALID_TIERS:
        raise ValueError(f"invalid tier {tier!r}; expected one of {sorted(VALID_TIERS)}")
    if provenance not in {"verified", "unverified"}:
        raise ValueError("provenance must be 'verified' or 'unverified'")
    cache = _read_cache(workdir)
    entry = {
        "tier": tier,
        "provider": provider.strip(),
        "provenance": provenance,
        "classified_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "source_note": source_note or "",
    }
    cache[model_id] = entry
    _write_cache(workdir, cache)
    out = dict(entry)
    out["model"] = model_id
    out["status"] = "recorded"
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".")
    sub = p.add_subparsers(dest="cmd", required=True)

    lk = sub.add_parser("lookup", help="Cached tier, or a needs_classification packet.")
    lk.add_argument("model_id")
    lk.add_argument("--refresh", action="store_true", help="Ignore cache; re-classify.")

    rc = sub.add_parser("record", help="Cache a classification verdict.")
    rc.add_argument("model_id")
    rc.add_argument("--tier", required=True, choices=sorted(VALID_TIERS))
    rc.add_argument("--provider", required=True)
    rc.add_argument(
        "--provenance",
        default="unverified",
        choices=["verified", "unverified"],
        help="'verified' only when a T1/T2 source confirmed the tier.",
    )
    rc.add_argument("--source-note", default=None)

    args = p.parse_args(argv)
    workdir = Path(args.workdir)

    if args.cmd == "lookup":
        result = lookup(args.model_id, workdir, refresh=args.refresh)
    else:
        result = record(
            args.model_id,
            tier=args.tier,
            provider=args.provider,
            workdir=workdir,
            provenance=args.provenance,
            source_note=args.source_note,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
