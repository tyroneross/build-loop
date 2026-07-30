#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Score systemic RCA reports for plain language, failure-map depth, evidence, and prevention controls.
#   application: validation
#   status: experimental
"""Deterministic evaluator for systemic root-cause-analysis reports.

The scorer is intentionally simple. It does not decide whether the RCA is
"true"; it checks whether the report has the control-oriented shape needed
for a build-loop DOE experiment.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RULE_WEIGHTS: dict[str, int] = {
    "plain_language_first": 15,
    "why_chain_present": 10,
    "failure_map_depth": 15,
    "system_control_failure": 15,
    "actor_blame_guard": 15,
    "evidence_two_types": 10,
    "failure_classification": 5,
    "pruned_alternatives": 5,
    "tradeoffs_impact": 5,
    "prevention_control": 5,
}
ALLOWED_FAILURE_CLASSES = {
    "ambiguous-contract",
    "cache-drift",
    "context-packet-gap",
    "dependency-provenance-gap",
    "environment-misread",
    "evidence-gap",
    "missing-test-trigger",
    "multi-session-coordination-gap",
    "observability-gap",
    "runtime-smoke-gap",
    "scope-audit-gap",
    "test-fixture-gap",
    "ui-contract-gap",
    "warning-baseline-gap",
}

CONTROL_RE = re.compile(
    r"\b("
    r"check|checker|contract|control|constraint|feedback|gate|guard|handoff|"
    r"invariant|lint|owner|ownership|policy|protocol|routing|scope|smoke|"
    r"test|trace|validator|verifier"
    r")\b",
    re.IGNORECASE,
)
BLAME_CONTROL_RE = re.compile(
    r"\b("
    r"contract|control|feedback|gate|guard|handoff|invariant|lint|owner|"
    r"ownership|policy|protocol|routing|scope|smoke|test|trace|validator|"
    r"verifier"
    r")\b",
    re.IGNORECASE,
)
ACTOR_BLAME_RE = re.compile(
    r"\b(agent|assistant|model|user|developer|codex|claude)\b.{0,40}"
    r"\b(forgot|missed|overlooked|failed to notice|did not notice|didn't notice)\b",
    re.IGNORECASE,
)
PLAIN_JARGON_RE = re.compile(
    r"\b("
    r"acci.?map|api|cast|causal tree|fmea|fmeca|fram|odc|orthogonal|"
    r"race condition|schema|stamp|stpa|stack trace"
    r")\b",
    re.IGNORECASE,
)
EVIDENCE_KEYWORDS: dict[str, re.Pattern[str]] = {
    "code": re.compile(r"\b(code|file|function|import|call site|diff)\b", re.IGNORECASE),
    "test": re.compile(r"\b(test|pytest|vitest|xcodebuild|assert|fixture)\b", re.IGNORECASE),
    "log": re.compile(r"\b(log|stderr|stdout|stack trace|error output)\b", re.IGNORECASE),
    "trace": re.compile(r"\b(trace|span|otel|open.?telemetry|request id)\b", re.IGNORECASE),
    "state": re.compile(r"\b(state|config|env|cache|database|worktree)\b", re.IGNORECASE),
}


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    passed: bool
    weight: int
    message: str


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return " ".join(_text(v) for v in value.values()).strip()
    if isinstance(value, list):
        return " ".join(_text(v) for v in value).strip()
    return str(value).strip()


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _field(report: dict[str, Any], *names: str) -> Any:
    for name in names:
        current: Any = report
        ok = True
        for part in name.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                ok = False
                break
        if ok:
            return current
    return None


def _evidence_types(report: dict[str, Any]) -> set[str]:
    evidence = _list(_field(report, "evidence", "technical_details.evidence"))
    found: set[str] = set()
    for item in evidence:
        if isinstance(item, dict):
            explicit = _text(item.get("type") or item.get("evidence_type")).lower()
            if explicit:
                found.add(explicit)
        text = _text(item)
        for name, pattern in EVIDENCE_KEYWORDS.items():
            if pattern.search(text):
                found.add(name)
    return found


def _has_actor_blame_without_control(report: dict[str, Any]) -> bool:
    narrative_fields = [
        _text(_field(report, "plain_language_failure", "plain_language")),
        _text(_field(report, "why_it_happened", "why", "causal_chain")),
        _text(_field(report, "root_cause.description", "root_cause")),
        _text(_field(report, "system_control_failure")),
    ]
    return any(
        ACTOR_BLAME_RE.search(field) and not BLAME_CONTROL_RE.search(field)
        for field in narrative_fields
        if field
    )


def evaluate_report(report: dict[str, Any]) -> dict[str, Any]:
    results: list[RuleResult] = []

    plain = _text(_field(report, "plain_language_failure", "plain_language"))
    results.append(RuleResult(
        "plain_language_first",
        bool(plain) and _word_count(plain) <= 80 and not PLAIN_JARGON_RE.search(plain),
        RULE_WEIGHTS["plain_language_first"],
        "Plain-language failure exists, is concise, and avoids framework/technical jargon.",
    ))

    why = _text(_field(report, "why_it_happened", "why", "causal_chain"))
    results.append(RuleResult(
        "why_chain_present",
        bool(why),
        RULE_WEIGHTS["why_chain_present"],
        "Report includes a why-it-happened chain.",
    ))

    failure_map = _list(_field(report, "failure_map", "failure_chain"))
    results.append(RuleResult(
        "failure_map_depth",
        len(failure_map) >= 4,
        RULE_WEIGHTS["failure_map_depth"],
        "Failure map has at least four levels: symptom, technical failure, upstream dependency, system control.",
    ))

    system_control = _text(_field(report, "system_control_failure", "control_structure_gap"))
    results.append(RuleResult(
        "system_control_failure",
        bool(system_control) and CONTROL_RE.search(system_control) is not None,
        RULE_WEIGHTS["system_control_failure"],
        "Terminal cause names the failed or missing controllable system control.",
    ))

    results.append(RuleResult(
        "actor_blame_guard",
        not _has_actor_blame_without_control(report),
        RULE_WEIGHTS["actor_blame_guard"],
        "Actor-blame phrases are paired with the missing system control that allowed the failure.",
    ))

    evidence_types = _evidence_types(report)
    results.append(RuleResult(
        "evidence_two_types",
        len(evidence_types) >= 2,
        RULE_WEIGHTS["evidence_two_types"],
        f"At least two evidence types are present; observed: {sorted(evidence_types)}.",
    ))

    classification = _text(_field(report, "failure_classification", "classification")).lower()
    results.append(RuleResult(
        "failure_classification",
        classification in ALLOWED_FAILURE_CLASSES,
        RULE_WEIGHTS["failure_classification"],
        f"Report uses a known process-failure class; observed: {classification or 'missing'}.",
    ))

    pruned = _list(_field(report, "pruned_causes", "pruned_branches"))
    results.append(RuleResult(
        "pruned_alternatives",
        len(pruned) > 0,
        RULE_WEIGHTS["pruned_alternatives"],
        "Report records rejected alternatives or pruned branches.",
    ))

    tradeoffs = _text(_field(report, "tradeoffs"))
    impact = _text(_field(report, "impact"))
    results.append(RuleResult(
        "tradeoffs_impact",
        bool(tradeoffs) and bool(impact),
        RULE_WEIGHTS["tradeoffs_impact"],
        "Report names tradeoffs and impact.",
    ))

    prevention = _text(_field(report, "prevention_control", "preventive_control"))
    results.append(RuleResult(
        "prevention_control",
        bool(prevention) and CONTROL_RE.search(prevention) is not None,
        RULE_WEIGHTS["prevention_control"],
        "Report names a durable prevention control.",
    ))

    earned = sum(result.weight for result in results if result.passed)
    total = sum(RULE_WEIGHTS.values())
    findings = [
        {
            "rule_id": result.rule_id,
            "passed": result.passed,
            "weight": result.weight,
            "message": result.message,
        }
        for result in results
    ]
    return {
        "score": round((earned / total) * 100, 2),
        "earned": earned,
        "possible": total,
        "findings": findings,
    }


def _load_reports(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("reports"), list):
        payload = payload["reports"]
    if isinstance(payload, list):
        reports = payload
    else:
        reports = [payload]
    out: list[dict[str, Any]] = []
    for item in reports:
        if not isinstance(item, dict):
            raise ValueError(f"{path}: each report must be a JSON object")
        out.append(item)
    return out


def evaluate_paths(paths: list[Path]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for idx, report in enumerate(_load_reports(path)):
            result = evaluate_report(report)
            result["path"] = str(path)
            result["index"] = idx
            result["passed"] = result["score"] >= 80
            rows.append(result)
    mean = sum(float(row["score"]) for row in rows) / len(rows) if rows else 0.0
    return {
        "summary": {
            "reports": len(rows),
            "mean_score": round(mean, 2),
            "passed": sum(1 for row in rows if row["passed"]),
            "pass_threshold": 80,
        },
        "reports": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path, help="JSON RCA report file(s)")
    parser.add_argument("--json", action="store_true", help="Emit full JSON result")
    parser.add_argument("--score-only", action="store_true", help="Emit only the mean numeric score")
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="Exit 1 if the mean score is below this threshold",
    )
    args = parser.parse_args(argv)

    try:
        result = evaluate_paths(args.reports)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"systemic_rca_eval: {exc}", file=sys.stderr)
        return 2

    mean_score = float(result["summary"]["mean_score"])
    if args.score_only:
        print(f"{mean_score:.2f}")
    elif args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"reports={result['summary']['reports']} "
            f"mean_score={mean_score:.2f} "
            f"passed={result['summary']['passed']}"
        )

    if args.fail_under is not None and mean_score < args.fail_under:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
