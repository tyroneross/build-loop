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
- ``clean``    : schema >= 1.1 AND ``source`` == "runtime". Rate-eligible by
                 provenance; the source marker alone cannot prove a row was
                 organic runtime activity.
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
import memory_telemetry as memory_telemetry  # type: ignore  # noqa: E402

READ, WRITE, EFFECT, USE = "memory-read", "memory-write", "memory-effect", "memory-use"


def valid_effect(value: object) -> bool:
    return isinstance(value, str) and value in memory_telemetry.VALID_EFFECTS


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
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(row, dict):
                            yield row
        except OSError:
            continue


def _add_row(tiers: dict, row: dict) -> None:
    """Add one telemetry row to one aggregate."""
    tier = tier_of(row)
    t = tiers.setdefault(tier, {
        "kinds": Counter(), "reads": 0, "reads_with_hits": 0,
        "reads_with_used": 0, "reads_with_effect": 0,
        "reads_with_paths": 0, "reads_with_session": 0, "reads_with_ranks": 0,
        "reads_with_phase": 0,
        "readers": Counter(), "first": "", "last": "",
        # Only clean reads participate in loop metrics. Follow-ups aggregate
        # by correlation because append order does not guarantee that a read
        # precedes its effect/use row.
        "read_correlations": {}, "uncorrelated_reads": 0, "followups": {},
        "uncorrelated_followups": Counter(),
        "invalid_effect_labels": 0,
    })
    kind = row.get("kind", "?")
    t["kinds"][kind] += 1
    ts = row.get("ts")
    if isinstance(ts, str) and ts:
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
        if row.get("returned_paths"):
            t["reads_with_paths"] += 1
        if row.get("session_id"):
            t["reads_with_session"] += 1
        if row.get("ranks"):
            t["reads_with_ranks"] += 1
        # "unknown" is the absent-phase sentinel, not a phase. Counting it
        # would report full attribution for rows that attribute nothing.
        if row.get("phase") and row.get("phase") != "unknown":
            t["reads_with_phase"] += 1
        if tier == "clean":
            correlation_id = row.get("correlation_id")
            if correlation_id:
                attribution = t["read_correlations"].setdefault(
                    str(correlation_id), {"used": False, "effect": False}
                )
                attribution["used"] |= bool(row.get("memory_ids_used"))
                effect = row.get("effect")
                attribution["effect"] |= valid_effect(effect)
                if effect is not None and not valid_effect(effect):
                    t["invalid_effect_labels"] += 1
            else:
                t["uncorrelated_reads"] += 1
    elif tier == "clean" and kind in {USE, EFFECT}:
        correlation_id = row.get("correlation_id")
        if not correlation_id:
            t["uncorrelated_followups"][kind] += 1
            return
        followup = t["followups"].setdefault(str(correlation_id), {
            "kinds": Counter(), "used": False, "effect": False,
            "invalid_effect_labels": 0,
        })
        followup["kinds"][kind] += 1
        if kind == USE and (row.get("memory_ids_used") or row.get("files_read")):
            followup["used"] = True
        effect = row.get("effect")
        if valid_effect(effect):
            followup["effect"] = True
        elif effect is not None:
            followup["invalid_effect_labels"] += 1


def collect_windows(store: Path, since_values: list[str | None]) -> dict[str | None, dict]:
    """Aggregate every requested UTC window from one ledger stream.

    Target reporting needs lifetime plus a few rebaseline windows.  Reading the
    append-only ledger once keeps those figures a coherent snapshot while
    avoiding a raw-row materialization.
    """
    windows = {since: {} for since in dict.fromkeys(since_values)}
    for row in iter_rows(store):
        ts = row.get("ts")
        for since, tiers in windows.items():
            if since is not None and (not isinstance(ts, str) or ts < since):
                continue
            _add_row(tiers, row)
    return windows


def collect(store: Path, since: str | None = None) -> dict:
    """Aggregate telemetry rows into per-tier counters.

    ``since`` is an ISO-8601 UTC lower bound on ``ts``. It exists because the
    ledger is append-only: old rows can dilute a post-fix rate long after a fix
    landed. Rows without a ``ts``
    are dropped when a window is set -- an undated row cannot be proven to fall
    inside it, and counting it would silently readmit the history the window
    exists to exclude.
    """
    return collect_windows(store, [since])[since]


def stats_summary(tiers: dict) -> dict:
    """Project the health collection into store-stat metrics without rescanning."""
    kinds = Counter()
    out = {"kinds": {}, "tiers": {}}
    for name, t in sorted(tiers.items()):
        kinds.update(t["kinds"])
        reads = t["reads"]
        out["tiers"][name] = {
            "rate_eligible": name == "clean",
            "reads": reads,
            "hit_rate": round(t["reads_with_hits"] / reads, 4) if reads else None,
            "zero_result_rate": round(1 - t["reads_with_hits"] / reads, 4) if reads else None,
            "joinable_rate": round(t["reads_with_paths"] / reads, 4) if reads else None,
            "session_rate": round(t["reads_with_session"] / reads, 4) if reads else None,
            "exposure_rate": round(t["reads_with_ranks"] / reads, 4) if reads else None,
            "phase_rate": round(t["reads_with_phase"] / reads, 4) if reads else None,
            "top_readers": dict(t["readers"].most_common(4)),
        }
    loop = summarize(tiers)["loop"]
    out["kinds"] = dict(kinds)
    out["loop"] = loop
    out["loop_closed"] = loop["closed"]
    out["use_rows"] = loop["recorded_use_reads"]
    out["use_rows_all_tiers"] = kinds.get(USE, 0)
    return out


