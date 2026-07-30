#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Resolve build-loop model tier overrides.

The orchestrator reasons in tiers (`frontier`, `thinking`, `code`, `pattern`)
and resolves those tiers to concrete model ids at dispatch time. Repo config is
the preferred source; state.json is accepted for older runs that snapshot
config there.

Tier defaults (Anthropic mapping, used as the fallback when no override
is configured and no `--fallback` is supplied):

    frontier  -> fable    (planning + verification verdicts)
    thinking  -> opus     (coordination + escalation)
    code      -> sonnet   (execution default)
    pattern   -> haiku    (recognition / mock-scan)

Configs that predate the `frontier` tier resolve `frontier` -> `fable` so older
repos keep working without edits.

`MODEL_REGISTRY` lists the cross-vendor models known to fit each tier (e.g.
GPT-5.5 for frontier, GPT-5 Nano for pattern). It is advisory: override
resolution accepts any model id; the registry powers `--list-models` and an
advisory `registered` flag on the envelope. `python3 scripts/model_overrides.py
--list-models` prints it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Single source of truth for the segment/tier vocabulary. This module re-expresses
# the legacy 4-token tier surface (frontier/thinking/code/pattern) over the
# taxonomy ladder so there is exactly ONE vocabulary in the codebase. The legacy
# tokens are PRESERVED as the public surface (every existing test, config, plan
# frontmatter, and route_decision references them) — they fold to ladder rungs
# T1/T2/T3/T4 via the taxonomy's legacy_aliases.
try:  # pragma: no cover - import shim for direct + packaged execution
    import model_taxonomy
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import model_taxonomy  # type: ignore[no-redefine]

# The legacy tier tokens, in capability order (highest first). This stays the
# public `TIERS` surface; the values fold to ladder rungs internally.
_LEGACY_ORDER = ("frontier", "thinking", "code", "pattern")
# The implicit segment of the legacy tier tokens (they predate the segment axis).
_LEGACY_SEGMENT = "generative_reasoning"

TIERS = set(_LEGACY_ORDER)

# Derived from the taxonomy: each legacy token's default = the first preferred
# model for (generative_reasoning, its-ladder-rung). frontier->fable,
# thinking->opus, code->sonnet, pattern->haiku — same values as before, now
# sourced from references/model-taxonomy.json instead of hand-maintained here.
def _derive_tier_defaults() -> dict[str, str]:
    out: dict[str, str] = {}
    for token in _LEGACY_ORDER:
        pref = model_taxonomy.preferred(_LEGACY_SEGMENT, token)
        if pref:
            out[token] = pref[0]
    return out


TIER_DEFAULTS = _derive_tier_defaults()

# Capability ordering, highest first. The single source of truth for "is tier A
# at or above tier B" — used by the floor clamp so the frontier-never-below-
# thinking invariant is enforced no matter HOW a model was selected (config
# override, in-tier walk, or cross-tier fallback), not only inside the
# TIER_FALLBACK walk. Lower index == higher capability. Legacy-token surface;
# the ordering is inherited from the taxonomy ladder rank of each token's rung.
TIER_ORDER = _LEGACY_ORDER
TIER_RANK = {tier: i for i, tier in enumerate(TIER_ORDER)}


def tier_of_model(model: str | None) -> str | None:
    """Best-effort: the registry tier a concrete model id belongs to, or None.

    Resolves from MODEL_REGISTRY (curated). An id not in the registry returns
    None — the caller decides how to treat an unknown id (the floor clamp keeps
    an unknown id, since we cannot prove it is below the floor)."""
    if not model:
        return None
    for tier, entries in MODEL_REGISTRY.items():
        if any(entry.get("id") == model for entry in entries):
            return tier
    return None


def is_below_floor(model: str | None, floor_tier: str) -> bool:
    """True iff ``model``'s KNOWN registry tier is strictly below ``floor_tier``.

    An unknown model (not in the registry) is NOT considered below the floor —
    we cannot prove it, and refusing every unknown id would break legitimate
    config overrides to brand-new models. Only a model we can positively place in
    a lower tier is rejected by the clamp."""
    if floor_tier not in TIER_RANK:
        return False
    mtier = tier_of_model(model)
    if mtier is None:
        return False
    return TIER_RANK[mtier] > TIER_RANK[floor_tier]

