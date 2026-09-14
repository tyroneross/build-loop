#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Map coding-host identity to dispatchable providers and host Task slugs; autodetect new fused-effort model ids.
#   application: meta
#   status: active
"""Host dispatch map — a host is not a provider.

Cursor dispatches Anthropic, OpenAI, xAI, Google, and Cursor-native (Composer)
ids in one session. Treating ``--host cursor`` as provider ``cursor`` made every
role unresolved. This module is the data-driven expansion + slug adapter.

Autodetect: host Task slugs fuse model + effort (``gpt-5.6-sol-medium``,
``cursor-grok-4.6-high-fast``). Strip the host prefix and effort suffix and
match the stem against the taxonomy. A new *variant* of a known family inherits;
a genuinely new family still goes through ``classify_model_tier.py lookup``.

Reachable-slug ingest (optional): ``BUILD_LOOP_HOST_MODELS`` or
``.build-loop/host-models.json`` lists the slugs THIS session can dispatch
(Cursor Task lists follow the model picker). ``detect_host_models`` classifies
each unknown slug without a vendor API call.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:  # pragma: no cover - import shim
    import model_taxonomy
except ImportError:  # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import model_taxonomy  # type: ignore[no-redefine]

HOST_MODELS_FILENAME = "host-models.json"
HOST_MODELS_ENV = "BUILD_LOOP_HOST_MODELS"

# Longest-first so "thinking-high-fast" wins over "fast".
_EFFORT_SUFFIXES: tuple[str, ...] = (
    "thinking-high-fast",
    "thinking-xhigh-fast",
    "thinking-medium-fast",
    "thinking-low-fast",
    "thinking-high",
    "thinking-xhigh",
    "thinking-medium",
    "thinking-low",
    "high-fast",
    "xhigh-fast",
    "medium-fast",
    "low-fast",
    "thinking",
    "xhigh",
    "high",
    "medium",
    "low",
    "max",
    "fast",
)
_HOST_PREFIXES: tuple[str, ...] = ("cursor-",)


def hosts() -> dict[str, dict[str, Any]]:
    """Host rows from the taxonomy (doc keys stripped)."""
    raw = model_taxonomy.taxonomy().get("hosts", {})
    if not isinstance(raw, dict):
        return {}
    return {
        k: dict(v)
        for k, v in raw.items()
        if not k.startswith("_") and isinstance(v, dict)
    }


def providers_for_host(host: str | None) -> set[str] | None:
    """Dispatchable providers for a coding-host family, or None if unknown."""
    if not host:
        return None
    row = hosts().get(host.strip().lower())
    if not row:
        return None
    providers = row.get("providers")
    if not isinstance(providers, list) or not providers:
        return None
    return {str(p).strip().lower() for p in providers if str(p).strip()}


def host_family_for_token(token: str) -> str | None:
    """The host family a ``--host`` token names, or None if it is a raw provider."""
    key = token.strip().lower()
    if not key:
        return None
    table = hosts()
    if key in table:
        return key
    for family, row in table.items():
        aliases = row.get("aliases") or []
        if not isinstance(aliases, list):
            continue
        if any(str(a).strip().lower() == key for a in aliases):
            return family
    return None


def expand_host_tokens(raw: str) -> set[str]:
    """Expand a comma-separated ``--host`` / ``--host-providers`` value.

    Host family names (``cursor``, ``codex``, ``claude``) expand to the
    providers that host can dispatch. Unknown tokens pass through as provider
    names so ``anthropic,openai`` keeps working.
    """
    out: set[str] = set()
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        family = host_family_for_token(token)
        providers = providers_for_host(family) if family else None
        if providers:
            out.update(providers)
        else:
            out.add(token)
    return out


def is_host_provider_set(providers: set[str] | frozenset[str] | None, family: str) -> bool:
    """True when ``providers`` is exactly that host family's provider set."""
    expected = providers_for_host(family)
    if expected is None or providers is None:
        return False
    return {str(p).strip().lower() for p in providers} == expected


