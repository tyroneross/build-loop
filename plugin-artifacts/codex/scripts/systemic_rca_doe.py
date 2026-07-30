#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Build and score DOE packets for systemic RCA protocol experiments.
#   application: validation
#   status: experimental
"""Build run packets and score results for the systemic RCA DOE."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import systemic_rca_eval


OUTPUT_CONTRACT = [
    "plain_language_failure",
    "why_it_happened",
    "failure_map",
    "system_control_failure",
    "failure_classification",
    "technical_details.evidence",
    "pruned_causes",
    "tradeoffs",
    "impact",
    "prevention_control",
]

FACTOR_INSTRUCTIONS: dict[str, dict[str, str]] = {
    "explanation_format": {
        "technical_first": "Begin with the technical diagnosis, then summarize user impact.",
        "plain_language_then_system_cause": (
            "Begin with a plain-language failure explanation, then name the system cause."
        ),
    },
    "framework_core": {
        "causal_tree_only": "Use a causal tree and evidence-pruned branches.",
        "cast_stpa_control_gap": (
            "Use a causal tree and identify the failed controller, feedback path, or control."
        ),
    },
    "failure_map_shape": {
        "linear_why_chain": "Use a concise why-chain from symptom to cause.",
        "fault_tree_plus_dependency_chain": (
            "Use a failure map that includes symptom, technical failure, dependency, and control."
        ),
    },
    "forward_scan": {
        "none": "Focus on the observed failure.",
        "fmea_interface_failure_modes": (
            "Also name the interface failure mode, effect, detection path, and control."
        ),
    },
    "classification": {
        "freeform": "Classify the failure in your own words.",
        "odc_taxonomy": "Use one of the known failure_classification values from the contract.",
    },
    "evidence_method": {
        "manual_evidence": "Use the provided code, test, log, trace, and state evidence.",
        "trace_or_delta_debug_required": (
            "Prefer trace evidence or a minimized failure-inducing change when available."
        ),
    },
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_reports(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("reports"), list):
        reports = payload["reports"]
    elif isinstance(payload, list):
        reports = payload
    elif isinstance(payload, dict):
        reports = [payload]
    else:
        raise ValueError(f"{path}: expected a report object or report list")
    if not all(isinstance(item, dict) for item in reports):
        raise ValueError(f"{path}: every report must be a JSON object")
    return reports


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return value.strip("-") or "case"


def _case_from_report(report: dict[str, Any], index: int) -> dict[str, Any]:
    classification = str(report.get("failure_classification") or "unknown")
    evidence = report.get("technical_details", {}).get("evidence", [])
    return {
        "case_id": f"case-{index + 1:03d}-{_slug(classification)}",
        "symptom": report.get("plain_language_failure", ""),
        "available_evidence": evidence,
        "expected_output_contract": OUTPUT_CONTRACT,
    }


def _protocol_from_factors(factors: dict[str, str]) -> dict[str, Any]:
    instructions = []
    for factor_name, level in factors.items():
        instruction = FACTOR_INSTRUCTIONS.get(factor_name, {}).get(level)
        if instruction:
            instructions.append({
                "factor": factor_name,
                "level": level,
                "instruction": instruction,
            })
    return {
        "factors": factors,
        "instructions": instructions,
        "output_contract": OUTPUT_CONTRACT,
    }


def build_packets(design_path: Path, corpus_path: Path) -> list[dict[str, Any]]:
    design = _load_json(design_path)
    reports = _load_reports(corpus_path)
    cases = [_case_from_report(report, index) for index, report in enumerate(reports)]
    packets = []
    for run in design["runs"]:
        run_id = int(run["_run_id"])
        factors = dict(run["_factors"])
        packets.append({
            "run_id": run_id,
            "protocol": _protocol_from_factors(factors),
            "cases": cases,
            "score_command": (
                "python3 scripts/systemic_rca_eval.py "
                f"<results-dir>/run-{run_id:02d}.json --score-only"
            ),
        })
    return packets


def expected_run_ids(design_path: Path) -> set[int]:
    design = _load_json(design_path)
    return {int(run["_run_id"]) for run in design["runs"]}


def write_packets(packets: list[dict[str, Any]], outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    for packet in packets:
        path = outdir / f"run-{int(packet['run_id']):02d}.json"
        path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def score_results(results_dir: Path, expected_ids: set[int] | None = None) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(results_dir.glob("run-*.json")):
        match = re.search(r"run-(\d+)\.json$", path.name)
        if not match:
            continue
        run_id = int(match.group(1))
        result = systemic_rca_eval.evaluate_paths([path])
        rows.append({
            "run_id": run_id,
            "value": result["summary"]["mean_score"],
            "reports": result["summary"]["reports"],
            "passed": result["summary"]["passed"],
        })
    if not rows:
        raise ValueError(f"{results_dir}: no run-*.json result files found")
    observed = {int(row["run_id"]) for row in rows}
    if expected_ids is not None and observed != expected_ids:
        missing = sorted(expected_ids - observed)
        extra = sorted(observed - expected_ids)
        raise ValueError(
            f"{results_dir}: incomplete DOE results; missing={missing}, extra={extra}"
        )
    return rows


def _cmd_build_packets(args: argparse.Namespace) -> int:
    try:
        packets = build_packets(args.design, args.corpus)
        if args.outdir:
            write_packets(packets, args.outdir)
            payload: Any = {"packets": len(packets), "outdir": str(args.outdir)}
        else:
            payload = {"packets": packets}
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        print(f"systemic_rca_doe: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _cmd_score_results(args: argparse.Namespace) -> int:
    try:
        expected_ids = expected_run_ids(args.design) if args.design else None
        rows = score_results(args.results_dir, expected_ids)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"systemic_rca_doe: {exc}", file=sys.stderr)
        return 2
    if args.jsonl:
        for row in rows:
            print(json.dumps({"run_id": row["run_id"], "value": row["value"]}, sort_keys=True))
    else:
        print(json.dumps({"results": rows}, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    build = subparsers.add_parser("build-packets", help="Build DOE run packets")
    build.add_argument("--design", required=True, type=Path)
    build.add_argument("--corpus", required=True, type=Path)
    build.add_argument("--outdir", type=Path)
    build.set_defaults(func=_cmd_build_packets)

    score = subparsers.add_parser("score-results", help="Score run output files")
    score.add_argument("--results-dir", required=True, type=Path)
    score.add_argument("--design", type=Path, help="Require exactly the run IDs in this DOE design")
    score.add_argument("--jsonl", action="store_true", help="Emit optimize_doe-compatible JSONL")
    score.set_defaults(func=_cmd_score_results)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
