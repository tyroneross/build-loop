#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Host-neutral READ/QUERY CLI over the model-routing index, so a non-Python host can ask what model to use without importing build-loop.
#   application: meta
#   status: active
"""Host-neutral read surface over the model-routing index.

WHY THIS EXISTS. ``references/model-taxonomy.json`` is already the single source
of truth for model routing, and ``model_taxonomy.py`` / ``model_resolver.py`` /
``model_overrides.py`` / ``resolve_agent_model.py`` already own every routing
DECISION. But reaching them required a build-loop checkout, a Python import, and
a ``sys.path`` shim — so Codex, a shell profile, a terminal agent, or a local
model runner had no contract to ask "what model should I use for tier X / role Y
on host Z?". This module is that contract: a stdlib-only, network-free CLI that
is a THIN SHELL over the existing resolvers.

It adds NO routing logic and NO second registry. Every answer comes from the
existing loader:

  * ``model_taxonomy``       — tier ladder, legacy aliases, segments, preferred
                               lists, per-model metadata.
  * ``model_resolver``       — ``resolve`` (legacy tier axis) / ``resolve_role``
                               (two-axis) incl. persistent availability +
                               host-provider reachability.
  * ``model_overrides``      — registry order, tier-fallback graph, floor walk.
  * ``resolve_agent_model``  — an agent's ``(segment, tier)`` frontmatter role.

If this CLI ever disagrees with those, THIS CLI is wrong.

Subcommands::

    model_index.py resolve --tier <t> [--segment <s>] [--host <h>] [--json]
    model_index.py tiers [--json]
    model_index.py segments [--json]
    model_index.py models [--tier T] [--segment S] [--provider P] [--status S] [--json]
    model_index.py export [--format json|env|toml]
    model_index.py agent <name> [--host <h>] [--json]

Exit codes: 0 success · 1 not found / unresolved · 2 hard error.

Every ``--json`` payload carries ``schema_version`` + ``fingerprint`` so a
consumer can cache the answer and detect staleness. The fingerprint algorithm is
byte-identical to ``RossLabs-AI-Assistant/registry/sync.py::_json_fingerprint``
(sha256 over the canonically-serialized parsed JSON, first 16 hex chars), so the
two indexes agree on whether the taxonomy changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# Resolve the repo from __file__, never cwd — the CLI must answer identically
# from any working directory (that is the whole point of a host-neutral surface).
SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
TAXONOMY_PATH = REPO_ROOT / "references" / "model-taxonomy.json"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import model_overrides  # noqa: E402
import model_resolver  # noqa: E402
import model_taxonomy  # noqa: E402
import resolve_agent_model  # noqa: E402

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_ERROR = 2

ENV_PREFIX = "BUILDLOOP_MODEL_"
FINGERPRINT_ALGORITHM = "sha256(json.dumps(taxonomy,sort_keys,compact,utf-8))[:16]"

# Host token -> dispatchable provider. Accepts the provider name itself, the
# coding-host name a non-Claude consumer would naturally type, or "any".
_HOST_ALIASES = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "claude_code": "anthropic",
    "claude-code": "anthropic",
    "openai": "openai",
    "codex": "openai",
    "chatgpt": "openai",
    "google": "google",
    "gemini": "google",
    "gemini_cli": "google",
    "gemini-cli": "google",
    # The taxonomy's provider token for locally-run models is "local"; accept the
    # runner name a consumer would type and fold it to that, so `--host ollama`
    # actually matches llama3.2-3b / qwen2.5-coder-32b instead of nothing.
    "local": "local",
    "ollama": "local",
    "lmstudio": "local",
    "mlx": "local",
}


class IndexError_(Exception):
    """A lookup that names something the index does not contain (exit 1)."""


# --------------------------------------------------------------------------
# Fingerprint / staleness contract
# --------------------------------------------------------------------------

def fingerprint(taxonomy: dict[str, Any] | None = None) -> str:
    """Content fingerprint of the taxonomy — MATCHES the AI-Assistant registry.

    Deliberately identical to ``registry/sync.py::_json_fingerprint``: sha256 of
    the parsed JSON re-serialized canonically (``sort_keys=True``,
    ``separators=(",", ":")``, ``ensure_ascii=False``), truncated to 16 hex
    chars. Fingerprinting the PARSED value rather than the file bytes means
    reformatting the file (whitespace, key order) does not falsely read as a
    routing change, and both indexes reach the same verdict on staleness.
    """
    data = model_taxonomy.taxonomy() if taxonomy is None else taxonomy
    payload = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def envelope(command: str, **payload: Any) -> dict[str, Any]:
    """Wrap a payload with the staleness contract every consumer reads.

    The path field is named ``taxonomy_path``, not ``source``: every resolver
    envelope already uses ``source`` to mean "how this model was chosen", and
    ``do_agent`` splats a resolver envelope straight in. Reusing ``source`` here
    made the resolver's value silently overwrite the contract field. A collision
    guard makes that class of bug fail loudly instead of shipping a wrong-looking
    payload.
    """
    out: dict[str, Any] = {
        "schema_version": model_taxonomy.taxonomy().get("schema_version"),
        "fingerprint": fingerprint(),
        "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        "taxonomy_path": str(TAXONOMY_PATH),
        "command": command,
    }
    collisions = set(out) & set(payload)
    if collisions:
        raise RuntimeError(
            f"payload key(s) {sorted(collisions)} would overwrite the staleness contract"
        )
    out.update(payload)
    return out


def _emit(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


# --------------------------------------------------------------------------
# Shared lookups
# --------------------------------------------------------------------------

def legacy_token_for_rung(rung: str) -> str | None:
    """The legacy tier token for a ladder rung (T1 -> "frontier"), or None."""
    return {v: k for k, v in model_taxonomy.legacy_aliases().items()}.get(rung)


def parse_host(raw: str | None) -> Any:
    """Map ``--host`` to the resolver's ``host_providers`` argument.

    None -> None (the resolver's default: config -> detected host).
    "any" -> ``model_resolver.HOST_FILTER_DISABLED`` (no filter).
    else  -> a provider set, resolving host aliases (codex -> openai).
    """
    if raw is None or not raw.strip():
        return None
    if raw.strip().lower() == "any":
        return model_resolver.HOST_FILTER_DISABLED
    out: set[str] = set()
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        out.add(_HOST_ALIASES.get(token, token))
    return out or None


def _effective_host_providers(host: Any, workdir: Path) -> set[str] | None:
    """The provider allowlist actually in force for this call (for reporting)."""
    if host is model_resolver.HOST_FILTER_DISABLED:
        return None
    if host is not None:
        return {str(p).strip().lower() for p in host}
    return model_resolver.load_host_providers(workdir)


def _provider_of(model_id: str) -> str | None:
    meta = model_taxonomy.model_meta(model_id) or {}
    provider = meta.get("provider")
    return str(provider).strip().lower() if provider else None


def _chain_entry(
    model_id: str,
    *,
    tier: str | None,
    via: str,
    unavailable: set[str],
    host_providers: set[str] | None,
    selected_model: str | None,
) -> dict[str, Any]:
    provider = _provider_of(model_id)
    alias = model_overrides.normalize_model_id(model_id)
    reachable = True if (host_providers is None or not provider) else provider in host_providers
    return {
        "model": model_id,
        "tier": tier,
        "provider": provider,
        "via": via,
        "available": model_id not in unavailable and alias not in unavailable,
        "host_reachable": reachable,
        "selected": model_id == selected_model or alias == selected_model,
    }


def _fallback_chain(
    *,
    segment: str | None,
    rung: str,
    legacy_token: str | None,
    workdir: Path,
    unavailable: set[str],
    host_providers: set[str] | None,
    selected_model: str | None,
) -> list[dict[str, Any]]:
    """The ORDERED candidate walk the resolver performs, rendered for a consumer.

    Built from the same primitives the resolver walks — ``preferred`` +
    ``break_ties_by_recency`` for the role axis, ``in_tier_candidates`` +
    ``TIER_FALLBACK`` for the legacy tier axis — so the chain cannot drift from
    the decision. It reports candidates, not a second decision.
    """
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(model_id: str, tier: str | None, via: str) -> None:
        if not model_id or model_id in seen:
            return
        seen.add(model_id)
        chain.append(
            _chain_entry(
                model_id,
                tier=tier,
                via=via,
                unavailable=unavailable,
                host_providers=host_providers,
                selected_model=selected_model,
            )
        )

    if segment:
        for mid in model_taxonomy.break_ties_by_recency(
            model_taxonomy.preferred(segment, rung)
        ):
            add(mid, rung, "role-preferred")

    if legacy_token:
        tier_cache = model_resolver.load_tier_cache(workdir)
        for mid in model_resolver.in_tier_candidates(
            legacy_token, tier_cache, host_providers
        ):
            add(mid, legacy_token, "in-tier-chain")
        # The standing floor walk: frontier walks AT MOST one edge (the hard
        # invariant lives in model_overrides.resolve_with_tier_fallback).
        current = legacy_token
        steps = 1 if legacy_token == "frontier" else len(model_overrides.TIERS)
        for _ in range(steps):
            nxt = model_overrides.TIER_FALLBACK.get(current)
            if not nxt:
                break
            for entry in model_overrides.MODEL_REGISTRY.get(nxt, []):
                add(entry.get("id", ""), nxt, "tier-fallback")
            current = nxt
    return chain


# --------------------------------------------------------------------------
# resolve
# --------------------------------------------------------------------------

def do_resolve(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    try:
        rung = model_taxonomy.normalize_tier(args.tier)
    except ValueError as exc:
        raise IndexError_(str(exc)) from exc

    segment = args.segment
    if segment and segment not in model_taxonomy.segments():
        raise IndexError_(
            f"unknown segment {segment!r}; expected one of "
            f"{sorted(model_taxonomy.segments())}"
        )

    host = parse_host(args.host)
    host_providers = _effective_host_providers(host, workdir)
    legacy_token = legacy_token_for_rung(rung)

    # Route to the SAME entrypoint the dispatcher uses, so answers agree:
    #   segment given            -> two-axis resolve_role
    #   literal legacy token     -> single-axis resolve (the legacy default path)
    #   canonical ladder rung    -> resolve_role with the implicit segment
    #
    # A canonical T2 is not the legacy token ``thinking``. Keeping that
    # distinction makes per-tier resolve agree with the canonical export map.
    if segment:
        env = model_resolver.resolve_role(
            segment=segment, tier=rung, workdir=workdir, host_providers=host
        )
        axis = "role"
    elif model_taxonomy.is_legacy_tier(args.tier):
        env = model_resolver.resolve(
            tier=legacy_token, workdir=workdir, host_providers=host
        )
        axis = "tier"
    else:
        segment = "generative_reasoning"
        env = model_resolver.resolve_role(
            segment=segment, tier=rung, workdir=workdir, host_providers=host
        )
        axis = "role-implicit-segment"

    model = env.get("model")
    unavailable = model_overrides.expand_unavailable(
        model_resolver.load_unavailable(workdir)
    )
    chain = _fallback_chain(
        segment=segment,
        rung=rung,
        legacy_token=legacy_token,
        workdir=workdir,
        unavailable=unavailable,
        host_providers=host_providers,
        selected_model=model,
    )

    why: list[str] = [f"axis={axis}", f"source={env.get('source')}"]
    if host_providers is not None:
        why.append(f"host-providers={sorted(host_providers)}")
    else:
        why.append("host-providers=unfiltered")
    if unavailable:
        why.append(f"declared-unavailable={sorted(unavailable)}")
    if env.get("fallback_tier"):
        why.append(f"fell back to tier={env['fallback_tier']}")

    payload = {
        "tier_requested": args.tier,
        "tier": rung,
        "legacy_tier": legacy_token,
        "segment": segment,
        "model": model,
        "provider": _provider_of(model) if model else None,
        "resolver_source": env.get("source"),
        "axis": axis,
        "host_providers": sorted(host_providers) if host_providers else None,
        "fallback_chain": chain,
        "resolution_path": env.get("resolution_path", []),
        "prompting_profile": model_taxonomy.prompting_profile(rung),
        "preferred_models": env.get("preferred_models", []),
        "preferred_effort": env.get("preferred_effort"),
        "resolved": env.get("resolved", bool(model)),
        "why": why,
    }

    if args.json:
        _emit(envelope("resolve", **payload))
    else:
        label = f"{segment}/{rung}" if segment else rung
        print(f"model:    {model or '(unresolved)'}")
        print(f"role:     {label}" + (f" (legacy: {legacy_token})" if legacy_token else ""))
        print(f"source:   {env.get('source')}")
        print("why:      " + "; ".join(why))
        print("chain:")
        for entry in chain:
            mark = "->" if entry["selected"] else "  "
            flags = []
            if not entry["available"]:
                flags.append("unavailable")
            if not entry["host_reachable"]:
                flags.append("host-unreachable")
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            print(
                f"  {mark} {entry['model']:<24} {str(entry['provider'] or '-'):<10}"
                f" {entry['via']}{suffix}"
            )
    return EXIT_OK if payload["resolved"] else EXIT_NOT_FOUND


# --------------------------------------------------------------------------
# tiers / segments / models
# --------------------------------------------------------------------------

def do_tiers(args: argparse.Namespace) -> int:
    defs = model_taxonomy.taxonomy()["tiers"]["defs"]
    fallback = model_taxonomy.ladder_fallback()
    rungs = []
    for rung in model_taxonomy.tier_ladder():
        d = defs.get(rung, {}) if isinstance(defs.get(rung), dict) else {}
        rungs.append(
            {
                "tier": rung,
                "label": d.get("label"),
                "rank": d.get("rank"),
                "specialist": bool(d.get("specialist")),
                "legacy_alias": legacy_token_for_rung(rung),
                "fallback": fallback.get(rung),
            }
        )
    if args.json:
        _emit(
            envelope(
                "tiers",
                tier_ladder=list(model_taxonomy.tier_ladder()),
                legacy_aliases=model_taxonomy.legacy_aliases(),
                tiers=rungs,
            )
        )
    else:
        print(f"{'TIER':<6} {'LEGACY':<9} {'FALLBACK':<9} {'RANK':<5} LABEL")
        for r in rungs:
            print(
                f"{r['tier']:<6} {str(r['legacy_alias'] or '-'):<9} "
                f"{str(r['fallback'] or '-'):<9} {str(r['rank']):<5} {r['label']}"
            )
    return EXIT_OK


def do_segments(args: argparse.Namespace) -> int:
    rows = []
    for sid, meta in sorted(model_taxonomy.segments().items()):
        meta = meta if isinstance(meta, dict) else {}
        rows.append(
            {
                "segment": sid,
                "label": meta.get("label"),
                "status": meta.get("status", "unknown"),
                "subsegments": meta.get("subsegments", []),
            }
        )
    if args.json:
        _emit(
            envelope(
                "segments",
                segments=rows,
                active_segments=model_taxonomy.active_segments(),
            )
        )
    else:
        print(f"{'SEGMENT':<26} {'STATUS':<9} LABEL")
        for r in rows:
            print(f"{r['segment']:<26} {r['status']:<9} {r['label']}")
    return EXIT_OK


def do_models(args: argparse.Namespace) -> int:
    tier_filter = None
    if args.tier:
        try:
            tier_filter = model_taxonomy.normalize_tier(args.tier)
        except ValueError as exc:
            raise IndexError_(str(exc)) from exc
    if args.segment and args.segment not in model_taxonomy.segments():
        raise IndexError_(
            f"unknown segment {args.segment!r}; expected one of "
            f"{sorted(model_taxonomy.segments())}"
        )

    raw = model_taxonomy.taxonomy().get("models", {})
    rows: list[dict[str, Any]] = []
    for mid, meta in sorted(raw.items()):
        if mid.startswith("_") or not isinstance(meta, dict):
            continue
        if tier_filter and meta.get("tier") != tier_filter:
            continue
        if args.segment and meta.get("segment") != args.segment:
            continue
        if args.provider and str(meta.get("provider", "")).lower() != args.provider.lower():
            continue
        if args.status and str(meta.get("status", "")).lower() != args.status.lower():
            continue
        rows.append(
            {
                "id": mid,
                "label": meta.get("label"),
                "provider": meta.get("provider"),
                "segment": meta.get("segment"),
                "tier": meta.get("tier"),
                "status": meta.get("status"),
                "released": meta.get("released"),
                "aliases": meta.get("aliases", []),
                "tags": meta.get("tags", []),
            }
        )

    if args.json:
        _emit(
            envelope(
                "models",
                filters={
                    "tier": tier_filter,
                    "segment": args.segment,
                    "provider": args.provider,
                    "status": args.status,
                },
                count=len(rows),
                models=rows,
            )
        )
    else:
        if not rows:
            print("(no models match)")
        else:
            print(f"{'ID':<30} {'PROVIDER':<11} {'TIER':<6} {'STATUS':<10} SEGMENT")
            for r in rows:
                print(
                    f"{r['id']:<30} {str(r['provider'] or '-'):<11} "
                    f"{str(r['tier'] or '-'):<6} {str(r['status'] or '-'):<10} "
                    f"{r['segment'] or '-'}"
                )
    return EXIT_NOT_FOUND if not rows else EXIT_OK


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def env_var_name(tier: str) -> str:
    """``BUILDLOOP_MODEL_<TIER>`` — ladder rungs keep their rung name (T-S ->
    ``T_S``) so a shell can read a rung directly, not just a legacy token."""
    return ENV_PREFIX + tier.upper().replace("-", "_")


def build_export_map(workdir: Path, host: Any) -> list[dict[str, Any]]:
    """The whole tier -> model map, resolved through the existing resolvers.

    Both vocabularies are emitted: the four legacy tokens (the surface most
    consumers already speak) and every ladder rung (the canonical axis). A rung
    with no resolvable model is reported with ``model: null`` rather than
    omitted, so a consumer can tell "empty cell" from "I forgot to look".
    """
    rows: list[dict[str, Any]] = []
    for token in ("frontier", "thinking", "code", "pattern"):
        env = model_resolver.resolve(tier=token, workdir=workdir, host_providers=host)
        rows.append(
            {
                "tier": token,
                "vocabulary": "legacy",
                "ladder_tier": model_taxonomy.normalize_tier(token),
                "model": env.get("model"),
                "provider": _provider_of(env.get("model") or "") if env.get("model") else None,
                "source": env.get("source"),
                "preferred_models": env.get("preferred_models", []),
                "preferred_effort": env.get("preferred_effort"),
                "resolved": env.get("resolved", bool(env.get("model"))),
                "env_var": env_var_name(token),
            }
        )
    for rung in model_taxonomy.tier_ladder():
        env = model_resolver.resolve_role(
            segment="generative_reasoning",
            tier=rung,
            workdir=workdir,
            host_providers=host,
        )
        rows.append(
            {
                "tier": rung,
                "vocabulary": "ladder",
                "ladder_tier": rung,
                "model": env.get("model"),
                "provider": _provider_of(env.get("model") or "") if env.get("model") else None,
                "source": env.get("source"),
                "preferred_models": env.get("preferred_models", []),
                "preferred_effort": env.get("preferred_effort"),
                "resolved": env.get("resolved", bool(env.get("model"))),
                "env_var": env_var_name(rung),
            }
        )
    return rows


def do_export(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    host = parse_host(args.host)
    rows = build_export_map(workdir, host)
    fmt = "json" if args.json else args.format

    if fmt == "json":
        _emit(
            envelope(
                "export",
                format="json",
                models={r["tier"]: r["model"] for r in rows},
                entries=rows,
            )
        )
    elif fmt == "env":
        print("# generated by scripts/model_index.py export --format env")
        print(f"# source: {TAXONOMY_PATH}")
        print(f"{ENV_PREFIX}SCHEMA_VERSION={model_taxonomy.taxonomy().get('schema_version')}")
        print(f"{ENV_PREFIX}FINGERPRINT={fingerprint()}")
        for r in rows:
            if r["model"]:
                print(f"{r['env_var']}={r['model']}")
            else:
                print(f"# {r['env_var']}= (unresolved)")
    elif fmt == "toml":
        print("# generated by scripts/model_index.py export --format toml")
        print(f'schema_version = "{model_taxonomy.taxonomy().get("schema_version")}"')
        print(f'fingerprint = "{fingerprint()}"')
        print(f'source = "{TAXONOMY_PATH}"')
        print("")
        print("[models]")
        for r in rows:
            key = r["tier"].replace("-", "_")
            if r["model"]:
                print(f'{key} = "{r["model"]}"')
            else:
                print(f"# {key} = unresolved")
    else:  # pragma: no cover - argparse choices prevent this
        raise IndexError_(f"unknown export format {fmt!r}")
    return EXIT_OK


# --------------------------------------------------------------------------
# agent
# --------------------------------------------------------------------------

def do_agent(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    host = parse_host(args.host)
    try:
        result = resolve_agent_model.resolve(
            agent=args.name, workdir=workdir, host_providers=host
        )
    except FileNotFoundError as exc:
        raise IndexError_(str(exc)) from exc

    model = result.get("model")
    if args.json:
        _emit(envelope("agent", **result))
    else:
        print(f"agent:    {result.get('agent')}")
        print(f"model:    {model or '(unresolved)'}")
        print(f"role:     {result.get('segment')}/{result.get('tier')}")
        print(f"source:   {result.get('source')}")
    return EXIT_OK if model else EXIT_NOT_FOUND


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="Machine-readable output.")
    common.add_argument(
        "--workdir",
        default=".",
        help="Project whose .build-loop/ availability + overrides apply (default: cwd).",
    )

    host_help = (
        "Host that will dispatch the model: a provider (anthropic/openai/google), "
        "a host name (claude/codex/gemini), a comma-separated list, or 'any' to "
        "disable the filter. Default: detect the current host."
    )

    p = argparse.ArgumentParser(
        prog="model_index.py", description=__doc__.splitlines()[0]
    )
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("resolve", parents=[common], help="Resolve a tier/role to a model.")
    r.add_argument("--tier", required=True, help="Ladder rung (T0..T5, T-S) or legacy token.")
    r.add_argument("--segment", default=None, help="Work-role segment (two-axis resolve).")
    r.add_argument("--host", default=None, help=host_help)
    r.set_defaults(func=do_resolve)

    t = sub.add_parser("tiers", parents=[common], help="List the tier ladder + legacy aliases.")
    t.set_defaults(func=do_tiers)

    s = sub.add_parser("segments", parents=[common], help="List the segments.")
    s.set_defaults(func=do_segments)

    m = sub.add_parser("models", parents=[common], help="List/filter model rows.")
    m.add_argument("--tier", default=None)
    m.add_argument("--segment", default=None)
    m.add_argument("--provider", default=None)
    m.add_argument("--status", default=None)
    m.set_defaults(func=do_models)

    e = sub.add_parser("export", parents=[common], help="Emit the whole tier->model map.")
    e.add_argument("--format", default="json", choices=("json", "env", "toml"))
    e.add_argument("--host", default=None, help=host_help)
    e.set_defaults(func=do_export)

    a = sub.add_parser("agent", parents=[common], help="Resolve a build-loop agent's model.")
    a.add_argument("name", help="Agent name (the file is agents/<name>.md).")
    a.add_argument("--host", default=None, help=host_help)
    a.set_defaults(func=do_agent)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except IndexError_ as exc:
        print(json.dumps({"error": str(exc), "kind": "not-found"}), file=sys.stderr)
        return EXIT_NOT_FOUND
    except Exception as exc:  # noqa: BLE001 - a CLI boundary must not traceback
        print(json.dumps({"error": str(exc), "kind": "error"}), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
