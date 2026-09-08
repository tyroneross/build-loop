#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Compare exact-repeat Build Loop runs using measured tokens and quality outcomes."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

# Pilot controls are declared by the experiment operator, not inferred by an LLM.
PILOT_CONTROLS = ("experiment_axis", "task_shape", "controls_id")
PILOT_METRICS = ("duration_seconds", "user_interventions", "lost_decisions", "rework", "escaped_defects")
PILOT_RATIOS = {
    "grounding": ("grounded_claims", "checked_claims"),
    "coverage": ("covered_items", "expected_items"),
}


def validate_pilot_row(row: dict[str, Any]) -> None:
    for field in (*PILOT_CONTROLS, "trial_id"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ValueError(f"pilot requires nonempty {field}")
    if row["experiment_axis"] not in {"context", "execution"}:
        raise ValueError("experiment_axis must be context or execution")
    if row.get("evidence_kind") not in {"live", "calibration"}:
        raise ValueError("pilot evidence_kind must be live or calibration")
    count_fields = (*PILOT_METRICS[1:], *(f for pair in PILOT_RATIOS.values() for f in pair))
    for field in count_fields:
        if field in row and (type(row[field]) is not int or row[field] < 0):
            raise ValueError(f"{field} must be a nonnegative integer")
    for numerator, denominator in PILOT_RATIOS.values():
        if numerator in row and denominator in row and row[numerator] > row[denominator]:
            raise ValueError(f"{numerator} cannot exceed {denominator}")


def paired_metric(pairs: list[tuple[dict, dict]], fields: tuple[str, ...]) -> dict[str, Any]:
    """Report only fully observed pairs; never replace missing counts with zero."""
    measured = [(a, b) for a, b in pairs if all(
        field in row for row in (a, b) for field in fields
    ) and (len(fields) == 1 or (a[fields[1]] > 0 and b[fields[1]] > 0))]
    complete = bool(pairs) and len(measured) == len(pairs)
    totals = [sum(row[fields[0]] for row in side) for side in zip(*measured)] if measured else []
    if len(fields) == 2 and measured:
        denominators = [sum(row[fields[1]] for row in side) for side in zip(*measured)]
        totals = [total / denominator for total, denominator in zip(totals, denominators)]
    return {
        "measured_pairs": len(measured),
        "missing_pairs": len(pairs) - len(measured),
        "baseline": round(totals[0], 6) if complete else None,
        "candidate": round(totals[1], 6) if complete else None,
        "change": round(totals[1] - totals[0], 6) if complete else None,
    }


def summarize_pilot(pairs: list[tuple[dict, dict]]) -> dict[str, Any]:
    metrics = {name: paired_metric(pairs, (name,)) for name in PILOT_METRICS}
    token_pairs = [tuple(
        {"tokens": total} if (total := measured_tokens(row)) is not None else {}
        for row in (a, b)
    ) for a, b in pairs]
    metrics["raw_tokens"] = paired_metric(token_pairs, ("tokens",))
    metrics.update({name: paired_metric(pairs, fields) for name, fields in PILOT_RATIOS.items()})
    return {
        "pairs": len(pairs),
        "metrics": metrics,
        "baseline_passed": sum(a["passed"] for a, _ in pairs),
        "candidate_passed": sum(b["passed"] for _, b in pairs),
        "regressed_pairs": sum(a["passed"] and not b["passed"] for a, b in pairs),
    }


def measured_tokens(row: dict[str, Any]) -> int | None:
    explicit = row.get("measured_total_tokens")
    if explicit is not None:
        return explicit if type(explicit) is int and explicit >= 0 else None
    # A lone output/cache bucket cannot establish the total cost of a run.
    if not all(type(row.get(field)) is int and row[field] >= 0 for field in TOKEN_FIELDS[:2]):
        return None
    values = [row.get(field) for field in TOKEN_FIELDS]
    if any(value is not None and (type(value) is not int or value < 0) for value in values):
        return None
    return sum(value for value in values if value is not None)


def validate_row(row: dict[str, Any]) -> None:
    for field in ("task_id", "variant", "model", "snapshot"):
        if field not in row:
            raise ValueError(f"missing {field}")
        if not isinstance(row[field], str) or not row[field].strip():
            raise ValueError(f"{field} must be a nonempty string")
    if type(row.get("passed")) is not bool:
        raise ValueError("passed must be a boolean")
    if "trial_id" in row and (not isinstance(row["trial_id"], str) or not row["trial_id"].strip()):
        raise ValueError("trial_id must be a nonempty string")
    for field in (*TOKEN_FIELDS, "measured_total_tokens", "escaped_defects", "calls"):
        if field in row and (type(row[field]) is not int or row[field] < 0):
            raise ValueError(f"{field} must be a nonnegative integer")
    duration = row.get("duration_seconds", 0)
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
        raise ValueError("duration_seconds must be a finite nonnegative number")


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"line {line_no}: row must be a JSON object")
        try:
            validate_row(row)
        except ValueError as exc:
            raise ValueError(f"line {line_no}: {exc}") from exc
        rows.append(row)
    return rows


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tokens = [value for row in rows if (value := measured_tokens(row)) is not None]
    passed = sum(bool(row.get("passed")) for row in rows)
    total_tokens = sum(tokens)
    return {
        "runs": len(rows),
        "measured_runs": len(tokens),
        "unmeasured_runs": len(rows) - len(tokens),
        "passed": passed,
        "pass_rate": round(passed / len(rows), 4) if rows else None,
        "escaped_defects": sum(int(row.get("escaped_defects") or 0) for row in rows),
        "calls": sum(int(row.get("calls") or 0) for row in rows),
        "raw_tokens": total_tokens,
        "raw_tokens_per_passed_run": (
            round(total_tokens / passed, 2) if passed and len(tokens) == len(rows) else None
        ),
        "duration_seconds": round(sum(float(row.get("duration_seconds") or 0) for row in rows), 3),
    }


