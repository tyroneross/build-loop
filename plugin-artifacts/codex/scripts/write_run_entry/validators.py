#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""validators.py — input validation and JSON-loader helpers for write_run_entry."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "run_id": str,
    "date": str,
    "goal": str,
    "outcome": str,
    "phases": dict,
    "filesTouched": list,
    "diagnosticCommands": list,
    "manualInterventions": list,
    "active_experimental_artifacts": list,
}
VALID_OUTCOMES = {"pass", "fail", "partial"}
VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
# Promotion-reviewer-style verdicts plus the independent-auditor's own vocabulary
# (yay/nay/suggest/look-again — see scripts/audit_record_verdict.py). The two judge
# families write into the same judge_decisions[] list, so both must validate.
VALID_JUDGE_VERDICTS = {"approve", "rethink", "new_approach"}
VALID_AUDITOR_VERDICTS = {
    "yay", "nay", "suggest", "suggest_correction", "look-again", "look_again",
}
ALL_JUDGE_VERDICTS = VALID_JUDGE_VERDICTS | VALID_AUDITOR_VERDICTS
# judge_id substring that identifies the independent commit auditor (covers both the
# dispatched "independent-auditor" agent and the "independent-auditor-hook" record).
AUDITOR_JUDGE_MARKER = "independent-auditor"
VALID_JUDGE_SPEC_ALIGNMENT = {"aligned", "partial", "misaligned", "unverifiable"}
VALID_BUDGET_MODES = {"default", "long", "custom"}
# Advisory oracle-completeness note on a verify verdict: how much of the checked
# surface the oracle actually covered. A "green" gate with a thin oracle is a known
# false-confidence source (arXiv:2606.09863 false-success; the oracle's completeness
# is the reliability ceiling). Additive + optional — never required.
VALID_ORACLE_COVERAGE = {"full", "partial", "thin"}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry validation
# ---------------------------------------------------------------------------

def validate_entry(entry: dict) -> None:
    for field, expected in REQUIRED_FIELDS.items():
        if field not in entry:
            raise ValueError(f"missing required field: {field}")
        if not isinstance(entry[field], expected):
            raise ValueError(
                f"field {field!r} must be "
                f"{expected.__name__ if isinstance(expected, type) else expected}, "
                f"got {type(entry[field]).__name__}"
            )
    if entry["outcome"] not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(VALID_OUTCOMES)}, got {entry['outcome']!r}")


def auditor_present(judge_decisions: object) -> bool:
    """True when judge_decisions[] carries a real independent-auditor verdict.

    Matches both the dispatched `independent-auditor` agent and the
    `independent-auditor-hook` record. An empty list, None, or a list of only
    other judges (an inline self-audit substituting for a real dispatch) is False.
    """
    if not isinstance(judge_decisions, list):
        return False
    for item in judge_decisions:
        if isinstance(item, dict) and AUDITOR_JUDGE_MARKER in str(item.get("judge_id", "")):
            return True
    return False


def review_completeness_error(entry: dict, scope: str) -> str | None:
    """Return an error message when a build-scope code-touching pass lacks the auditor.

    The gap this closes (bl-enforce-independent-auditor-dispatch): an orchestrator
    can substitute inline self-reasoning for a real `independent-auditor` dispatch,
    and nothing fails — an empty/inline-only auditor record reaches Report-G on
    shipped code. Rule: a `pass` outcome on a `scope=build` run that touched files
    MUST carry a real independent-auditor verdict. Returns None when satisfied or
    when the rule does not apply (non-build scope, no files, non-pass outcome).
    """
    if scope != "build":
        return None
    if entry.get("outcome") != "pass":
        return None
    if not entry.get("filesTouched"):
        return None
    if not auditor_present(entry.get("judge_decisions")):
        return (
            "review incomplete: a build-scope run that touched code cannot record "
            "outcome=pass without a real independent-auditor verdict in "
            "judge_decisions[] (inline self-audit is not a substitute). Re-dispatch "
            "the independent-auditor at build scope before Report. "
            "See bl-enforce-independent-auditor-dispatch."
        )
    return None


# ---------------------------------------------------------------------------
# JSON source reader (shared by all loaders)
# ---------------------------------------------------------------------------

