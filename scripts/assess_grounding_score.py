#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Deterministic multi-objective scorer for the Assess-grounding replay harness.
#   application: validation
#   status: experimental
"""Score a candidate Phase-1 Assess output against a real run's recorded outcome.

The harness replays a prior real challenge (goal + repo SHA) under a grounding
variant, re-runs Assess, and captures the resulting assessment object. THIS
script grades that candidate against the OBJECTIVE recorded outcome of the real
run (state.json runs[]): the triggers / synthesisDensity / filesTouched that the
run actually proved out. It never grades against assistant prose — circular
self-grading is the failure mode this harness exists to avoid.

Objective vector (per candidate; None = not gradable from this run's record):
  trigger_recall          caught the triggers that actually mattered      [max]
  trigger_precision       did not over-flag triggers                      [max]
  synthesis_calibration   predicted synthesis density ~= actual           [max]
  file_recall             predicted-files vs files actually touched        [max]
  file_precision          predicted-files not over-broad                   [max]
  groundedness            judge-supplied fraction of triggers evidence-cited [max]
  cost_tokens             assess token cost                                [min]
  latency_ms              assess wall-clock                                [min]

`groundedness` is supplied externally (eval-guide.md: binary LLM-judge per
trigger, aggregated to a fraction, run on the Fable tier). This script is the
code-based grading tier only: deterministic, no LLM calls.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

# Canonical Phase-1 trigger keys (recon: phase-1-assess.md / state.json assess block).
TRIGGER_KEYS: tuple[str, ...] = (
    "riskSurfaceChange",
    "structuredWriting",
    "promptAuthoring",
    "promptEditingExisting",
    "runtimeServer",
)
# Escalation threshold for synthesisDensity is > 5 dimensions; use 6 as the
# calibration scale so a full-band miss maps to ~0.
SYNTHESIS_SCALE = 6

# Which objectives are minimized (everything else is maximized).
MINIMIZE = frozenset({"cost_tokens", "latency_ms"})
OBJECTIVES: tuple[str, ...] = (
    "trigger_recall",
    "trigger_precision",
    "synthesis_calibration",
    "file_recall",
    "file_precision",
    "groundedness",
    "cost_tokens",
    "latency_ms",
)


def _truthy(triggers: dict[str, Any] | None) -> set[str]:
    """Trigger keys set True. Missing key == False (sparse dicts are normal)."""
    triggers = triggers or {}
    return {k for k in TRIGGER_KEYS if bool(triggers.get(k))}


def score_triggers(pred: dict | None, gt: dict | None) -> tuple[float, float]:
    """(recall, precision) of predicted-true triggers vs actually-true triggers.

    recall    = |pred_true & gt_true| / |gt_true|   (1.0 when gt_true empty)
    precision = |pred_true & gt_true| / |pred_true|  (1.0 when pred_true empty)
    """
    p, g = _truthy(pred), _truthy(gt)
    recall = 1.0 if not g else len(p & g) / len(g)
    precision = 1.0 if not p else len(p & g) / len(p)
    return recall, precision


def score_synthesis(
    pred_count: int | None,
    pred_escalated: bool | None,
    gt_count: int | None,
    gt_escalated: bool | None,
) -> float | None:
    """Combined synthesis-density calibration: avg(count_cal, escalation_match).

    Returns None when the ground-truth run did not record synthesisDensity
    (empty {} -> count None) — not gradable, must not be counted as 0.
    """
    if gt_count is None and gt_escalated is None:
        return None
    parts: list[float] = []
    if gt_count is not None:
        pc = pred_count if pred_count is not None else 0
        parts.append(max(0.0, 1.0 - abs(pc - gt_count) / SYNTHESIS_SCALE))
    if gt_escalated is not None:
        parts.append(1.0 if bool(pred_escalated) == bool(gt_escalated) else 0.0)
    return round(sum(parts) / len(parts), 4) if parts else None


def score_files(
    pred_files: list[str] | None, gt_files: list[str] | None
) -> tuple[float | None, float | None]:
    """(recall, precision) of predicted files vs files actually touched.

    None when either side is absent — file prediction is optional; a run that
    only recorded a count (no list) cannot grade file-level precision/recall.
    """
    if not pred_files or not gt_files:
        return None, None
    p, g = set(pred_files), set(gt_files)
    recall = len(p & g) / len(g)
    precision = len(p & g) / len(p)
    return round(recall, 4), round(precision, 4)


def score_candidate(candidate: dict, ground_truth: dict) -> dict:
    """Score one candidate assessment against one challenge's recorded outcome."""
    a = candidate.get("assessment", {})
    gt = ground_truth

    recall, precision = score_triggers(a.get("triggers"), gt.get("triggers"))
    synth = score_synthesis(
        a.get("synthesis_count"),
        a.get("synthesis_escalated"),
        gt.get("synthesis_count"),
        gt.get("synthesis_escalated"),
    )
    f_recall, f_precision = score_files(a.get("predicted_files"), gt.get("files_touched"))
    cost = candidate.get("cost", {}) or {}

    return {
        "challenge_id": candidate.get("challenge_id"),
        "variant": candidate.get("variant"),
        "rep": candidate.get("rep", 0),
        "goal_type": ground_truth.get("goal_type"),
        "trigger_recall": round(recall, 4),
        "trigger_precision": round(precision, 4),
        "synthesis_calibration": synth,
        "file_recall": f_recall,
        "file_precision": f_precision,
        "groundedness": candidate.get("groundedness"),
        "cost_tokens": cost.get("tokens"),
        "latency_ms": cost.get("latency_ms"),
    }