# Standing tier-fallback graph: when a tier's resolved model is unavailable and
# the caller supplied no explicit `fallback`, resolution walks DOWN this graph to
# the fallback tier's default. This is the durable POLICY (tier -> tier edges);
# concrete model ids live ONLY in TIER_DEFAULTS / MODEL_REGISTRY, never here —
# the rule is expressed in tier/role terms so a model swap never touches it.
#
#     frontier -> thinking   (judgment role degrades to the THINKING tier)
#     thinking -> code
#     code     -> pattern
#     pattern  -> (none; nothing lower)
#
# HARD INVARIANT: the frontier (judgment) tier's ONLY permitted standing
# fallback is thinking. A frontier role must NEVER silently resolve below
# thinking (i.e. never to code or pattern). `resolve_with_tier_fallback`
# enforces this by walking AT MOST one edge from frontier; if the thinking
# tier's default is itself unavailable, frontier resolution stops at thinking
# rather than degrading further. See feedback_model_org_fable5.md.
# Derived from the taxonomy ladder fallback, mapped back to legacy tokens. The
# ladder edges T1->T2->T3->T4 correspond exactly to frontier->thinking->code->
# pattern; pattern (T4) bottoms out at None on the legacy surface (the legacy
# vocabulary has no rung below pattern). Sourcing this from the taxonomy keeps
# ONE fallback graph in the codebase while preserving the legacy token shape the
# existing tests assert (fallback_tier == "thinking"/"code"/"pattern").
def _derive_tier_fallback() -> dict[str, str | None]:
    rung_to_token = {model_taxonomy.normalize_tier(t): t for t in _LEGACY_ORDER}
    ladder_fb = model_taxonomy.ladder_fallback()
    out: dict[str, str | None] = {}
    for token in _LEGACY_ORDER:
        rung = model_taxonomy.normalize_tier(token)
        nxt_rung = ladder_fb.get(rung)
        # Map the next ladder rung back to a legacy token; if the next rung is
        # outside the legacy vocabulary (below pattern/T4) the legacy chain ends.
        out[token] = rung_to_token.get(nxt_rung) if nxt_rung else None
    return out


TIER_FALLBACK = _derive_tier_fallback()

# Selectable models per tier. TIER_DEFAULTS (above) is the Anthropic mapping used
# when nothing is configured; this registry is the broader set of models known to
# fit each tier's contract and therefore safe to select via
# `.build-loop/config.json` modelOverrides[tier] or a per-dispatch model id.
# `status`: default (the tier's Anthropic fallback) | verified (cross-vendor,
# advisory) | local. Override resolution still accepts ANY string — the registry
# is advisory (powers `--list-models` + the `registered` flag), never a gate.
# Cross-vendor cells are best-effort; confirm current benchmarks before swapping.
# Canonical detail + swap recipes: references/model-tier-mapping.md.
# `aliases`: the canonical/full model ids that map to this registry alias. The
# SINGLE source of the canonical<->alias map — a dispatch error names the
# canonical id ("Claude Fable 5 is currently unavailable" -> claude-fable-5),
# but the registry + config use the short alias ("fable"). `normalize_model_id`
# folds either form to the alias so an availability match on EITHER works.
# Mirror of references/model-tier-mapping.md "Anthropic default" canonical ids.
# Derived from the taxonomy (DRY): for each legacy tier token, the selectable
# models are the taxonomy's preferred list for (generative_reasoning, its-rung),
# expanded to the `{id, provider, label, status, aliases}` registry shape via the
# taxonomy's per-model metadata. The FIRST entry per tier is the default (its
# status is forced to "default" so `--list-models` and `is_registered` keep their
# contract). This replaces the hand-maintained registry — there is now ONE place
# (references/model-taxonomy.json) where these model ids live.
def _derive_model_registry() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for token in _LEGACY_ORDER:
        entries: list[dict[str, Any]] = []
        ids = model_taxonomy.legacy_registry(token)
        for i, mid in enumerate(ids):
            meta = model_taxonomy.model_meta(mid) or {}
            entry: dict[str, Any] = {
                "id": mid,
                "provider": meta.get("provider", "unknown"),
                "label": meta.get("label", mid),
                # First model per tier is the tier default; the taxonomy may mark
                # it "default" already, but force it so the contract is explicit.
                "status": "default" if i == 0 else meta.get("status", "verified"),
            }
            aliases = meta.get("aliases") or []
            if aliases:
                entry["aliases"] = list(aliases)
            entries.append(entry)
        out[token] = entries
    return out


MODEL_REGISTRY: dict[str, list[dict[str, Any]]] = _derive_model_registry()


def _build_alias_index() -> dict[str, str]:
    """canonical-or-alias id (lowercased) -> registry alias id. Built once."""
    idx: dict[str, str] = {}
    for entries in MODEL_REGISTRY.values():
        for entry in entries:
            alias = entry["id"]
            idx[alias.lower()] = alias
            for canon in entry.get("aliases", []) or []:
                idx[str(canon).lower()] = alias
    return idx


