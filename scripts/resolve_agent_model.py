#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Resolve a build-loop agent's (segment, tier) ROLE to a concrete dispatch model from the model index.
#   application: meta
#   status: active
"""Resolve an agent's `(segment, tier)` role to a dispatch model — the FRONT DOOR.

The orchestrator dispatches each subagent with a concrete model. The durable KEY
that decides which model is the agent's `(segment, tier)` ROLE in its frontmatter
(the index key into ``references/model-taxonomy.json``); the `model:` line is the
index-DERIVED recommended fallback, kept in sync by
``scripts/sync_agent_model_defaults.py``. This script reads an agent's frontmatter
and returns the model the LIVE index resolves the role to, so dispatch can OVERRIDE
the (possibly stale) `model:` frontmatter.

It REUSES the existing resolution path and reimplements NOTHING:
``model_resolver.resolve_role(segment, tier, workdir)`` already owns availability
fallback + host-provider reachability filter + the hybrid preferred-list walk +
release-recency tiebreak + the floor invariant. This module only:

  1. reads ``agents/<name>.md`` frontmatter (`segment`, `tier`, `model`), and
  2. applies the fallback chain when the role cannot be resolved.

No vendor API calls.

Resolution + fallback chain:

  * ``segment``/``tier`` == ``inherit``  -> ``{model: "inherit", source: "inherit"}``
    (an inherit agent flows the caller's model through; dispatch passes NO override).
  * else                                 -> ``resolve_role(segment, tier, workdir)``.
  * resolve yields no model               -> agent ``model:`` frontmatter (``source: frontmatter-fallback``).
  * still nothing + a known tier          -> tier default (``source: tier-default-fallback``).
  * nothing resolvable                    -> ``{model: None, source: unresolved}`` (exit 1 with ``--require``).

CLI::

    python3 scripts/resolve_agent_model.py <agent-name> [--workdir .] [--plain|--json]
    # --plain prints just the model id ("inherit" for inherit agents, "" if unresolved).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:  # pragma: no cover - import shim for direct + packaged execution
    import model_overrides
    import model_resolver
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import model_overrides  # type: ignore[no-redefine]
    import model_resolver  # type: ignore[no-redefine]

INHERIT = "inherit"
# agents/ sits one level up from scripts/.
_DEFAULT_AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"


def default_agents_dir() -> Path:
    return _DEFAULT_AGENTS_DIR


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse the simple ``key: value`` YAML frontmatter block.

    Agent frontmatter for the fields we read (`segment`, `tier`, `model`) is flat
    scalar — no nesting, no lists. A tiny stdlib parser avoids a yaml dependency
    and matches the repo's lightweight convention. Only top-level scalar keys are
    captured; block scalars (``description: |``) and list/dict values are skipped.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    in_block = False  # inside a `key: |` block scalar — skip its indented body
    for raw in lines[1:]:
        if raw.strip() == "---":
            break
        if in_block:
            # A block scalar's continuation lines are indented; a new top-level
            # ``key:`` at column 0 ends it.
            if raw and not raw[0].isspace():
                in_block = False
            else:
                continue
        if not raw or raw[0].isspace():
            continue  # indented (list item / nested) — not a top-level scalar
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        if value in ("|", ">", "|-", ">-"):
            in_block = True
            continue
        # Strip surrounding quotes on a simple scalar.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def read_agent_frontmatter(agent: str, agents_dir: Path) -> dict[str, str]:
    """Read ``agents/<agent>.md`` frontmatter, or raise FileNotFoundError."""
    path = agents_dir / f"{agent}.md"
    if not path.is_file():
        raise FileNotFoundError(f"agent file not found: {path}")
    return _parse_frontmatter(path.read_text(encoding="utf-8"))


def resolve(
    *,
    agent: str,
    workdir: Path,
    agents_dir: Path | None = None,
    extra_unavailable: set[str] | frozenset[str] | None = None,
    host_providers: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Resolve ``agent``'s frontmatter role to a dispatch model.

    Returns ``{agent, segment, tier, model, source, resolution_path}``. ``source``
    is one of: ``inherit``, ``role-preferred`` / ``role-tier-fallback`` (passed
    through from ``resolve_role``), ``frontmatter-fallback``, ``tier-default-fallback``,
    ``unresolved``.
    """
    adir = agents_dir or default_agents_dir()
    fm = read_agent_frontmatter(agent, adir)
    segment = fm.get("segment")
    tier = fm.get("tier")
    fm_model = fm.get("model")

    # An inherit agent flows the caller's model through — never overridden.
    if segment == INHERIT or tier == INHERIT or fm_model == INHERIT:
        return {
            "agent": agent,
            "segment": segment,
            "tier": tier,
            "model": INHERIT,
            "source": "inherit",
            "resolution_path": [{"model": INHERIT, "selected": True, "via": "inherit"}],
        }

    resolution_path: list[dict[str, Any]] = []

    # Primary path: resolve the (segment, tier) ROLE via the existing resolver.
    # REUSE — this is the single resolution path (availability + host filter +
    # preferred-list + recency + floor invariant all live there).
    if segment and tier:
        try:
            env = model_resolver.resolve_role(
                segment=segment,
                tier=tier,
                workdir=workdir,
                extra_unavailable=extra_unavailable,
                host_providers=host_providers,
            )
        except ValueError as exc:
            # Unknown segment/tier token — fall through to the frontmatter/tier chain.
            resolution_path.append({"role": f"{segment}/{tier}", "skipped": f"invalid: {exc}"})
            env = {}
        else:
            resolution_path.extend(env.get("resolution_path", []))
            if env.get("model"):
                return {
                    "agent": agent,
                    "segment": segment,
                    "tier": tier,
                    "model": env["model"],
                    "source": env.get("source", "role-preferred"),
                    "resolution_path": resolution_path,
                }
    else:
        resolution_path.append({"role": f"{segment}/{tier}", "skipped": "missing segment or tier"})

    # Fallback 1: the agent's own `model:` frontmatter (the recommended default).
    if fm_model:
        resolution_path.append({"model": fm_model, "selected": True, "via": "frontmatter-fallback"})
        return {
            "agent": agent,
            "segment": segment,
            "tier": tier,
            "model": fm_model,
            "source": "frontmatter-fallback",
            "resolution_path": resolution_path,
        }

    # Fallback 2: the tier default (legacy token), if the tier is a known legacy token.
    if tier and tier in model_overrides.TIER_DEFAULTS:
        default = model_overrides.TIER_DEFAULTS[tier]
        if default:
            resolution_path.append({"model": default, "selected": True, "via": "tier-default-fallback"})
            return {
                "agent": agent,
                "segment": segment,
                "tier": tier,
                "model": default,
                "source": "tier-default-fallback",
                "resolution_path": resolution_path,
            }

    # Nothing resolvable.
    return {
        "agent": agent,
        "segment": segment,
        "tier": tier,
        "model": None,
        "source": "unresolved",
        "resolution_path": resolution_path,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("agent", help="Agent name (the file is agents/<agent>.md).")
    p.add_argument("--workdir", default=".")
    p.add_argument("--agents-dir", default=None, help="Override the agents/ directory (tests).")
    p.add_argument(
        "--host-providers",
        default=None,
        help="Comma-separated providers the host can dispatch (e.g. 'anthropic'). "
        "Default: detect the current host. 'any' disables the filter.",
    )
    p.add_argument("--plain", action="store_true", help="Print only the model id.")
    p.add_argument("--json", action="store_true", help="Print the full envelope.")
    p.add_argument("--require", action="store_true", help="Exit 1 if unresolved.")
    args = p.parse_args(argv)

    host_providers = model_resolver._parse_host_providers_arg(args.host_providers)
    try:
        result = resolve(
            agent=args.agent,
            workdir=Path(args.workdir),
            agents_dir=Path(args.agents_dir) if args.agents_dir else None,
            host_providers=host_providers,
        )
    except FileNotFoundError as exc:
        print(json.dumps({"agent": args.agent, "model": None, "source": "error", "error": str(exc)}), file=sys.stderr)
        return 1

    if args.require and not result.get("model"):
        print(json.dumps(result, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    if args.plain and not args.json:
        print(result.get("model") or "")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
