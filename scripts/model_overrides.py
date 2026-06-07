#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Resolve build-loop model tier overrides.

The orchestrator reasons in tiers (`thinking`, `code`, `pattern`) and resolves
those tiers to concrete model ids at dispatch time. Repo config is the preferred
source; state.json is accepted for older runs that snapshot config there.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

TIERS = {"thinking", "code", "pattern"}


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
            }

    return {
        "tier": tier,
        "model": fallback,
        "source": "fallback" if fallback else "unresolved",
        "path": None,
        "configured": False,
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
    p.add_argument("--tier", required=True, choices=sorted(TIERS))
    p.add_argument("--fallback", default=None)
    p.add_argument("--config", default=None, help="Override config.json path.")
    p.add_argument("--state", default=None, help="Override state.json path.")
    p.add_argument("--require", action="store_true", help="Exit 1 if unresolved.")
    p.add_argument("--plain", action="store_true", help="Print only the model id.")
    p.add_argument("--json", action="store_true", help="Print a JSON envelope.")
    args = p.parse_args(argv)

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
