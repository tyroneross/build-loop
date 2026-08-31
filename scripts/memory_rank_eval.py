#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Grade recall ordering: current recency-sort vs relevance rank.

INDEPENDENCE OF THE ORACLE (this is the whole point)
----------------------------------------------------
Grading a ranker with its own scoring function proves nothing -- it measures
agreement with itself. So the oracle here reads the candidate's **full document
text from disk** and computes distinct query-term coverage over that text.

`memory_rank` never sees document bodies. It scores the metadata fields carried
on the result row (id, title, tags, path, status). The oracle scores the file
contents. Different inputs, so a win is not tautological.

The oracle is still lexical, so it shares lexical blind spots with the ranker
(neither understands synonyms). It measures "did the ranker put documents that
actually contain the asked-about terms near the top", which is precisely the
property recency-sort was violating -- not semantic relevance in general. That
limit is reported with the result rather than hidden.

Queries come from real runtime telemetry (schema 1.1, source=runtime), not
hand-picked, so the query distribution is the one the system actually sees.

Metrics: precision@1, precision@3, and MRR against "candidate covers >= half the
query terms in its body".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import memory_rank as mr  # noqa: E402
from _paths import memory_store_root  # noqa: E402

_WORD = re.compile(r"[a-z0-9]+")


def oracle_coverage(row: Dict[str, Any], terms: List[str], store: Path) -> float:
    """Fraction of query terms present in the candidate's FILE TEXT on disk."""
    if not terms:
        return 0.0
    path = row.get("path") or row.get("file") or ""
    p = Path(path)
    if not p.is_absolute():
        p = store / path
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0.0
    words = set(_WORD.findall(text.lower()))
    hit = sum(1 for t in terms
              if t in words or any(w.startswith(t) for w in words))
    return hit / len(terms)


def relevant(row, terms, store, threshold: float) -> bool:
    return oracle_coverage(row, terms, store) >= threshold


def load_queries(store: Path, limit: int) -> List[str]:
    seen, out = set(), []
    for path in sorted(store.rglob("TELEMETRY.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("kind") != "memory-read" or d.get("schema_version") != "1.1":
                continue
            if d.get("source") != "runtime":
                continue
            q = (d.get("query") or "").strip()
            if len(q) < 8 or q in seen:
                continue
            seen.add(q)
            out.append(q)
    return out[:limit]


def evaluate(queries, store: Path, threshold: float) -> Dict[str, Any]:
    # Telemetry MUST NOT land in the production ledger during evaluation.
    os.environ["BUILD_LOOP_TELEMETRY_SOURCE"] = "test"
    os.environ.setdefault(
        "BUILD_LOOP_TEST_TELEMETRY_PATH",
        str(Path(os.environ.get("TMPDIR", "/tmp")) / "memory-rank-eval-telemetry.jsonl"),
    )
    import memory_facade as mf  # noqa: PLC0415

    stats = {m: {"p1": 0, "p3": 0, "mrr": 0.0} for m in ("recency", "relevance")}
    evaluated = 0
    per_query = []
    for q in queries:
        res = mf.recall(query=q, workdir=store, skip_postgres=True)
        merged = res.get("merged") or []
        if not merged:
            continue
        terms = mr.query_terms(q)
        if not terms:
            continue
        if not any(relevant(r, terms, store, threshold) for r in merged):
            continue  # no gradable answer in the pool; skip rather than score noise
        evaluated += 1

        orders = {
            "recency": sorted(merged, key=lambda r: r.get("_recency_ts") or 0, reverse=True),
            "relevance": mr.rank(merged, q),
        }
        row = {"query": q[:60], "n": len(merged)}
        for name, ordered in orders.items():
            rel = [relevant(r, terms, store, threshold) for r in ordered]
            stats[name]["p1"] += int(rel[0])
            stats[name]["p3"] += int(any(rel[:3]))
            rr = next((1.0 / (i + 1) for i, ok in enumerate(rel) if ok), 0.0)
            stats[name]["mrr"] += rr
            row[name] = {"p1": int(rel[0]), "rr": round(rr, 3)}
        per_query.append(row)

    out = {"queries_evaluated": evaluated, "threshold": threshold, "metrics": {}}
    for name, s in stats.items():
        n = max(evaluated, 1)
        out["metrics"][name] = {
            "precision_at_1": round(s["p1"] / n, 4),
            "precision_at_3": round(s["p3"] / n, 4),
            "mrr": round(s["mrr"] / n, 4),
        }
    out["per_query"] = per_query
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=None)
    ap.add_argument("--limit", type=int, default=40, help="max distinct queries")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="oracle: fraction of query terms the body must contain")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    store = Path(args.store).expanduser() if args.store else memory_store_root()
    queries = load_queries(store, args.limit)
    if not queries:
        print("no runtime queries found in telemetry", file=sys.stderr)
        return 0
    res = evaluate(queries, store, args.threshold)

    if args.json:
        print(json.dumps(res, indent=2))
        return 0

    m = res["metrics"]
    print(f"Ranking evaluation  (oracle: document body text, independent of ranker)")
    print(f"queries evaluated: {res['queries_evaluated']}   "
          f"relevance threshold: {res['threshold']}")
    print()
    print(f"{'ordering':12s} {'P@1':>8s} {'P@3':>8s} {'MRR':>8s}")
    print("-" * 40)
    for name in ("recency", "relevance"):
        s = m[name]
        print(f"{name:12s} {s['precision_at_1']:8.3f} {s['precision_at_3']:8.3f} {s['mrr']:8.3f}")
    d1 = m["relevance"]["precision_at_1"] - m["recency"]["precision_at_1"]
    dm = m["relevance"]["mrr"] - m["recency"]["mrr"]
    print()
    print(f"delta P@1 {d1:+.3f}   delta MRR {dm:+.3f}")
    print()
    print("Oracle is lexical: it shares synonym blindness with the ranker and")
    print("measures term presence in the body, not semantic relevance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
