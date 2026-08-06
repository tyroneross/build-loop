#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Block final Review exit while critical/high or same-intent findings remain open.

The severity gate protects quality. The intent-closure gate separately protects
the user's requested outcome: a medium/low finding still blocks when it is a
known, in-scope part of satisfying the original request and lacks an evidenced
terminal disposition. The returned ``orchestrator_route`` makes that failure
actionable instead of leaving it as report prose.
"""
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
INTENT_TERMINAL_DISPOSITIONS = {"fixed", "user_deferred", "external_blocked", "waived"}
INTENT_RELATIONS = {"same_intent", "adjacent", "out_of_scope", "unknown"}
FIXED_PROOF_FIELDS = ("closure_proof", "closureProof", "regression_proof", "resolution_evidence", "proof")


def normalize_severity(value: Any) -> str:
    if isinstance(value, str):
        return SEVERITY_MAP.get(value.strip().lower(), "high")
    return "high"


def _proof_present(finding: dict[str, Any]) -> bool:
    """Return real closure evidence for the legacy critical/high gate."""
    for field in FIXED_PROOF_FIELDS:
        value = finding.get(field)
        if value not in (None, "", [], {}):
            return True
    return False


def _terminal_proof_present(finding: dict[str, Any], disposition: str) -> bool:
    """Require evidence that matches the claimed same-intent disposition."""
    present = lambda field: finding.get(field) not in (None, "", [], {})
    if disposition == "fixed":
        return any(present(field) for field in FIXED_PROOF_FIELDS)
    if disposition == "user_deferred":
        return (
            (present("decision_record") or present("user_decision_record"))
            and finding.get("decision_authority") == "user"
        )
    if disposition == "external_blocked":
        return (
            (present("blocker_evidence") or present("external_blocker_evidence"))
            and present("remaining_action")
        )
    if disposition == "waived":
        return all(
            present(field)
            for field in ("waiver_record", "waiver_scope", "waiver_expiry")
        ) and finding.get("waiver_approved_by") == "user"
    return False


def _closed_state(finding: dict[str, Any]) -> bool:
    for field in ("status", "state", "resolution"):
        value = finding.get(field)
        if isinstance(value, str) and value.strip().lower() in CLOSED_STATES:
            return True
    return finding.get("closed") is True or finding.get("resolved") is True


def _intent_relation(finding: dict[str, Any]) -> str:
    value = finding.get("intent_relation") or finding.get("scope_relation")
    if finding.get("same_intent") is True:
        return "same_intent"
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "in_scope": "same_intent",
        "same_scope": "same_intent",
        "natural_next_step": "same_intent",
        "out_of_scope": "out_of_scope",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in INTENT_RELATIONS else "unknown"


def _disposition(finding: dict[str, Any]) -> str:
    value = finding.get("disposition")
    if not isinstance(value, str):
        for field in ("status", "state", "resolution"):
            candidate = finding.get(field)
            if isinstance(candidate, str):
                value = candidate
                break
    if not isinstance(value, str):
        return "open"
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "closed": "fixed",
        "resolved": "fixed",
        "done": "fixed",
        "accepted": "fixed",
        "deferred_by_user": "user_deferred",
        "blocked_external": "external_blocked",
    }
    return aliases.get(normalized, normalized)


def _recommended_phase(finding: dict[str, Any]) -> str:
    value = finding.get("recommended_phase") or finding.get("next_phase")
    if isinstance(value, str) and value.strip().lower() in {"replan", "iterate", "execute"}:
        return value.strip().lower()
    if finding.get("plan_narrowed") is True:
        return "replan"
    return "iterate"


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


def _routing_findings(
    payload: Any,
    source: str,
    existing_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Materialize top-level auditor return routes so the gate cannot ignore them."""
    if not isinstance(payload, dict):
        return []
    route = payload.get("completion_routing")
    if not isinstance(route, dict) or route.get("action") != "return_to_orchestrator":
        return []
    item_ids = route.get("open_item_ids")
    if not isinstance(item_ids, list) or not item_ids:
        item_ids = [f"{source}:completion-routing"]
    phase = route.get("next_phase")
    if phase not in {"replan", "iterate", "execute"}:
        phase = "iterate"
    additions = []
    by_id = {str(finding.get("id")): finding for finding in existing_findings}
    for item_id in item_ids:
        item_key = str(item_id)
        if item_key in by_id:
            existing = by_id[item_key]
            existing["intent_relation"] = "same_intent"
            existing["disposition"] = "open"
            existing["recommended_phase"] = phase
            existing.setdefault(
                "evidence",
                route.get("reason") or "auditor requested return to orchestrator",
            )
            continue
        additions.append({
            "id": item_key,
            "severity": "high",
            "intent_relation": "same_intent",
            "disposition": "open",
            "recommended_phase": phase,
            "evidence": route.get("reason") or "auditor requested return to orchestrator",
        })
    return additions


