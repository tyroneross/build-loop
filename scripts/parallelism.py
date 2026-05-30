#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
parallelism.py — machine-aware, configurable subagent-parallelism ceiling for build-loop.

Replaces the hardcoded "up to 4" parallel implementer limit with a value derived
from CPU headroom and an optional project-level config.

Config schema (.build-loop/config.json):
    {
      "parallelism": {
        "maxImplementers": 8
      }
    }

CLI usage:
    python3 scripts/parallelism.py --workdir . [--requested N] [--describe] --json

    --describe  prints full diagnostic dict instead of bare integer
    --json      emit JSON (always set alongside --describe; omit for plain int)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HARD_CEILING: int = 12  # never exceed — coordination / token overhead
DEFAULT_MAX: int = 8    # new default, up from the prior hardcoded 4


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _cpu_budget() -> int:
    """Leave 2 cores for the main loop + OS; floor at 1."""
    return max(1, (os.cpu_count() or 4) - 2)


def _config_max(workdir: Path) -> int:
    """Read .build-loop/config.json parallelism.maxImplementers; fail-soft → DEFAULT_MAX."""
    try:
        cfg_path = workdir / ".build-loop" / "config.json"
        data = json.loads(cfg_path.read_text())
        val = data["parallelism"]["maxImplementers"]
        if not isinstance(val, int) or val < 1:
            raise ValueError(f"invalid maxImplementers: {val!r}")
        return val
    except Exception:  # missing file, key error, json error, type error
        return DEFAULT_MAX


def effective_max_implementers(
    workdir: Path,
    requested: int | None = None,
) -> int:
    """Return the effective parallelism ceiling for *workdir*.

    Resolution order:
        1. ``requested`` (caller-supplied override)
        2. ``parallelism.maxImplementers`` from ``.build-loop/config.json``
        3. ``DEFAULT_MAX`` (8)

    The result is further capped by ``cpu_budget`` and ``HARD_CEILING``,
    then floored at 1.  Any config read error degrades silently to DEFAULT_MAX.
    """
    budget = _cpu_budget()
    cfg = _config_max(workdir)
    candidate = requested if requested is not None else cfg
    return max(1, min(candidate, budget, HARD_CEILING))


def plan_batches(items: list, batch_size: int) -> list[list]:
    """Split *items* into consecutive batches of *batch_size*.

    ``batch_size < 1`` is treated as 1 (each item its own singleton batch).
    """
    if not items:
        return []
    size = max(1, batch_size)
    return [items[i : i + size] for i in range(0, len(items), size)]


def describe(workdir: Path) -> dict:
    """Return a diagnostic snapshot for reporting / --describe CLI."""
    cpu = os.cpu_count() or 4
    budget = _cpu_budget()
    cfg = _config_max(workdir)
    effective = effective_max_implementers(workdir)
    return {
        "cpu_count": cpu,
        "cpu_budget": budget,
        "config_max": cfg,
        "hard_ceiling": HARD_CEILING,
        "effective_max": effective,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Print the effective subagent parallelism ceiling for a build-loop workdir."
    )
    p.add_argument("--workdir", type=Path, default=Path("."), metavar="DIR")
    p.add_argument("--requested", type=int, default=None, metavar="N",
                   help="Caller-requested parallelism (overrides config when provided).")
    p.add_argument("--describe", action="store_true",
                   help="Emit full diagnostic dict instead of bare integer.")
    p.add_argument("--json", action="store_true",
                   help="Force JSON output (implied when --describe is set).")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    workdir = args.workdir.resolve()

    if args.describe:
        print(json.dumps(describe(workdir), indent=2))
    elif args.json:
        result = effective_max_implementers(workdir, requested=args.requested)
        print(json.dumps({"effective_max": result}))
    else:
        result = effective_max_implementers(workdir, requested=args.requested)
        print(result)


if __name__ == "__main__":
    main()
