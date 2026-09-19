#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Measure whether build-loop-memory is usable by a cold agent.

Replays real recall queries through ``memory_locator.locate`` (the path the Phase 1
bootstrap uses) and reports four signals:

- engine: how often the fast index answered instead of the slow directory scan;
- latency: median and p90 milliseconds per query;
- unvetted share: fraction of returned paths that are unreviewed auto-captures
  (``/_review/``), which carry no human or triage vetting;
- known answers: hit@k and MRR for cases whose correct file is known in advance.

Queries come from ``indexes/TELEMETRY.jsonl`` (real runtime reads), so the query
mix is what agents actually send. Known-answer cases live in a JSONL file so the
set can grow without code changes. Read-only: telemetry emission is disabled.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import memory_locator  # type: ignore  # noqa: E402
from _paths import memory_store_root  # type: ignore  # noqa: E402

UNVETTED_MARKER = "/_review/"


def real_queries(telemetry: Path, sample: int) -> list[str]:
    """Most recent distinct real queries, newest first."""
    seen: list[str] = []
    if not telemetry.is_file():
        return seen
    lines = telemetry.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        query = str(row.get("query") or "").strip()
        if row.get("kind") != "memory-read" or not query or query in seen:
            continue
        seen.append(query)
        if len(seen) >= sample:
            break
    return seen


def known_cases(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            if case.get("query") and case.get("expect"):
                cases.append(case)
    return cases


def run(root: Path, queries: list[str], cases: list[dict[str, Any]], k: int, project: str) -> dict[str, Any]:
    engines: dict[str, int] = {}
    latencies: list[float] = []
    returned = unvetted = 0
    for query in queries:
        started = time.perf_counter()
        receipt = memory_locator.locate(query, project=project, limit=k, memory_root=root, emit_telemetry=False)
        latencies.append((time.perf_counter() - started) * 1000)
        engines[receipt.get("engine", "?")] = engines.get(receipt.get("engine", "?"), 0) + 1
        for result in receipt.get("results") or []:
            returned += 1
            unvetted += UNVETTED_MARKER in "/" + str(result.get("path") or "")
    hits = 0
    reciprocal = 0.0
    misses: list[str] = []
    for case in cases:
        receipt = memory_locator.locate(case["query"], project=case.get("project", project), limit=k,
                                        memory_root=root, emit_telemetry=False)
        paths = [str(r.get("path") or "") for r in receipt.get("results") or []]
        rank = next((i for i, p in enumerate(paths, 1) if any(e in p for e in case["expect"])), None)
        if rank:
            hits += 1
            reciprocal += 1 / rank
        else:
            misses.append(case["query"])
    ordered = sorted(latencies)
    return {
        "queries": len(queries),
        "engines": engines,
        "latency_ms": {
            "median": round(statistics.median(ordered), 1) if ordered else None,
            "p90": round(ordered[int(0.9 * (len(ordered) - 1))], 1) if ordered else None,
        },
        "results_returned": returned,
        "unvetted_share": round(unvetted / returned, 3) if returned else 0.0,
        "known_cases": len(cases),
        "hit_at_k": round(hits / len(cases), 3) if cases else None,
        "mrr": round(reciprocal / len(cases), 3) if cases else None,
        "known_misses": misses,
        "k": k,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--memory-root", type=Path, default=None)
    parser.add_argument("--sample", type=int, default=40, help="distinct real queries to replay")
    parser.add_argument("--cases", type=Path, default=None, help="known-answer JSONL: {query, expect:[path fragment], project?}")
    parser.add_argument("--project", default=memory_locator.ALL_PROJECTS)
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args(argv)
    root = (args.memory_root or memory_store_root()).expanduser().resolve()
    cases_path = args.cases or root / "golden" / "memory-usability-cases.jsonl"
    report = run(root, real_queries(root / "indexes" / "TELEMETRY.jsonl", args.sample), known_cases(cases_path), args.k, args.project)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
