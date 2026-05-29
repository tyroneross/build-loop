#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Block final Review exit while critical/high findings remain open."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

NORMALIZED = {"critical", "high", "medium", "low"}
SEVERITY_MAP = {
    "critical": "critical",
    "crit": "critical",
    "blocker": "high",
    "high": "high",
    "major": "high",
    "medium": "medium",
    "med": "medium",
    "minor": "medium",
    "low": "low",
    "info": "low",
    "informational": "low",
}
BLOCKING = {"critical", "high"}
CLOSED_STATES = {"closed", "resolved", "fixed", "done", "accepted"}
PROOF_FIELDS = ("closure_proof", "closureProof", "regression_proof", "resolution_evidence", "proof")


def normalize_severity(value: Any) -> str:
    if isinstance(value, str):
        return SEVERITY_MAP.get(value.strip().lower(), "high")
    return "high"


def _proof_present(finding: dict[str, Any]) -> bool:
    for field in PROOF_FIELDS:
        value = finding.get(field)
        if value not in (None, "", [], {}):
            return True
    return False


def _closed_state(finding: dict[str, Any]) -> bool:
    for field in ("status", "state", "resolution"):
        value = finding.get(field)
        if isinstance(value, str) and value.strip().lower() in CLOSED_STATES:
            return True
    return finding.get("closed") is True or finding.get("resolved") is True


def _extract_findings(payload: Any, source: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if isinstance(payload, list):
        raw_findings = payload
    elif isinstance(payload, dict):
        raw_findings = payload.get("findings", [])
        for severity in ("critical", "high"):
            count = payload.get(f"{severity}_count")
            if isinstance(count, int) and count > 0 and not raw_findings:
                findings.append({
                    "id": f"{source}:{severity}_count",
                    "severity": severity,
                    "evidence": f"{severity}_count={count} without finding details",
                })
    else:
        raw_findings = []

    for idx, item in enumerate(raw_findings):
        if isinstance(item, dict):
            copied = dict(item)
            copied.setdefault("id", f"{source}:finding-{idx + 1}")
            findings.append(copied)
    return findings


def evaluate_payloads(payloads: list[Any], sources: list[str] | None = None) -> dict[str, Any]:
    sources = sources or [f"payload-{idx + 1}" for idx in range(len(payloads))]
    normalized_findings: list[dict[str, Any]] = []
    counts = {severity: 0 for severity in NORMALIZED}

    for payload, source in zip(payloads, sources, strict=False):
        for finding in _extract_findings(payload, source):
            severity = normalize_severity(finding.get("severity"))
            counts[severity] += 1
            closed = _closed_state(finding)
            proof = _proof_present(finding)
            open_blocking = severity in BLOCKING and not (closed and proof)
            normalized_findings.append({
                "id": str(finding.get("id")),
                "source": source,
                "source_severity": finding.get("severity"),
                "normalized_severity": severity,
                "blocking": open_blocking,
                "closed": closed,
                "closure_proof_present": proof,
                "evidence": finding.get("evidence") or finding.get("snippet") or finding.get("observed"),
            })

    blocking = [finding for finding in normalized_findings if finding["blocking"]]
    return {
        "pass": not blocking,
        "blocking_count": len(blocking),
        "blocking_findings": blocking,
        "summary": {
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
        },
        "findings": normalized_findings,
    }


def _load_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"failed to read findings JSON {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate final Review on open critical/high findings")
    parser.add_argument("--findings-json", action="append", required=True, help="reviewer JSON file; repeatable")
    parser.add_argument("--json", action="store_true", help="emit JSON (default)")
    args = parser.parse_args(argv)

    payloads = [_load_json(path) for path in args.findings_json]
    result = evaluate_payloads(payloads, args.findings_json)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