def _mean(vals: list[Any]) -> float | None:
    nums = [v for v in vals if isinstance(v, (int, float))]
    return round(statistics.mean(nums), 4) if nums else None


def _stdev(vals: list[Any]) -> float | None:
    nums = [v for v in vals if isinstance(v, (int, float))]
    return round(statistics.pstdev(nums), 4) if len(nums) >= 2 else (0.0 if nums else None)


def aggregate_cell(rows: list[dict]) -> dict:
    """Mean each objective across reps of one (challenge, variant) cell, plus a
    `stability` block of per-objective stdev (lower = more reproducible)."""
    if not rows:
        return {}
    head = rows[0]
    agg: dict[str, Any] = {
        "challenge_id": head.get("challenge_id"),
        "variant": head.get("variant"),
        "goal_type": head.get("goal_type"),
        "reps": len(rows),
    }
    stability: dict[str, Any] = {}
    for obj in OBJECTIVES:
        agg[obj] = _mean([r.get(obj) for r in rows])
        stability[obj] = _stdev([r.get(obj) for r in rows])
    agg["stability"] = stability
    return agg


def _dominates(a: dict, b: dict) -> bool:
    """True if cell `a` Pareto-dominates `b` over the gradable objectives:
    >= on every maximize / <= on every minimize, strictly better on >=1.
    Objectives where either side is None are skipped (incomparable)."""
    strictly_better = False
    for obj in OBJECTIVES:
        av, bv = a.get(obj), b.get(obj)
        if av is None or bv is None:
            continue
        if obj in MINIMIZE:
            if av > bv:
                return False
            if av < bv:
                strictly_better = True
        else:
            if av < bv:
                return False
            if av > bv:
                strictly_better = True
    return strictly_better


def pareto_front(cells: list[dict]) -> list[dict]:
    """Non-dominated cells. Small-N enumeration (the harness is deliberately
    small); no NSGA needed."""
    front = []
    for c in cells:
        if not any(_dominates(o, c) for o in cells if o is not c):
            front.append(c)
    return front


def _load_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", required=True, type=Path, help="JSONL of candidate assessments")
    ap.add_argument("--challenges", required=True, type=Path, help="JSONL of challenges (ground truth)")
    ap.add_argument("--out", type=Path, help="Write scored rows + aggregates as JSON (default stdout)")
    args = ap.parse_args(argv)

    challenges = {c["id"]: c for c in _load_jsonl(args.challenges)}
    candidates = _load_jsonl(args.candidates)

    rows: list[dict] = []
    for cand in candidates:
        ch = challenges.get(cand.get("challenge_id"))
        if ch is None:
            print(f"warn: candidate references unknown challenge_id={cand.get('challenge_id')!r}", file=sys.stderr)
            continue
        rows.append(score_candidate(cand, ch.get("ground_truth", {}) | {"goal_type": ch.get("goal_type")}))

    # Group reps by (challenge, variant) -> cells.
    cells_map: dict[tuple, list[dict]] = {}
    for r in rows:
        cells_map.setdefault((r["challenge_id"], r["variant"]), []).append(r)
    cells = [aggregate_cell(v) for v in cells_map.values()]

    # Per-variant rollup (mean of cells) and Pareto front over variant rollups.
    variants_map: dict[str, list[dict]] = {}
    for c in cells:
        variants_map.setdefault(c["variant"], []).append(c)
    variant_rollup = []
    for variant, vcells in variants_map.items():
        roll = {"variant": variant, "cells": len(vcells)}
        for obj in OBJECTIVES:
            roll[obj] = _mean([c.get(obj) for c in vcells])
        variant_rollup.append(roll)

    result = {
        "rows": rows,
        "cells": cells,
        "variant_rollup": variant_rollup,
        "pareto_variants": [r["variant"] for r in pareto_front(variant_rollup)],
    }
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out} ({len(rows)} rows, {len(cells)} cells)", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
