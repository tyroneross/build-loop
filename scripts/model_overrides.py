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

TIERS = {"frontier", "thinking", "code", "pattern"}

TIER_DEFAULTS = {
    "frontier": "fable",
    "thinking": "opus",
    "code": "sonnet",
    "pattern": "haiku",
}

# Capability ordering, highest first. The single source of truth for "is tier A
# at or above tier B" — used by the floor clamp so the frontier-never-below-
# thinking invariant is enforced no matter HOW a model was selected (config
# override, in-tier walk, or cross-tier fallback), not only inside the
# TIER_FALLBACK walk. Lower index == higher capability.
TIER_ORDER = ("frontier", "thinking", "code", "pattern")
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
TIER_FALLBACK = {
    "frontier": "thinking",
    "thinking": "code",
    "code": "pattern",
    "pattern": None,
}

# Selectable models per tier. TIER_DEFAULTS (above) is the Anthropic mapping used
# when nothing is configured; this registry is the broader set of models known to
# fit each tier's contract and therefore safe to select via
# `.build-loop/config.json` modelOverrides[tier] or a per-dispatch model id.
# `status`: default (the tier's Anthropic fallback) | verified (cross-vendor,
# advisory) | local. Override resolution still accepts ANY string — the registry
# is advisory (powers `--list-models` + the `registered` flag), never a gate.
# Cross-vendor cells are best-effort; confirm current benchmarks before swapping.
# Canonical detail + swap recipes: references/model-tier-mapping.md.
MODEL_REGISTRY: dict[str, list[dict[str, str]]] = {
    "frontier": [
        {"id": "fable", "provider": "anthropic", "label": "Fable 5", "status": "default"},
        {"id": "gpt-5.5", "provider": "openai", "label": "GPT-5.5 (Codex)", "status": "verified"},
        {"id": "gpt-5.4", "provider": "openai", "label": "GPT-5.4", "status": "verified"},
    ],
    "thinking": [
        {"id": "opus", "provider": "anthropic", "label": "Opus 4.8", "status": "default"},
        {"id": "gpt-5.4", "provider": "openai", "label": "GPT-5.4", "status": "verified"},
        {"id": "gemini-2.5-pro", "provider": "google", "label": "Gemini 2.5 Pro", "status": "verified"},
    ],
    "code": [
        {"id": "sonnet", "provider": "anthropic", "label": "Sonnet 4.6", "status": "default"},
        {"id": "gpt-5.4-mini", "provider": "openai", "label": "GPT-5.4 Mini", "status": "verified"},
        {"id": "gemini-2.5-flash", "provider": "google", "label": "Gemini 2.5 Flash", "status": "verified"},
        {"id": "qwen2.5-coder-32b", "provider": "local", "label": "Qwen2.5-Coder 32B", "status": "local"},
    ],
    "pattern": [
        {"id": "haiku", "provider": "anthropic", "label": "Haiku 4.5", "status": "default"},
        {"id": "gpt-5-nano", "provider": "openai", "label": "GPT-5 Nano", "status": "verified"},
        {"id": "gemini-flash-lite", "provider": "google", "label": "Gemini Flash Lite", "status": "verified"},
        {"id": "llama3.2-3b", "provider": "local", "label": "Llama 3.2 3B", "status": "local"},
    ],
}


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

    unavailable = set(unavailable or ())

    base = resolve_model(
        tier=tier,
        workdir=workdir,
        fallback=fallback,
        config_path=config_path,
        state_path=state_path,
    )

    model = base.get("model")
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