def evaluate_payloads(payloads: list[Any], sources: list[str] | None = None) -> dict[str, Any]:
    sources = sources or [f"payload-{idx + 1}" for idx in range(len(payloads))]
    normalized_findings: list[dict[str, Any]] = []
    counts = {severity: 0 for severity in NORMALIZED}

    for payload, source in zip(payloads, sources, strict=False):
        payload_findings = _extract_findings(payload, source)
        payload_findings.extend(
            _routing_findings(
                payload,
                source,
                payload_findings,
            )
        )
        for finding in payload_findings:
            severity = normalize_severity(finding.get("severity"))
            counts[severity] += 1
            intent_relation = _intent_relation(finding)
            disposition = _disposition(finding)
            closed = _closed_state(finding)
            proof = _proof_present(finding)
            terminal_proof = _terminal_proof_present(finding, disposition)
            intent_terminal = disposition in INTENT_TERMINAL_DISPOSITIONS and terminal_proof
            same_intent_open = intent_relation == "same_intent" and not intent_terminal
            if intent_relation == "same_intent":
                severity_open = severity in BLOCKING and not intent_terminal
            else:
                severity_open = severity in BLOCKING and not (closed and proof)
            open_blocking = same_intent_open or severity_open
            normalized_findings.append({
                "id": str(finding.get("id")),
                "source": source,
                "source_severity": finding.get("severity"),
                "normalized_severity": severity,
                "intent_relation": intent_relation,
                "disposition": disposition,
                "intent_terminal": intent_terminal,
                "blocking": open_blocking,
                "blocking_reasons": [
                    reason
                    for reason, applies in (
                        ("same_intent_without_evidenced_terminal_disposition", same_intent_open),
                        ("open_critical_or_high", severity_open),
                    )
                    if applies
                ],
                "closed": closed,
                "closure_proof_present": proof,
                "terminal_proof_present": terminal_proof,
                "recommended_phase": _recommended_phase(finding) if same_intent_open else None,
                "evidence": finding.get("evidence") or finding.get("snippet") or finding.get("observed"),
            })

    blocking = [finding for finding in normalized_findings if finding["blocking"]]
    intent_blocking = [
        finding
        for finding in blocking
        if "same_intent_without_evidenced_terminal_disposition" in finding["blocking_reasons"]
    ]
    phases = {finding["recommended_phase"] for finding in intent_blocking}
    if "replan" in phases:
        next_phase = "replan"
    elif "execute" in phases:
        next_phase = "execute"
    else:
        next_phase = "iterate"
    orchestrator_route = (
        {
            "action": "return_to_orchestrator",
            "next_phase": next_phase,
            "open_item_ids": [finding["id"] for finding in blocking],
            "reason": "blocking findings require closure before Review-G",
        }
        if blocking
        else {
            "action": "proceed",
            "next_phase": "report",
            "open_item_ids": [],
            "reason": "all blocking and same-intent findings are closed with evidence",
        }
    )
    return {
        "pass": not blocking,
        "blocking_count": len(blocking),
        "blocking_findings": blocking,
        "orchestrator_route": orchestrator_route,
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
