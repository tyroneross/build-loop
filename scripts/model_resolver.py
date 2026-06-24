#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Host-neutral model-tier resolver with availability fallback.

Thin wrapper over ``scripts/model_overrides.py`` — it reimplements NOTHING.
``model_overrides`` already owns the tier taxonomy, the per-tier model registry,
and the cross-tier floor walk (``resolve_with_tier_fallback`` with its hard
invariant that a frontier/judgment role never resolves below the thinking tier).

This module adds the two things ``model_overrides`` does not:

1. **Persistent availability** — it loads the unavailable-model set from
   ``.build-loop/model-availability.json`` (and any dynamically-classified ids
   from ``.build-loop/model-tier-cache.json``) so an outage observed once
   persists across dispatches. ``model_overrides`` only accepts an ``unavailable``
   set passed in-process; this layer makes it durable.

2. **In-tier priority chain** — before delegating to the cross-tier floor walk,
   it walks the SAME-tier candidate list (the registry models for the tier, plus
   any cached ids classified into that tier) in priority order and returns the
   highest-priority AVAILABLE one. Only when every same-tier candidate is
   unavailable does it hand off to ``resolve_with_tier_fallback`` for the
   floor-respecting descent.

   This ordering is floor-safe by construction: staying in-tier can only resolve
   to a same-tier model (never lower), and the cross-tier hand-off inherits the
   frontier→thinking-and-no-further invariant from ``model_overrides``.

**Tier-integrity guard** — a dynamically-classified id (from the tier-cache) is
only eligible for the in-tier walk when its cache entry's ``tier`` exactly equals
the requested tier AND its ``provenance`` is ``verified``. A model whose tier was
guessed (``provenance: unverified``) is NEVER selected for the frontier tier, so
a misclassification cannot silently raise the floor. Registry models
(``MODEL_REGISTRY``) are always trusted (they are curated, not guessed).

The single source of concrete model ids stays ``MODEL_REGISTRY`` /
``TIER_DEFAULTS`` in ``model_overrides.py`` plus the two JSON caches under
``.build-loop/``. No model ids live here.

CLI::

    python3 scripts/model_resolver.py --workdir <repo> --tier frontier --json
    # honors .build-loop/model-availability.json automatically;
    # add --unavailable a,b to merge ad-hoc ids for this call only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Reuse the existing resolver — do not reimplement the tier taxonomy or floor.
try:  # pragma: no cover - import shim for direct + packaged execution
    from model_overrides import (
        MODEL_REGISTRY,
        TIERS,
        is_registered,
        resolve_with_tier_fallback,
    )
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from model_overrides import (  # type: ignore[no-redefine]
        MODEL_REGISTRY,
        TIERS,
        is_registered,
        resolve_with_tier_fallback,
    )

AVAILABILITY_FILENAME = "model-availability.json"
TIER_CACHE_FILENAME = "model-tier-cache.json"


def _build_loop_dir(workdir: Path) -> Path:
    return workdir.expanduser().resolve() / ".build-loop"


def availability_path(workdir: Path) -> Path:
    return _build_loop_dir(workdir) / AVAILABILITY_FILENAME


def tier_cache_path(workdir: Path) -> Path:
    return _build_loop_dir(workdir) / TIER_CACHE_FILENAME


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_unavailable(workdir: Path) -> set[str]:
    """Load the persistent unavailable-model set.

    Accepts two on-disk shapes (both fail-open to an empty set):
      - {"unavailable": ["fable", ...]}                 # explicit list
      - {"fable": {"reason": "...", "since": "..."}}    # id-keyed map
    """
    data = _read_json(availability_path(workdir))
    if isinstance(data, dict):
        listed = data.get("unavailable")
        if isinstance(listed, list):
            return {str(m).strip() for m in listed if str(m).strip()}
        # id-keyed map fallback (any non-"unavailable" top-level key is an id)
        return {str(k).strip() for k in data if k != "unavailable" and str(k).strip()}
    if isinstance(data, list):
        return {str(m).strip() for m in data if str(m).strip()}
    return set()


def load_tier_cache(workdir: Path) -> dict[str, dict[str, Any]]:
    """Load the dynamically-classified-model cache (id -> entry). Fail-open."""
    data = _read_json(tier_cache_path(workdir))
    if isinstance(data, dict):
        return {
            str(k): v for k, v in data.items() if isinstance(v, dict) and v.get("tier")
        }
    return set() if False else {}  # noqa: SIM211 - explicit empty dict, fail-open


def load_host_providers(workdir: Path) -> set[str] | None:
    """Load the host's reachable-provider allowlist, if declared.

    A host (e.g. Claude Code, which can only dispatch Anthropic models) may
    declare ``{"hostProviders": ["anthropic"]}`` in ``model-availability.json``.
    When present, any registry model whose ``provider`` is NOT in the set is
    treated as unreachable on this host and excluded from the in-tier chain —
    so the resolver never hands the dispatcher a model the host can't run.

    Absent/empty = host-neutral: every provider is reachable (no filtering),
    which is the default behavior. Returns ``None`` when undeclared.
    """
    data = _read_json(availability_path(workdir))
    if isinstance(data, dict):
        hp = data.get("hostProviders")
        if isinstance(hp, list) and hp:
            return {str(p).strip().lower() for p in hp if str(p).strip()}
    return None