def split_host_slug(model_id: str) -> dict[str, Any]:
    """Peel a fused host Task slug into stem / effort / variant / host prefix."""
    raw = (model_id or "").strip().lower()
    host_prefix = ""
    rest = raw
    for prefix in _HOST_PREFIXES:
        if rest.startswith(prefix):
            host_prefix = prefix.rstrip("-")
            rest = rest[len(prefix):]
            break
    effort = ""
    variant = ""
    stem = rest
    for suffix in _EFFORT_SUFFIXES:
        token = "-" + suffix
        if stem.endswith(token):
            stem = stem[: -len(token)]
            if suffix == "fast":
                variant = "fast"
            elif suffix.endswith("-fast"):
                variant = "fast"
                effort = suffix[: -len("-fast")]
                if effort.startswith("thinking-"):
                    effort = effort[len("thinking-"):]
            elif suffix.startswith("thinking-"):
                effort = suffix[len("thinking-"):]
            elif suffix == "thinking":
                effort = "high"
            else:
                effort = suffix
            break
    return {
        "raw": raw,
        "stem": stem,
        "effort": effort,
        "variant": variant,
        "host_prefix": host_prefix,
    }


def _match_stem(stem: str, models: dict[str, dict[str, Any]]) -> str | None:
    """Exact id or alias match for a peeled stem. No version-fuzzy mapping."""
    if not stem:
        return None
    low = stem.lower()
    if low in models:
        return low
    for mid, meta in models.items():
        if mid.lower() == low:
            return mid
        for alias in meta.get("aliases", []) or []:
            if str(alias).lower() == low:
                return mid
    return None


def canonical_for_slug(model_id: str, models: dict[str, dict[str, Any]] | None = None) -> str | None:
    """Taxonomy id a host slug belongs to, or None when the family is unknown."""
    if models is None:
        models = {
            k: v
            for k, v in model_taxonomy.taxonomy().get("models", {}).items()
            if not k.startswith("_") and isinstance(v, dict)
        }
    parts = split_host_slug(model_id)
    return _match_stem(parts["stem"], models)


