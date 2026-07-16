#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""cost_rca.py — deterministic measured-token aggregation for cost-impact RCA.

The number-crunching half of the ``cost-rca`` skill (deterministic-first: this script
MEASURES, the skill's LLM body NARRATES and fetches LIVE pricing). It reads the cost
ledger(s), keeps only MEASURED rows (real token counts — never ``tokens_estimate``),
and splits the token volume into the four billable buckets:

    inbound        = input_tokens (uncached prompt)
    outbound       = output_tokens (completion)
    cache_read     = cache_read_input_tokens (cheap cached reads)
    cache_write    = cache_creation_input_tokens (cache writes)

per-model and in total, plus context-window utilization when a window is known.

It DOES NOT price anything — rate cards change and must be fetched LIVE by the skill
(``est_cost_usd`` rows in the ledger are ignored for the same reason). Estimate-only
rows are counted separately and surfaced so the RCA can flag unmeasured spend.

Usage:
    cost_rca.py --ledger ~/.bookmark/cost-ledger.jsonl [--run-id R] [--since ISO]
                [--group-by model|agent] [--json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_LEDGER = Path.home() / ".bookmark" / "cost-ledger.jsonl"

# Measured token fields, mapped to billing bucket. Aliases cover the ledger's
# heterogeneous producers (build-loop rows, cache-telemetry rows, mcp rows).
_BUCKETS: dict[str, tuple[str, ...]] = {
    "inbound": ("input_tokens", "prompt_tokens", "inbound_tokens"),
    "outbound": ("output_tokens", "completion_tokens", "outbound_tokens"),
    "cache_read": ("cache_read_input_tokens", "cache_read_tokens", "cache_read"),
    "cache_write": ("cache_creation_input_tokens", "cache_creation_tokens", "cache_write"),
}

# Known context windows (tokens) for utilization math. Advisory only — the skill
# should confirm current windows live; absence just omits the utilization field.
_CONTEXT_WINDOWS = {
    "claude-opus-4-8": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-fable-5": 200_000,
}


def _first_int(row: dict, keys: tuple[str, ...]) -> int | None:
    for k in keys:
        v = row.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return int(v)
    return None


def _is_measured(row: dict) -> bool:
    """A row is MEASURED if it carries at least one real token bucket value.

    ``tokens_estimate`` alone does NOT qualify (that is a heuristic, not a meter)."""
    if str(row.get("tokens_source", "")).lower() == "estimate":
        return False
    for keys in _BUCKETS.values():
        if _first_int(row, keys) is not None:
            return True
    return False


def _context_window(model: str | None) -> int | None:
    if not model:
        return None
    m = model.split("[", 1)[0]  # strip harness suffix like [1m]
    return _CONTEXT_WINDOWS.get(m) or _CONTEXT_WINDOWS.get(model)


def _empty_buckets() -> dict[str, int]:
    return {b: 0 for b in _BUCKETS}


def aggregate(rows: list[dict], group_by: str = "model") -> dict[str, Any]:
    total = _empty_buckets()
    groups: dict[str, dict[str, int]] = {}
    measured_rows = 0
    estimate_only_rows = 0
    estimate_tokens = 0
    peak_context: dict[str, int] = {}

    for row in rows:
        if not _is_measured(row):
            if str(row.get("tokens_source", "")).lower() == "estimate" or row.get("tokens_estimate"):
                estimate_only_rows += 1
                te = _first_int(row, ("tokens_estimate",))
                if te:
                    estimate_tokens += te
            continue
        measured_rows += 1
        key = str(row.get(group_by) or "unknown")
        g = groups.setdefault(key, _empty_buckets())
        row_input_side = 0
        for bucket, keys in _BUCKETS.items():
            v = _first_int(row, keys) or 0
            g[bucket] += v
            total[bucket] += v
            if bucket in ("inbound", "cache_read", "cache_write"):
                row_input_side += v
        # Track peak single-call input-side load for context utilization.
        model = str(row.get("model") or "")
        if model and row_input_side:
            peak_context[model] = max(peak_context.get(model, 0), row_input_side)

    utilization = {}
    for model, peak in peak_context.items():
        win = _context_window(model)
        if win:
            utilization[model] = {
                "peak_input_side_tokens": peak,
                "context_window": win,
                "utilization_pct": round(100 * peak / win, 1),
            }

    return {
        "measured_rows": measured_rows,
        "estimate_only_rows": estimate_only_rows,
        "estimate_only_tokens": estimate_tokens,
        "total": total,
        "total_tokens": sum(total.values()),
        "by_" + group_by: groups,
        "context_utilization": utilization,
        "note": "MEASURED tokens only. est_cost_usd ignored — price LIVE per bucket. "
                "Estimate-only rows surfaced separately (unmeasured spend).",
    }


def load_rows(ledger: Path, run_id: str | None = None, since: str | None = None) -> list[dict]:
    rows: list[dict] = []
    if not ledger.exists():
        return rows
    for line in ledger.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if run_id and str(row.get("run_id") or "") != run_id:
            continue
        if since and str(row.get("ts") or "") < since:
            continue
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cost_rca", description=__doc__)
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--since", default=None, help="ISO timestamp lower bound (inclusive)")
    ap.add_argument("--group-by", default="model", choices=["model", "agent"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rows = load_rows(Path(args.ledger).expanduser(), run_id=args.run_id, since=args.since)
    result = aggregate(rows, group_by=args.group_by)
    result["ledger"] = str(Path(args.ledger).expanduser())

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        t = result["total"]
        print(f"measured rows: {result['measured_rows']}  (estimate-only ignored: {result['estimate_only_rows']})")
        print(f"  inbound={t['inbound']}  outbound={t['outbound']}  "
              f"cache_read={t['cache_read']}  cache_write={t['cache_write']}  "
              f"total={result['total_tokens']}")
        for model, u in result["context_utilization"].items():
            print(f"  ctx {model}: {u['utilization_pct']}% of {u['context_window']}")
        print("  -> price each bucket LIVE (rate cards change); do NOT use ledger est_cost_usd.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