def exact_repeat_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return row["task_id"], row["snapshot"], row["model"], row.get("trial_id", "")


def compare(
    rows: list[dict[str, Any]],
    *,
    baseline: str,
    candidate: str,
    pilot: bool = False,
) -> dict[str, Any]:
    if baseline == candidate:
        raise ValueError("baseline and candidate must be different variants")
    by_variant: dict[str, list[dict[str, Any]]] = {}
    indexed: dict[tuple[str, str, str, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        validate_row(row)
        if pilot:
            validate_pilot_row(row)
        variant = str(row["variant"])
        by_variant.setdefault(variant, []).append(row)
        variants = indexed.setdefault(exact_repeat_key(row), {})
        if variant in variants:
            raise ValueError(f"duplicate exact-repeat row for {variant}: {exact_repeat_key(row)!r}; use unique trial_id values")
        variants[variant] = row

    pairs = [
        (variants[baseline], variants[candidate])
        for variants in indexed.values()
        if baseline in variants and candidate in variants
    ]
    pilot_result = None
    if pilot:
        confounded = [{
            "task_id": a["task_id"], "trial_id": a["trial_id"],
            "mismatched_controls": [field for field in (*PILOT_CONTROLS, "evidence_kind") if a[field] != b[field]],
        } for a, b in pairs if any(a[field] != b[field] for field in (*PILOT_CONTROLS, "evidence_kind"))]
        matched_count = len(pairs)
        pairs = [(a, b) for a, b in pairs if all(a[field] == b[field] for field in (*PILOT_CONTROLS, "evidence_kind"))]
        strata: dict[str, list[tuple[dict, dict]]] = {}
        for a, b in pairs:
            key = json.dumps([a["evidence_kind"], a["experiment_axis"], a["task_shape"]])
            strata.setdefault(key, []).append((a, b))
        pilot_result = {
            "status": "exploratory_only",
            "confounded_pairs": confounded,
            "unmatched_runs": sum(row["variant"] in {baseline, candidate} for row in rows) - 2 * matched_count,
            "strata": [{
                "evidence_kind": group[0][0]["evidence_kind"],
                "experiment_axis": group[0][0]["experiment_axis"],
                "task_shape": group[0][0]["task_shape"],
                **summarize_pilot(group),
            } for _, group in sorted(strata.items())],
            "note": "Declared controls are not independently verified. Calibration is synthetic; live strata are descriptive, not causal or statistical proof. No automatic routing change or promotion.",
        }
    measured_pairs = [
        (left, right, measured_tokens(left), measured_tokens(right))
        for left, right in pairs
        if measured_tokens(left) is not None and measured_tokens(right) is not None
    ]
    baseline_tokens = sum(left_tokens for _, _, left_tokens, _ in measured_pairs)
    candidate_tokens = sum(right_tokens for _, _, _, right_tokens in measured_pairs)
    token_change_pct = (
        round((candidate_tokens - baseline_tokens) / baseline_tokens * 100, 2)
        if baseline_tokens and len(measured_pairs) == len(pairs)
        else None
    )
    baseline_passed = sum(bool(left.get("passed")) for left, _ in pairs)
    candidate_passed = sum(bool(right.get("passed")) for _, right in pairs)
    baseline_defects = sum(int(left.get("escaped_defects") or 0) for left, _ in pairs)
    candidate_defects = sum(int(right.get("escaped_defects") or 0) for _, right in pairs)

    result = {
        "baseline": baseline,
        "candidate": candidate,
        "variants": {name: aggregate(group) for name, group in sorted(by_variant.items())},
        "exact_repeat": {
            "evidence_status": (
                "complete" if pairs and len(measured_pairs) == len(pairs) else "insufficient_evidence"
            ),
            "pairs": len(pairs),
            "measured_pairs": len(measured_pairs),
            "baseline_raw_tokens": baseline_tokens,
            "candidate_raw_tokens": candidate_tokens,
            "token_change_pct": token_change_pct,
            "baseline_passed": baseline_passed,
            "candidate_passed": candidate_passed,
            "baseline_escaped_defects": baseline_defects,
            "candidate_escaped_defects": candidate_defects,
            "quality_non_inferior": (
                candidate_passed >= baseline_passed and candidate_defects <= baseline_defects
            ) if pairs else None,
        },
        "note": "Only exact task_id + snapshot + model + trial_id pairs support the A/B conclusion; token estimates and incomplete totals are excluded, and every pair needs measured totals for a token-change claim.",
    }
    if pilot_result is not None:
        # A pooled win across different interventions or synthetic/live evidence
        # is not an architecture decision. The strata carry the useful results.
        result["pilot"] = pilot_result
        # Legacy aggregates treat absent optional counts as zero. Pilot users
        # must instead consume the missing-data-aware paired metrics above.
        result.pop("variants")
        result["exact_repeat"]["baseline_escaped_defects"] = None
        result["exact_repeat"]["candidate_escaped_defects"] = None
        result["exact_repeat"]["quality_non_inferior"] = None
        result["exact_repeat"]["token_change_pct"] = None
        result["note"] = "Pilot mode excludes mismatched declared controls. Use per-stratum results; pooled quality and token-win claims are disabled."
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--pilot", action="store_true", help="Require declared experimental controls and report task-shape strata")
    args = parser.parse_args(argv)
    try:
        result = compare(
            load_rows(args.results), baseline=args.baseline, candidate=args.candidate,
            pilot=args.pilot,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
