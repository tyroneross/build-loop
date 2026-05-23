#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Deterministic final-report lint for build-loop.

This linter covers report-shape failures that are too late for
``plan_verify.py``:

- multi-chunk independent work must record ``parallel_batch`` or
  ``parallel_skipped_reason``;
- verified/known claims must name observer, method, and artifact;
- multi-chunk/parallel work must include a merge_plan with required fields.

Stdlib-only by design so it can run inside Review-G without dependency setup.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


FENCE_RE = re.compile(r"^\s*```")
CHECK_MARK = "\u2705"
CHUNK_ID_RE = re.compile(r"\b(?:C|chunk[-_ ]?)(\d+)\b", re.IGNORECASE)
PARALLEL_SIGNAL_RE = re.compile(
    r"\b(independent|parallel[- ]safe|parallelizable|fan[- ]out|concurrent|worktree)\b",
    re.IGNORECASE,
)
PARALLEL_DECISION_RE = re.compile(
    r"\b(parallel_batch|parallel_skipped_reason)\b",
    re.IGNORECASE,
)
VERIFICATION_CLAIM_RE = re.compile(r"\b(verified|known)\b", re.IGNORECASE)
EVIDENCE_FIELD_RES = {
    "observer": re.compile(r"\b(observer|observed_by|who)\s*[:=]", re.IGNORECASE),
    "method": re.compile(r"\b(method|how)\s*[:=]", re.IGNORECASE),
    "artifact": re.compile(r"\b(artifact|evidence|log|screenshot)\s*[:=]", re.IGNORECASE),
}
MERGE_PLAN_REQUIRED_FIELDS = ("clean_against", "conflicts_with", "suggested_order")


def strip_fenced_blocks(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            lines.append((lineno, ""))
            continue
        lines.append((lineno, "" if in_fence else line))
    return lines


def _finding(
    *,
    rule_id: str,
    severity: str,
    line: int | None,
    snippet: str | None,
    message: str,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "message": message,
        "evidence": {"line": line, "snippet": snippet},
    }


def _chunk_ids(lines: list[tuple[int, str]]) -> set[str]:
    out: set[str] = set()
    for _lineno, line in lines:
        for match in CHUNK_ID_RE.finditer(line):
            out.add(match.group(1))
    return out


def _has_parallel_signal(lines: list[tuple[int, str]]) -> tuple[int, str] | None:
    for lineno, line in lines:
        if PARALLEL_SIGNAL_RE.search(line):
            return lineno, line
    return None


def lint_parallel_decision(
    report_path: Path, lines: list[tuple[int, str]]
) -> list[dict[str, Any]]:
    del report_path
    signal = _has_parallel_signal(lines)
    if signal is None or len(_chunk_ids(lines)) < 2:
        return []
    if any(PARALLEL_DECISION_RE.search(line) for _lineno, line in lines):
        return []
    lineno, line = signal
    return [_finding(
        rule_id="parallel-decision-record",
        severity="BLOCKER",
        line=lineno,
        snippet=line.strip(),
        message=(
            "Report describes independent/parallel multi-chunk work but omits "
            "`parallel_batch` or `parallel_skipped_reason`."
        ),
    )]


def lint_verification_evidence(
    report_path: Path, lines: list[tuple[int, str]]
) -> list[dict[str, Any]]:
    del report_path
    findings: list[dict[str, Any]] = []
    for lineno, line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if CHECK_MARK not in stripped and not VERIFICATION_CLAIM_RE.search(stripped):
            continue
        missing = [
            field
            for field, pattern in EVIDENCE_FIELD_RES.items()
            if not pattern.search(stripped)
        ]
        if missing:
            findings.append(_finding(
                rule_id="verification-evidence",
                severity="BLOCKER",
                line=lineno,
                snippet=stripped,
                message=(
                    "Verified/known claim must name observer, method, and "
                    f"artifact; missing: {', '.join(missing)}."
                ),
            ))
    return findings


def lint_merge_plan(
    report_path: Path, lines: list[tuple[int, str]]
) -> list[dict[str, Any]]:
    del report_path
    chunks = _chunk_ids(lines)
    if len(chunks) < 2 and not any(PARALLEL_DECISION_RE.search(line) for _lineno, line in lines):
        return []

    merge_line: tuple[int, str] | None = None
    fields_found: set[str] = set()
    for lineno, line in lines:
        if re.search(r"\bmerge_plan\s*:", line, re.IGNORECASE):
            merge_line = (lineno, line)
        for field in MERGE_PLAN_REQUIRED_FIELDS:
            if re.search(rf"\b{re.escape(field)}\s*:", line):
                fields_found.add(field)

    if merge_line is None:
        trigger_line = next((item for item in lines if item[1].strip()), (None, None))
        return [_finding(
            rule_id="merge-plan-required",
            severity="BLOCKER",
            line=trigger_line[0],
            snippet=trigger_line[1].strip() if trigger_line[1] else None,
            message=(
                "Multi-chunk or parallel report must include `merge_plan:` "
                "with clean_against, conflicts_with, and suggested_order."
            ),
        )]

    missing = [field for field in MERGE_PLAN_REQUIRED_FIELDS if field not in fields_found]
    if not missing:
        return []
    lineno, line = merge_line
    return [_finding(
        rule_id="merge-plan-fields",
        severity="BLOCKER",
        line=lineno,
        snippet=line.strip(),
        message=f"`merge_plan` missing required field(s): {', '.join(missing)}.",
    )]


def run_lint(report_path: Path) -> dict[str, Any]:
    text = report_path.read_text(encoding="utf-8")
    lines = strip_fenced_blocks(text)
    findings: list[dict[str, Any]] = []
    findings.extend(lint_parallel_decision(report_path, lines))
    findings.extend(lint_verification_evidence(report_path, lines))
    findings.extend(lint_merge_plan(report_path, lines))
    summary = {
        "total": len(findings),
        "by_severity": {
            "BLOCKER": sum(1 for f in findings if f["severity"] == "BLOCKER"),
            "WARN": sum(1 for f in findings if f["severity"] == "WARN"),
            "INFO": sum(1 for f in findings if f["severity"] == "INFO"),
        },
    }
    return {"report": str(report_path), "summary": summary, "findings": findings}


def render_human(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        f"# build-report-lint — {Path(result['report']).name}",
        "",
        (
            f"Total findings: {summary['total']} "
            f"(BLOCKER={summary['by_severity']['BLOCKER']}, "
            f"WARN={summary['by_severity']['WARN']}, "
            f"INFO={summary['by_severity']['INFO']})"
        ),
    ]
    if not result["findings"]:
        lines.append("No findings.")
        return "\n".join(lines)
    lines.append("")
    for finding in result["findings"]:
        ev = finding["evidence"]
        lines.append(
            f"- [{finding['severity']}] {finding['rule_id']} "
            f"line {ev['line']}: {finding['message']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint build-loop final report shape.")
    parser.add_argument("report", help="Path to final report markdown")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--quiet", action="store_true", help="Suppress human output")
    args = parser.parse_args(argv)

    report_path = Path(args.report).expanduser().resolve()
    if not report_path.exists():
        print(f"build-report-lint: file not found: {report_path}", file=sys.stderr)
        return 2

    try:
        result = run_lint(report_path)
    except Exception as exc:  # noqa: BLE001 - verifier outage maps to exit 2
        print(f"build-report-lint: error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    elif not args.quiet:
        print(render_human(result))

    return 1 if result["summary"]["by_severity"]["BLOCKER"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
