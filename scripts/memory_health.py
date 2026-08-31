#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Report the memory telemetry loop honestly, separating trustworthy rows from legacy.

WHY THE SEPARATION IS LOAD-BEARING
----------------------------------
Measured 2026-08-31: of 41,128 ``memory-read`` rows, 40,843 are schema 1.0 and
predate the ``source`` field. Their top queries are ``'test'``, ``'x'``,
``'thing'``, ``'buy groceries tomorrow morning'`` and
``'build semantic search across the docs site'`` -- test fixtures written into
the production ledger before ``telemetry_source()`` existed to route them
elsewhere. They cannot be filtered by any field the rows carry.

So any hit-rate, usage, or ranking metric computed over the whole ledger is
measuring the test suite. That is the exact trap this repo's own lesson names:
verify the instrument before the finding. This tool refuses to print a blended
number -- every rate is reported per tier, and the legacy tier is labelled
untrustworthy rather than silently averaged in.

TIERS
-----
- ``clean``    : schema >= 1.1 AND ``source`` == "runtime". Trustworthy.
- ``non_runtime``: schema >= 1.1 with a non-runtime source (test/hook/...). Excluded from rates.
- ``legacy``   : schema 1.0, no ``source`` field. Test-polluted, unfilterable.

Exit codes: 0 always (observability, never a gate).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _paths import memory_store_root  # type: ignore  # noqa: E402

READ, WRITE, EFFECT, USE = "memory-read", "memory-write", "memory-effect", "memory-use"


def tier_of(row: dict) -> str:
    sv = str(row.get("schema_version") or "1.0")
    if sv == "1.0" or "source" not in row:
        return "legacy"
    return "clean" if row.get("source") == "runtime" else "non_runtime"


def iter_rows(store: Path):
    for path in sorted(store.rglob("TELEMETRY.jsonl")):
        try:
            with path.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
        except OSError:
            continue


def collect(store: Path) -> dict:
    tiers: dict[str, dict] = {}
    for row in iter_rows(store):
        t = tiers.setdefault(tier_of(row), {
            "kinds": Counter(), "reads": 0, "reads_with_hits": 0,
            "reads_with_used": 0, "reads_with_effect": 0,
            "readers": Counter(), "first": "", "last": "",
        })
        kind = row.get("kind", "?")
        t["kinds"][kind] += 1
        ts = row.get("ts") or ""
        if ts:
            t["first"] = min(t["first"], ts) if t["first"] else ts
            t["last"] = max(t["last"], ts)
        if kind == READ:
            t["reads"] += 1
            t["readers"][row.get("reader_or_writer", "?")] += 1
            if row.get("memory_ids_seen"):
                t["reads_with_hits"] += 1
            if row.get("memory_ids_used"):
                t["reads_with_used"] += 1
            if row.get("effect"):
                t["reads_with_effect"] += 1
    return tiers


def summarize(tiers: dict) -> dict:
    out = {"tiers": {}, "loop": {}}
    for name, t in sorted(tiers.items()):
        reads = t["reads"]
        out["tiers"][name] = {
            "trustworthy": name == "clean",
            "rows": sum(t["kinds"].values()),
            "kinds": dict(t["kinds"]),
            "reads": reads,
            "hit_rate": round(t["reads_with_hits"] / reads, 4) if reads else None,
            "reads_with_used": t["reads_with_used"],
            "reads_with_effect": t["reads_with_effect"],
            "first": t["first"], "last": t["last"],
            "top_readers": dict(t["readers"].most_common(5)),
        }
    clean = tiers.get("clean", {})
    all_reads = sum(t["reads"] for t in tiers.values())
    all_used = sum(t["reads_with_used"] for t in tiers.values())
    use_rows = sum(t["kinds"].get(USE, 0) for t in tiers.values())
    eff_rows = sum(t["kinds"].get(EFFECT, 0) for t in tiers.values())
    out["loop"] = {
        "reads_all_tiers": all_reads,
        "outcome_labelled": all_used + use_rows,
        "use_rows": use_rows,
        "effect_rows": eff_rows,
        "closure_rate": round((all_used + use_rows) / all_reads, 6) if all_reads else None,
        "closed": bool(all_used or use_rows or eff_rows),
    }
    return out


def render(s: dict) -> str:
    L = ["Memory telemetry health", "=" * 55, ""]
    for name, t in s["tiers"].items():
        mark = "TRUSTWORTHY" if t["trustworthy"] else "NOT trustworthy for rates"
        L.append(f"[{name}]  {mark}")
        L.append(f"  rows            : {t['rows']}  {t['kinds']}")
        L.append(f"  window          : {t['first'][:10] or '-'} -> {t['last'][:10] or '-'}")
        if t["reads"]:
            hr = f"{100*t['hit_rate']:.1f}%" if t["hit_rate"] is not None else "-"
            L.append(f"  reads           : {t['reads']}  (returned results: {hr})")
            L.append(f"  outcome labelled: {t['reads_with_used']} used / {t['reads_with_effect']} effect")
            L.append(f"  top readers     : {t['top_readers']}")
        L.append("")
    lp = s["loop"]
    L += ["Read -> effect loop", "-" * 55,
          f"  reads (all tiers) : {lp['reads_all_tiers']}",
          f"  outcome-labelled  : {lp['outcome_labelled']}",
          f"  memory-use rows   : {lp['use_rows']}",
          f"  memory-effect rows: {lp['effect_rows']}"]
    rate = lp["closure_rate"]
    L.append(f"  closure rate      : {100*rate:.4f}%" if rate is not None else "  closure rate      : n/a")
    if not lp["closed"]:
        L += ["",
              "  OPEN LOOP: the store records what it looked at and never what helped.",
              "  Nothing here can rank memory by usefulness. Close it with:",
              "    python3 scripts/memory_effect.py --range <sha>~1..<sha>"]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=None, help="memory store root")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    store = Path(args.store).expanduser() if args.store else memory_store_root()
    if not store.is_dir():
        print(f"memory store not found: {store}", file=sys.stderr)
        return 0
    s = summarize(collect(store))
    print(json.dumps(s, indent=2) if args.json else render(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