def summarize(tiers: dict) -> dict:
    out = {"tiers": {}, "loop": {}}
    for name, t in sorted(tiers.items()):
        reads = t["reads"]
        out["tiers"][name] = {
            "rate_eligible": name == "clean",
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
    correlations = clean.get("read_correlations", {})
    unmatched_followups = Counter()
    invalid_effect_labels = clean.get("invalid_effect_labels", 0)
    for correlation_id, followup in clean.get("followups", {}).items():
        attribution = correlations.get(correlation_id)
        if attribution is None:
            unmatched_followups.update(followup["kinds"])
            continue
        attribution["used"] |= followup["used"]
        attribution["effect"] |= followup["effect"]
        invalid_effect_labels += followup["invalid_effect_labels"]
    unmatched_followups.update(clean.get("uncorrelated_followups", {}))

    # A repeated follow-up must not make one retrieval look like several
    # outcomes.  The denominator is likewise one per correlation, preserving
    # uncorrelated clean read rows as an explicit measurement limitation.
    reads_clean = len(correlations) + clean.get("uncorrelated_reads", 0)
    reads_with_use = sum(a["used"] for a in correlations.values())
    reads_with_effect = sum(a["effect"] for a in correlations.values())
    use_rows_all_tiers = sum(t["kinds"].get(USE, 0) for t in tiers.values())
    effect_rows_all_tiers = sum(t["kinds"].get(EFFECT, 0) for t in tiers.values())
    out["loop"] = {
        "reads_clean": reads_clean,
        "clean_read_rows": clean.get("reads", 0),
        "recorded_use_reads": reads_with_use,
        "effect_labelled_reads": reads_with_effect,
        "recorded_use_rate": round(reads_with_use / reads_clean, 6) if reads_clean else None,
        "closure_rate": round(reads_with_effect / reads_clean, 6) if reads_clean else None,
        "use_rows_all_tiers": use_rows_all_tiers,
        "effect_rows_all_tiers": effect_rows_all_tiers,
        "unmatched_clean_followups": dict(unmatched_followups),
        "invalid_clean_effect_labels": invalid_effect_labels,
        "uncorrelated_clean_reads": clean.get("uncorrelated_reads", 0),
        "closed": bool(reads_with_effect),
    }
    return out


def render(s: dict) -> str:
    L = ["Memory telemetry health", "=" * 55, ""]
    for name, t in s["tiers"].items():
        mark = "RATE-ELIGIBLE by source=runtime" if t["rate_eligible"] else "EXCLUDED from rates"
        L.append(f"[{name}]  {mark}")
        L.append(f"  rows            : {t['rows']}  {t['kinds']}")
        L.append(f"  window          : {t['first'][:10] or '-'} -> {t['last'][:10] or '-'}")
        if t["reads"]:
            hr = f"{100*t['hit_rate']:.1f}%" if t["hit_rate"] is not None else "-"
            L.append(f"  reads           : {t['reads']}  (returned results: {hr})")
            L.append(f"  inline labels   : {t['reads_with_used']} used / {t['reads_with_effect']} effect")
            L.append(f"  top readers     : {t['top_readers']}")
        L.append("")
    lp = s["loop"]
    L += ["Clean read -> attributed outcome loop", "-" * 55,
          f"  clean reads       : {lp['reads_clean']} ({lp['clean_read_rows']} rows)",
          f"  recorded use      : {lp['recorded_use_reads']}",
          f"  effect-labelled   : {lp['effect_labelled_reads']}",
          f"  memory-use rows   : {lp['use_rows_all_tiers']} (all tiers)",
          f"  memory-effect rows: {lp['effect_rows_all_tiers']} (all tiers)"]
    rate = lp["closure_rate"]
    L.append(f"  effect-label rate : {100*rate:.4f}%" if rate is not None else "  effect-label rate : n/a")
    if lp["unmatched_clean_followups"] or lp["uncorrelated_clean_reads"]:
        L.append(f"  unjoinable rows   : {lp['uncorrelated_clean_reads']} read / {lp['unmatched_clean_followups']} follow-up")
    if lp["invalid_clean_effect_labels"]:
        L.append(f"  invalid effect labels: {lp['invalid_clean_effect_labels']} (excluded)")
    L.append("  A memory-use row records inspection; effect labels are consumer-reported, never proof of helpfulness.")
    L.append("  Source=runtime qualifies a row for this rate; it does not independently exclude fixtures.")
    if not lp["closed"]:
        L += ["",
              "  OPEN LOOP: no valid effect label joins a rate-eligible retrieval.",
              "  Inspect attributable effects; this does not establish usefulness:",
              "    python3 scripts/memory_effect.py --range <sha>~1..<sha>"]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=None, help="memory store root")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--since", default=None,
                    help="ISO-8601 UTC lower bound on row ts, e.g. 2026-09-01T07:00:00Z")
    args = ap.parse_args(argv)

    store = Path(args.store).expanduser() if args.store else memory_store_root()
    if not store.is_dir():
        print(f"memory store not found: {store}", file=sys.stderr)
        return 0
    s = summarize(collect(store, since=args.since))
    print(json.dumps(s, indent=2) if args.json else render(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
