#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""exec_state.py — producer CLI for execution-state item_iteration telemetry.

The orchestrator records one row per autonomous work-item attempt so dry-run /
task-review surfaces (`task_surface.py`) can show how many tries each item took,
why it stopped, who validated it, and — the point of this CLI — **which tier and
model actually ran it**.

`write_run_entry.update_execution_state(action="item_iteration", ...)` is the
Python API and `task_surface.py` is the reader; this is the missing CLI producer
that the orchestrator (an LLM agent driving Bash) can call in one line. It pairs
with `agent_ledger.py` (per-agent-action) on the same accuracy goal: never lose
which model fired on a tiered surface.

Tier→model resolution mirrors the rest of build-loop: pass `--tier` and the model
is resolved via `model_overrides.resolve_model` (repo config → state → fallback →
tier default, where `frontier` defaults to `fable`), so the recorded row carries
both `tier` and the resolved `model`. Pass `--model` to record an explicit id
instead (skips resolution).

Example — record that the autonomous loop passed item `q-7` on the Frontier tier::

    python3 scripts/exec_state.py item-iteration \
        --workdir . --item-id q-7 --status passed \
        --validator independent-auditor --tier frontier

emits the row with `tier=frontier, model=<resolved, e.g. fable>`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                    # scripts/ on path
sys.path.insert(0, str(HERE / "rally_point"))    # match sibling-script import style

from model_overrides import resolve_model  # type: ignore  # noqa: E402
from write_run_entry import update_execution_state  # type: ignore  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    it = sub.add_parser("item-iteration", help="Append one item-iteration telemetry row.")
    it.add_argument("--workdir", default=".", help="Project root containing .build-loop/")
    it.add_argument("--item-id", required=True, help="Stable task / queue item id.")
    it.add_argument("--status", default=None, help="Item status (default: started).")
    it.add_argument("--phase", default=None, help="Phase override (default: current execution phase).")
    it.add_argument("--criterion", default=None, help="Failed/passed criterion label.")
    it.add_argument("--stop-reason", default=None, help="Stop/defer/block reason.")
    it.add_argument("--validator", default=None, help="Validator or judge id.")
    # Tier vs explicit model: --tier resolves via model_overrides; --model is verbatim.
    it.add_argument("--tier", default=None, help="Tier to record + resolve a model from (frontier|thinking|code|pattern).")
    it.add_argument("--model", default=None, help="Explicit model id (skips tier resolution).")
    it.add_argument("--fallback", default=None, help="Fallback model id when --tier is unresolved by config/state.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    workdir = Path(args.workdir).expanduser().resolve()

    if args.cmd == "item-iteration":
        tier = args.tier
        model = args.model
        # Resolve a model from the tier when an explicit model was not supplied.
        if tier and not model:
            resolved = resolve_model(tier=tier, workdir=workdir, fallback=args.fallback)
            model = resolved.get("model")

        block = update_execution_state(
            workdir / ".build-loop" / "state.json",
            "item_iteration",
            item_id=args.item_id,
            status=args.status,
            phase=args.phase,
            criterion=args.criterion,
            stop_reason=args.stop_reason,
            validator=args.validator,
            tier=tier,
            model=model,
        )
        # Echo the row we just appended (last attempt for this item) for caller visibility.
        attempts = block.get("item_iterations", {}).get(args.item_id, [])
        print(json.dumps(attempts[-1] if attempts else {}, indent=2, sort_keys=True))
        return 0

    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    raise SystemExit(main())
