#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Compare exact-repeat Build Loop runs using measured tokens and quality outcomes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def measured_tokens(row: dict[str, Any]) -> int | None:
    explicit = row.get("measured_total_tokens")
    if isinstance(explicit, int) and explicit >= 0:
        return explicit
    values = [row.get(field) for field in TOKEN_FIELDS]
    if not any(isinstance(value, int) and value >= 0 for value in values):
        return None
    return sum(value for value in values if isinstance(value, int) and value >= 0)


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"line {line_no}: row must be a JSON object")
        for required in ("task_id", "variant", "model", "snapshot", "passed"):
            if required not in row:
                raise ValueError(f"line {line_no}: missing {required}")
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
        "raw_tokens_per_passed_run": round(total_tokens / passed, 2) if passed else None,
        "duration_seconds": round(sum(float(row.get("duration_seconds") or 0) for row in rows), 3),
    }


def exact_repeat_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return str(row["task_id"]), str(row["snapshot"]), str(row["model"])


def compare(
    rows: list[dict[str, Any]],
    *,
    baseline: str,
    candidate: str,
) -> dict[str, Any]:
    by_variant: dict[str, list[dict[str, Any]]] = {}
    indexed: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        variant = str(row["variant"])
        by_variant.setdefault(variant, []).append(row)
        indexed.setdefault(exact_repeat_key(row), {})[variant] = row

    pairs = [
        (variants[baseline], variants[candidate])
        for variants in indexed.values()
        if baseline in variants and candidate in variants
    ]
    measured_pairs = [
        (left, right, measured_tokens(left), measured_tokens(right))
        for left, right in pairs
        if measured_tokens(left) is not None and measured_tokens(right) is not None
    ]
    baseline_tokens = sum(left_tokens for _, _, left_tokens, _ in measured_pairs)
    candidate_tokens = sum(right_tokens for _, _, _, right_tokens in measured_pairs)
    token_change_pct = (
        round((candidate_tokens - baseline_tokens) / baseline_tokens * 100, 2)
        if baseline_tokens
        else None
    )
    baseline_passed = sum(bool(left.get("passed")) for left, _ in pairs)
    candidate_passed = sum(bool(right.get("passed")) for _, right in pairs)
    baseline_defects = sum(int(left.get("escaped_defects") or 0) for left, _ in pairs)
    candidate_defects = sum(int(right.get("escaped_defects") or 0) for _, right in pairs)

    return {
        "baseline": baseline,
        "candidate": candidate,
        "variants": {name: aggregate(group) for name, group in sorted(by_variant.items())},
        "exact_repeat": {
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
            ),
        },
        "note": "Only exact task_id + snapshot + model pairs support the A/B conclusion; token estimates are excluded.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = compare(
            load_rows(args.results), baseline=args.baseline, candidate=args.candidate
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