_ALIAS_INDEX = _build_alias_index()


def normalize_model_id(model: str | None) -> str | None:
    """Fold a canonical/full model id to its registry alias.

    "claude-fable-5" -> "fable", "claude-opus-4-8[1m]" -> "opus", "fable" -> "fable".
    An id not known to the registry is returned unchanged (lower-cased trim only
    of surrounding whitespace) so brand-new/unregistered ids still pass through.
    """
    if not model:
        return model
    key = model.strip()
    return _ALIAS_INDEX.get(key.lower(), key)


def expand_unavailable(unavailable: set[str] | frozenset[str] | None) -> set[str]:
    """Expand an unavailable set so a model down by EITHER its canonical id OR its
    alias marks BOTH forms unavailable. Returns alias + every known canonical id."""
    out: set[str] = set()
    reverse: dict[str, list[str]] = {}
    for entries in MODEL_REGISTRY.values():
        for entry in entries:
            reverse[entry["id"]] = [entry["id"], *(entry.get("aliases", []) or [])]
    for raw in unavailable or ():
        alias = normalize_model_id(raw)
        out.add(raw)
        out.add(alias)
        out.update(reverse.get(alias, []))
    return out


def registered_models(tier: str | None = None) -> dict[str, list[dict[str, str]]]:
    """Return the selectable-model registry, optionally filtered to one tier."""
    if tier is None:
        return {t: list(MODEL_REGISTRY.get(t, [])) for t in sorted(TIERS)}
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(TIERS)}")
    return {tier: list(MODEL_REGISTRY.get(tier, []))}


def is_registered(tier: str, model: str | None) -> bool:
    """True if `model` is a registered selectable model for `tier` (advisory)."""
    if not model:
        return False
    return any(entry["id"] == model for entry in MODEL_REGISTRY.get(tier, []))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _model_override_from_data(data: dict[str, Any], tier: str) -> str | None:
    config = data.get("config") if "config" in data else data
    if not isinstance(config, dict):
        return None
    overrides = config.get("modelOverrides")
    if not isinstance(overrides, dict):
        return None
    value = overrides.get(tier)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def default_config_path(workdir: Path) -> Path:
    return workdir / ".build-loop" / "config.json"


def default_state_path(workdir: Path) -> Path:
    return workdir / ".build-loop" / "state.json"


def resolve_model(
    *,
    tier: str,
    workdir: Path,
    fallback: str | None = None,
    config_path: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(TIERS)}")

    wd = workdir.expanduser().resolve()
    cfg = (config_path or default_config_path(wd)).expanduser()
    state = (state_path or default_state_path(wd)).expanduser()

    for source, path in (("config", cfg), ("state", state)):
        model = _model_override_from_data(_read_json(path), tier)
        if model:
            return {
                "tier": tier,
                "model": model,
                "source": source,
                "path": str(path),
                "configured": True,
                "registered": is_registered(tier, model),
            }

    # Prefer the explicit caller-supplied fallback; otherwise fall back to the
    # tier's built-in default so a config that predates a tier keeps working.
    if fallback:
        return {
            "tier": tier,
            "model": fallback,
            "source": "fallback",
            "path": None,
            "configured": False,
            "registered": is_registered(tier, fallback),
        }

    default = TIER_DEFAULTS.get(tier)
    if default:
        return {
            "tier": tier,
            "model": default,
            "source": "tier-default",
            "path": None,
            "configured": False,
            "registered": is_registered(tier, default),
        }

    return {
        "tier": tier,
        "model": None,
        "source": "unresolved",
        "path": None,
        "configured": False,
        "registered": False,
    }