def in_tier_candidates(
    tier: str,
    tier_cache: dict[str, dict[str, Any]],
    host_providers: set[str] | None = None,
) -> list[str]:
    """Ordered same-tier candidate ids: curated registry first, then verified cache.

    Registry order IS the priority order (default model first). Cached ids are
    appended only when their cache entry is tier-exact AND verified provenance —
    the tier-integrity guard. Duplicates are dropped, registry winning.

    When ``host_providers`` is given, a candidate is excluded if its declared
    provider is not in the host's reachable set (host-neutral filter: a model the
    host cannot dispatch is never offered). Models with an unknown provider are
    kept (fail-open — better to try and let the dispatcher's fallback catch it
    than to silently drop a possibly-valid model).
    """

    def reachable(provider: str | None) -> bool:
        if host_providers is None:
            return True
        if not provider:
            return True  # unknown provider — fail-open, keep it
        return provider.strip().lower() in host_providers

    out: list[str] = []
    seen: set[str] = set()
    for entry in MODEL_REGISTRY.get(tier, []):
        mid = entry.get("id")
        if mid and mid not in seen and reachable(entry.get("provider")):
            out.append(mid)
            seen.add(mid)
    for mid, entry in tier_cache.items():
        if entry.get("tier") != tier:
            continue
        if entry.get("provenance") != "verified":
            # Guard: a guessed tier never enters the in-tier chain. This is what
            # prevents a misclassification from silently raising the floor.
            continue
        if mid not in seen and reachable(entry.get("provider")):
            out.append(mid)
            seen.add(mid)
    return out


def resolve(
    *,
    tier: str,
    workdir: Path,
    extra_unavailable: set[str] | frozenset[str] | None = None,
    config_path: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Resolve a tier to the highest-priority AVAILABLE model.

    1. Load the persistent unavailable set (+ any ad-hoc ``extra_unavailable``).
    2. Walk the in-tier candidate chain; return the first available one.
    3. If every same-tier candidate is unavailable, delegate to
       ``resolve_with_tier_fallback`` for the floor-respecting cross-tier descent.

    Always returns an envelope with a ``resolution_path`` listing every candidate
    considered and why it was skipped, so the decision is auditable.
    """
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(TIERS)}")

    wd = workdir.expanduser().resolve()
    unavailable = load_unavailable(wd) | set(extra_unavailable or ())
    tier_cache = load_tier_cache(wd)
    host_providers = load_host_providers(wd)

    # Fold host-unreachable registry models into the unavailable set so the
    # cross-tier descent (resolve_with_tier_fallback) also avoids them. The
    # in-tier walk applies the same filter via in_tier_candidates.
    if host_providers is not None:
        for entries in MODEL_REGISTRY.values():
            for entry in entries:
                provider = (entry.get("provider") or "").strip().lower()
                mid = entry.get("id")
                if mid and provider and provider not in host_providers:
                    unavailable.add(mid)

    resolution_path: list[dict[str, Any]] = []
    candidates = in_tier_candidates(tier, tier_cache, host_providers)

    for mid in candidates:
        if mid in unavailable:
            resolution_path.append({"model": mid, "tier": tier, "skipped": "unavailable"})
            continue
        resolution_path.append({"model": mid, "tier": tier, "selected": True})
        return {
            "tier": tier,
            "model": mid,
            "source": "in-tier-chain",
            "registered": is_registered(tier, mid),
            "resolution_path": resolution_path,
            "unavailable_considered": sorted(unavailable),
        }

    # Every same-tier candidate is unavailable — hand off to the floor walk.
    base = resolve_with_tier_fallback(
        tier=tier,
        workdir=wd,
        unavailable=unavailable,
        config_path=config_path,
        state_path=state_path,
    )
    resolution_path.append(
        {
            "model": base.get("model"),
            "tier": base.get("fallback_tier", tier),
            "selected": True,
            "via": base.get("source"),
        }
    )
    base["resolution_path"] = resolution_path
    base["unavailable_considered"] = sorted(unavailable)
    return base


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".")
    p.add_argument("--tier", required=True, choices=sorted(TIERS))
    p.add_argument(
        "--unavailable",
        default=None,
        help="Comma-separated ids to treat unavailable for THIS call only, "
        "merged on top of the persistent model-availability.json set.",
    )
    p.add_argument("--config", default=None)
    p.add_argument("--state", default=None)
    p.add_argument("--plain", action="store_true", help="Print only the model id.")
    p.add_argument("--json", action="store_true", help="Print the full envelope.")
    p.add_argument("--require", action="store_true", help="Exit 1 if unresolved.")
    args = p.parse_args(argv)

    extra = (
        {m.strip() for m in args.unavailable.split(",") if m.strip()}
        if args.unavailable
        else None
    )
    result = resolve(
        tier=args.tier,
        workdir=Path(args.workdir),
        extra_unavailable=extra,
        config_path=Path(args.config) if args.config else None,
        state_path=Path(args.state) if args.state else None,
    )
    if args.require and not result.get("model"):
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 1
    if args.plain and not args.json:
        print(result.get("model") or "")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