def _read_source(source: str) -> str | None:
    """Return raw text from path or stdin '-'. Returns None when path missing/empty."""
    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _parse_json(raw: str, flag: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{flag} is not valid JSON: {e}") from e


# ---------------------------------------------------------------------------
# Security findings
# ---------------------------------------------------------------------------

def _validate_finding(i: int, item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"security_findings[{i}] must be an object, got {type(item).__name__}")
    if "mapped_risks" not in item or not isinstance(item["mapped_risks"], list):
        raise ValueError(f"security_findings[{i}].mapped_risks must be a list of strings")
    if not all(isinstance(r, str) for r in item["mapped_risks"]):
        raise ValueError(f"security_findings[{i}].mapped_risks must contain only strings")
    sev = item.get("severity")
    if not isinstance(sev, str) or sev not in VALID_SEVERITIES:
        raise ValueError(
            f"security_findings[{i}].severity must be one of {sorted(VALID_SEVERITIES)}, got {sev!r}"
        )


def load_security_findings(source: str) -> list[dict] | None:
    """Read findings JSON from a path or stdin ('-').

    Returns None when the file is missing or empty (caller should not write a 'security_findings'
    key — semantically equivalent to omitting the flag). Returns a list (possibly empty if the
    user explicitly passed `[]`) when the file decoded to a list shape.

    Validates that the decoded value is a list of objects, each with a 'mapped_risks' list of
    strings and a 'severity' string in VALID_SEVERITIES. Other fields pass through unchanged.
    Raises ValueError on malformed input.
    """
    raw = _read_source(source)
    if raw is None:
        log(f"note: --security-findings-json path {source} does not exist; treating as no findings (no security_findings key will be written)")
        return None
    if not raw.strip():
        return None
    data = _parse_json(raw, "--security-findings-json")
    # Accept either a bare list or the reviewer's full envelope ({"findings": [...], ...}).
    if isinstance(data, dict) and "findings" in data:
        data = data["findings"]
    if not isinstance(data, list):
        raise ValueError("--security-findings-json must decode to a list (or an object with a 'findings' list)")
    for i, item in enumerate(data):
        _validate_finding(i, item)
    return data


# ---------------------------------------------------------------------------
# Budget summary
# ---------------------------------------------------------------------------

def _validate_budget_int_fields(data: dict) -> None:
    for field in ("budget_seconds", "used_seconds", "items_closed", "items_deferred", "commits", "pushes"):
        if field not in data:
            raise ValueError(f"budget_summary missing required field: {field}")
        if not isinstance(data[field], int) or isinstance(data[field], bool):
            raise ValueError(f"budget_summary.{field} must be int, got {type(data[field]).__name__}")
        if data[field] < 0:
            raise ValueError(f"budget_summary.{field} must be >= 0, got {data[field]}")


def load_budget_summary(source: str) -> dict | None:
    """Read autonomous-mode budget_summary JSON from a path or stdin ('-').

    Shape per plan §14.4 + §14.5: a single object capturing the run's wall-clock
    + queue-drain summary. All fields required (validate hard) so downstream
    pattern mining doesn't have to defend against partial shapes.

    Required fields:
      mode             one of VALID_BUDGET_MODES (default | long | custom)
      budget_seconds   int >= 0
      used_seconds     int >= 0
      items_closed     int >= 0    # queue items routed through Phase 2→3→4 to completion
      items_deferred   int >= 0    # queue items moved to .build-loop/followup/
      commits          int >= 0
      pushes           int >= 0

    Returns None when source path missing/empty (caller skips the key).
    Raises ValueError on type/shape errors.
    """
    raw = _read_source(source)
    if raw is None:
        log(f"note: --budget-summary-json path {source} does not exist; skipping")
        return None
    if not raw.strip():
        return None
    data = _parse_json(raw, "--budget-summary-json")
    if not isinstance(data, dict):
        raise ValueError("--budget-summary-json must decode to an object")
    mode = data.get("mode")
    if not isinstance(mode, str) or mode not in VALID_BUDGET_MODES:
        raise ValueError(f"budget_summary.mode must be one of {sorted(VALID_BUDGET_MODES)}, got {mode!r}")
    _validate_budget_int_fields(data)
    return data


# ---------------------------------------------------------------------------
# Judge decisions
# ---------------------------------------------------------------------------

def _validate_judge_decision(i: int, item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"judge_decisions[{i}] must be an object, got {type(item).__name__}")
    if not isinstance(item.get("judge_id"), str):
        raise ValueError(f"judge_decisions[{i}].judge_id must be a string")
    verdict = item.get("verdict")
    if not isinstance(verdict, str) or verdict not in ALL_JUDGE_VERDICTS:
        raise ValueError(
            f"judge_decisions[{i}].verdict must be one of {sorted(ALL_JUDGE_VERDICTS)}, got {verdict!r}"
        )
    if "spec_alignment" in item:
        sa = item["spec_alignment"]
        if not isinstance(sa, str) or sa not in VALID_JUDGE_SPEC_ALIGNMENT:
            raise ValueError(
                f"judge_decisions[{i}].spec_alignment must be one of "
                f"{sorted(VALID_JUDGE_SPEC_ALIGNMENT)}, got {sa!r}"
            )
    if "oracle_completeness" in item:
        _validate_oracle_completeness(f"judge_decisions[{i}]", item["oracle_completeness"])


def _validate_oracle_completeness(where: str, oc: Any) -> None:
    """Validate the optional advisory oracle_completeness note on a verify verdict.

    Shape: {"covered": str?, "uncovered": str?, "coverage": "full|partial|thin"?}.
    Every field is optional (a partially-filled note is still useful); only present
    fields are type-checked. Advisory — its purpose is to record WHAT the check
    actually covered so a thin oracle behind a green gate is visible, not to gate.
    """
    if not isinstance(oc, dict):
        raise ValueError(f"{where}.oracle_completeness must be an object, got {type(oc).__name__}")
    for key in ("covered", "uncovered"):
        if key in oc and not isinstance(oc[key], str):
            raise ValueError(f"{where}.oracle_completeness.{key} must be a string")
    if "coverage" in oc:
        cov = oc["coverage"]
        if not isinstance(cov, str) or cov not in VALID_ORACLE_COVERAGE:
            raise ValueError(
                f"{where}.oracle_completeness.coverage must be one of "
                f"{sorted(VALID_ORACLE_COVERAGE)}, got {cov!r}"
            )


def load_judge_decisions(source: str) -> list[dict] | None:
    """Read advisory judge_decisions JSON from a path or stdin ('-').

    Shape per plan §12.5: advisory verdicts that never block execution. Each entry must have
    `judge_id` (str) and `verdict` (one of VALID_JUDGE_VERDICTS). Optional pass-through fields:
    checkpoint_id, confidence, spec_alignment, variances, meta_guidance, policy_refs,
    implementer_response, outcome.

    Returns None when missing/empty (caller skips the key). Returns a list otherwise.
    """
    raw = _read_source(source)
    if raw is None:
        log(f"note: --judge-decisions-json path {source} does not exist; skipping")
        return None
    if not raw.strip():
        return None
    data = _parse_json(raw, "--judge-decisions-json")
    if isinstance(data, dict) and "decisions" in data:
        data = data["decisions"]
    if not isinstance(data, list):
        raise ValueError("--judge-decisions-json must decode to a list (or an object with a 'decisions' list)")
    for i, item in enumerate(data):
        _validate_judge_decision(i, item)
    return data


# ---------------------------------------------------------------------------
# Model + harness config (report at the model+harness level, not model alone)
# ---------------------------------------------------------------------------

def load_config_object(source: str, flag: str) -> dict | None:
    """Read an optional free-form config object (models / harness) from a path or stdin ('-').

    Additive + optional. Reporting a run at the model+harness level — the scaffold,
    tool-set, and context-budget config, not the model id alone — is needed because
    undisclosed harness config confounds model-vs-model comparisons (arXiv:2605.23950)
    and the harness is a first-class reliability lever. The `models` and `harness`
    blocks are pass-through objects (shape owned by the caller / Phase-4 report writer);
    validated only as "must decode to an object" so downstream readers see a dict, never
    a scalar. Returns None when the source is missing/empty (caller skips the key).
    """
    raw = _read_source(source)
    if raw is None:
        log(f"note: {flag} path {source} does not exist; skipping")
        return None
    if not raw.strip():
        return None
    data = _parse_json(raw, flag)
    if not isinstance(data, dict):
        raise ValueError(f"{flag} must decode to an object")
    return data