def resolve_with_tier_fallback(
    *,
    tier: str,
    workdir: Path,
    unavailable: set[str] | frozenset[str] | None = None,
    fallback: str | None = None,
    config_path: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Resolve a tier to a model, applying the STANDING tier-fallback policy.

    First resolves via `resolve_model` (config/state override -> caller fallback
    -> tier default). If that model is in `unavailable` AND the caller supplied
    no explicit `fallback`, walk the standing `TIER_FALLBACK` graph to the
    fallback tier's default, labelling the result `source: "tier-fallback"`.

    An explicit caller `fallback` is honoured as-is (per-call intent wins over the
    standing policy) and the standing walk is skipped.

    HARD INVARIANT — frontier never resolves below thinking: the frontier
    (judgment) tier walks AT MOST one edge, to thinking. If the thinking default
    is also unavailable, frontier resolution STOPS at the thinking default rather
    than degrading to code/pattern. Every other tier may keep walking down the
    graph until a usable default is found or the graph bottoms out.
    """
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(TIERS)}")

    # Expand the unavailable set so a model declared down by EITHER its canonical
    # id ("claude-fable-5", as a dispatch error names it) OR its short alias
    # ("fable", as the registry/config use it) marks BOTH forms unavailable. The
    # resolved model is normalized to its alias before the membership test, so a
    # canonical-id outage actually fires the fallback. (Root fix for GAP 1.)
    unavailable = expand_unavailable(unavailable)

    base = resolve_model(
        tier=tier,
        workdir=workdir,
        fallback=fallback,
        config_path=config_path,
        state_path=state_path,
    )

    model = normalize_model_id(base.get("model"))
    # FLOOR ENFORCEMENT AT THE SOURCE (so EVERY caller inherits it, not just the
    # model_resolver wrapper). A config/state `modelOverrides[tier]` is resolved
    # by `resolve_model` BEFORE this floor walk — without this guard a frontier
    # override pointed at a sub-thinking model (modelOverrides.frontier="haiku")
    # would be returned as-is, breaching the HARD INVARIANT this function's own
    # docstring promises. So: a configured override whose KNOWN registry tier is
    # strictly below the requested tier's standing-fallback floor is NOT returned
    # — it is treated as unavailable and the standing walk runs instead. An
    # explicit per-call `fallback` is deliberate caller intent and is exempt
    # (the caller owns that choice, same as `--fallback`); an UNKNOWN model is
    # exempt (cannot be proven below floor — refusing all unknowns would break
    # legitimate overrides to brand-new models).
    floor_tier = TIER_FALLBACK.get(tier) or tier
    override_below_floor = (
        base.get("source") in {"config", "state"}
        and not fallback
        and is_below_floor(model, floor_tier)
    )
    if override_below_floor and model:
        unavailable = unavailable | {model}
        model = None  # force the standing walk below

    # Usable as-is when there's a model and it's not declared unavailable.
    if model and model not in unavailable:
        # Return the normalized alias so the caller always gets a dispatchable id
        # (a config override written as a canonical id resolves to its alias).
        base["model"] = model
        return base
    # An explicit caller fallback is intentional — don't override it with the
    # standing policy. If it's unavailable that's the caller's problem to know.
    if fallback:
        return base

    # Walk the standing tier-fallback graph. `frontier` walks at most one edge.
    visited = [tier]
    current = tier
    max_steps = 1 if tier == "frontier" else len(TIERS)
    for _ in range(max_steps):
        nxt = TIER_FALLBACK.get(current)
        if nxt is None:
            break
        candidate = TIER_DEFAULTS.get(nxt)
        visited.append(nxt)
        if candidate and candidate not in unavailable:
            return {
                "tier": tier,
                "model": candidate,
                "source": "tier-fallback",
                "fallback_tier": nxt,
                "fallback_path": visited,
                "path": None,
                "configured": False,
                "registered": is_registered(nxt, candidate),
            }
        # frontier stops at thinking even if thinking's default is unavailable
        # (invariant: never resolve a judgment role below thinking).
        if tier == "frontier":
            return {
                "tier": tier,
                "model": candidate,  # thinking default (may itself be unavailable)
                "source": "tier-fallback",
                "fallback_tier": nxt,
                "fallback_path": visited,
                "path": None,
                "configured": False,
                "registered": is_registered(nxt, candidate),
            }
        current = nxt

    # No usable fallback tier (e.g. pattern, or every default unavailable):
    # return the base resolution unchanged.
    return base


def resolve_role(
    *,
    segment: str,
    tier: str,
    workdir: Path,
    unavailable: set[str] | frozenset[str] | None = None,
    recency_tiebreak: bool = True,
) -> dict[str, Any]:
    """Two-axis entrypoint: resolve a ``(segment, tier)`` ROLE to a model.

    Walks ``taxonomy.preferred(segment, tier)`` in capability-rank order
    (the list order), optionally re-ordering equal-rank ties by release
    recency (newer first), and returns the highest-ranked AVAILABLE id. The
    preferred list order already encodes capability rank, so recency only
    re-orders *within* the list — it never overrides a higher-ranked model
    with a newer lower-ranked one (that would violate Accuracy>Speed>Cost).

    Back-compat bridge: the legacy tier tokens have an implicit segment of
    ``generative_reasoning``. When ``segment == "generative_reasoning"`` and
    the requested tier maps to a legacy token, on exhaustion this delegates to
    ``resolve_with_tier_fallback`` so the floor invariant (frontier never below
    thinking) and the legacy fallback chain are inherited unchanged.

    For a specialist (T-S) or dormant-segment role with no available preferred
    model, returns ``source: "unresolved"`` (there is no generative ladder to
    walk for an off-ladder specialist role).
    """
    if segment not in model_taxonomy.segments():
        raise ValueError(
            f"unknown segment {segment!r}; expected one of "
            f"{sorted(model_taxonomy.segments())}"
        )
    rung = model_taxonomy.normalize_tier(tier)
    wd = workdir.expanduser().resolve()
    unavail = expand_unavailable(unavailable)

    candidates = model_taxonomy.preferred(segment, rung)
    if recency_tiebreak and candidates:
        # Re-order ONLY among same-capability-rank entries. The preferred list is
        # already rank-ordered, so we keep rank as the dominant key and recency
        # as the tiebreak: group is the list itself (every entry shares the same
        # (segment,tier) cell == same rung == same rank), so recency is a pure
        # in-cell tiebreak. Newer wins.
        candidates = model_taxonomy.break_ties_by_recency(candidates)

    resolution_path: list[dict[str, Any]] = []
    for mid in candidates:
        alias = normalize_model_id(mid)
        if alias in unavail or mid in unavail:
            resolution_path.append({"model": mid, "skipped": "unavailable"})
            continue
        resolution_path.append({"model": mid, "selected": True})
        return {
            "segment": segment,
            "tier": rung,
            "model": mid,
            "source": "role-preferred",
            "released": model_taxonomy.released(mid),
            "resolution_path": resolution_path,
        }

    # Every preferred candidate is unavailable (or the cell is empty).
    # Generative-Reasoning roles inherit the legacy ladder floor walk.
    legacy_token = {v: k for k, v in model_taxonomy.legacy_aliases().items()}.get(rung)
    if segment == "generative_reasoning" and legacy_token:
        base = resolve_with_tier_fallback(
            tier=legacy_token, workdir=wd, unavailable=unavail
        )
        base["segment"] = segment
        base["resolution_path"] = resolution_path + [
            {"model": base.get("model"), "via": base.get("source")}
        ]
        return base

    # Off-ladder specialist / dormant role with nothing available.
    return {
        "segment": segment,
        "tier": rung,
        "model": None,
        "source": "unresolved",
        "resolution_path": resolution_path,
    }


def has_override(
    *,
    tier: str,
    workdir: Path,
    config_path: Path | None = None,
    state_path: Path | None = None,
) -> bool:
    return bool(
        resolve_model(
            tier=tier,
            workdir=workdir,
            config_path=config_path,
            state_path=state_path,
        ).get("configured")
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".")
    p.add_argument("--tier", default=None, choices=sorted(TIERS))
    p.add_argument("--fallback", default=None)
    p.add_argument(
        "--unavailable",
        default=None,
        help="Comma-separated model ids that are unavailable. When set, "
        "resolution applies the standing TIER_FALLBACK policy "
        "(frontier never resolves below thinking).",
    )
    p.add_argument("--config", default=None, help="Override config.json path.")
    p.add_argument("--state", default=None, help="Override state.json path.")
    p.add_argument("--require", action="store_true", help="Exit 1 if unresolved.")
    p.add_argument("--plain", action="store_true", help="Print only the model id.")
    p.add_argument("--json", action="store_true", help="Print a JSON envelope.")
    p.add_argument(
        "--list-models",
        action="store_true",
        help="List the selectable models per tier (the registry) and exit. "
        "Honors --tier to filter and --json for machine output.",
    )
    args = p.parse_args(argv)

    if args.list_models:
        registry = registered_models(args.tier)
        if args.json:
            print(json.dumps(registry, indent=2, sort_keys=True))
        else:
            for tier in sorted(registry):
                print(f"{tier}:")
                for entry in registry[tier]:
                    flag = " (default)" if entry["status"] == "default" else f" [{entry['status']}]"
                    print(f"  {entry['id']:<22} {entry['provider']:<10} {entry['label']}{flag}")
        return 0

    if not args.tier:
        p.error("--tier is required unless --list-models is given")

    if args.unavailable is not None:
        unavailable = {m.strip() for m in args.unavailable.split(",") if m.strip()}
        result = resolve_with_tier_fallback(
            tier=args.tier,
            workdir=Path(args.workdir),
            unavailable=unavailable,
            fallback=args.fallback,
            config_path=Path(args.config) if args.config else None,
            state_path=Path(args.state) if args.state else None,
        )
    else:
        result = resolve_model(
            tier=args.tier,
            workdir=Path(args.workdir),
            fallback=args.fallback,
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