def inherit_from_slug(
    model_id: str, models: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Seed metadata for a fused host slug of a known family, or None."""
    canonical = canonical_for_slug(model_id, models)
    if not canonical:
        return None
    # Exact id/alias already resolved by model_meta before this is called.
    if canonical.lower() == (model_id or "").strip().lower():
        return None
    meta = models.get(canonical)
    if not isinstance(meta, dict):
        return None
    parts = split_host_slug(model_id)
    if parts["stem"].lower() == (model_id or "").strip().lower():
        return None  # nothing was peeled; vendor+family inherit owns this case
    out = dict(meta)
    out["inherited_from"] = canonical
    out["status"] = "inherited"
    out["aliases"] = [model_id]
    out["label"] = f"{model_id} (inherited from {meta.get('label', canonical)})"
    if parts["effort"]:
        out["host_effort"] = parts["effort"]
    if parts["variant"]:
        out["host_variant"] = parts["variant"]
    return out


def _looks_fused(slug: str) -> bool:
    low = slug.strip().lower()
    return any(low.endswith("-" + suffix) for suffix in _EFFORT_SUFFIXES) or low.startswith(
        _HOST_PREFIXES
    )


def dispatch_id(
    canonical: str | None,
    *,
    effort: str | None = None,
    host_family: str | None = None,
    workdir: Path | None = None,
) -> str | None:
    """Host-dispatch token for a taxonomy id.

    Non-Cursor hosts keep the canonical short token (``opus``, ``gpt-5.6-sol``).
    Cursor Task requires a fused slug from the session allowlist shape.
    When ``BUILD_LOOP_HOST_MODELS`` or host-models.json is set, pick a slug
    that is in that allowlist.
    """
    if not canonical:
        return None
    family = (host_family or "").strip().lower()
    if family != "cursor":
        return canonical
    meta = model_taxonomy.model_meta(canonical) or {}
    wanted = (effort or "").strip().lower()
    aliases = [canonical, *(str(a) for a in (meta.get("aliases") or []) if a)]
    fused = [a for a in aliases if _looks_fused(a)]
    chosen = None
    if wanted:
        thinking = f"thinking-{wanted}"
        scored: list[tuple[int, str]] = []
        for slug in fused:
            parts = split_host_slug(slug)
            score = 0
            if parts["effort"] == wanted:
                score += 4
            if thinking in slug:
                score += 2
            if wanted in slug.split("-"):
                score += 1
            if score:
                scored.append((score, slug))
        if scored:
            scored.sort(key=lambda item: (-item[0], item[1]))
            chosen = scored[0][1]
    if chosen is None and fused:
        chosen = fused[0]
    if chosen is None:
        chosen = _synthesize_cursor_slug(canonical, meta, wanted)
    allowed = {s.lower() for s in load_host_model_slugs(workdir)}
    if not allowed:
        return chosen
    if chosen.lower() in allowed:
        return chosen
    for slug in fused:
        if slug.lower() in allowed:
            return slug
    return None


def _synthesize_cursor_slug(
    canonical: str, meta: dict[str, Any], effort: str
) -> str:
    provider = str(meta.get("provider") or "").strip().lower()
    effort = effort or "high"
    if provider == "anthropic":
        base = next(
            (
                str(a)
                for a in (meta.get("aliases") or [])
                if str(a).startswith("claude-")
            ),
            f"claude-{canonical}",
        )
        if "thinking" in base:
            return base
        return f"{base}-thinking-{effort}"
    if provider == "openai":
        return f"{canonical}-{effort}"
    if provider == "xai":
        return f"cursor-{canonical}-{effort}-fast"
    if provider == "cursor":
        return f"{canonical}-fast"
    if provider == "google":
        return f"{canonical}-{effort}"
    return canonical


def load_host_model_slugs(workdir: Path | None = None) -> list[str]:
    """Session-reachable slugs from env and ``.build-loop/host-models.json``."""
    found: list[str] = []
    env = os.environ.get(HOST_MODELS_ENV, "")
    if env.strip():
        found.extend(p.strip() for p in env.split(",") if p.strip())
    if workdir is not None:
        path = workdir.expanduser().resolve() / ".build-loop" / HOST_MODELS_FILENAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            slugs = data.get("slugs")
            if isinstance(slugs, list):
                found.extend(str(s).strip() for s in slugs if str(s).strip())
        elif isinstance(data, list):
            found.extend(str(s).strip() for s in data if str(s).strip())
    # Preserve order, drop dupes.
    out: list[str] = []
    seen: set[str] = set()
    for slug in found:
        key = slug.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(slug)
    return out


def detect_host_models(workdir: Path | None = None) -> dict[str, Any]:
    """Classify every session slug: known, inherited family, or needs search."""
    slugs = load_host_model_slugs(workdir)
    known: list[dict[str, Any]] = []
    inherited: list[dict[str, Any]] = []
    unknown: list[str] = []
    for slug in slugs:
        meta = model_taxonomy.model_meta(slug)
        if meta is None:
            unknown.append(slug)
            continue
        row = {
            "slug": slug,
            "canonical": meta.get("inherited_from")
            or canonical_for_slug(slug)
            or slug,
            "provider": meta.get("provider"),
            "tier": meta.get("tier"),
            "segment": meta.get("segment"),
            "status": meta.get("status"),
        }
        if meta.get("status") == "inherited":
            inherited.append(row)
        else:
            known.append(row)
    return {
        "slugs": slugs,
        "known": known,
        "inherited": inherited,
        "needs_classification": unknown,
        "source": HOST_MODELS_ENV + "|" + HOST_MODELS_FILENAME,
    }
