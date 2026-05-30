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
VALID_JUDGE_VERDICTS = {"approve", "rethink", "new_approach"}
VALID_JUDGE_SPEC_ALIGNMENT = {"aligned", "partial", "misaligned"}
VALID_BUDGET_MODES = {"default", "long", "custom"}


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
    if not isinstance(verdict, str) or verdict not in VALID_JUDGE_VERDICTS:
        raise ValueError(
            f"judge_decisions[{i}].verdict must be one of {sorted(VALID_JUDGE_VERDICTS)}, got {verdict!r}"
        )
    if "spec_alignment" in item:
        sa = item["spec_alignment"]
        if not isinstance(sa, str) or sa not in VALID_JUDGE_SPEC_ALIGNMENT:
            raise ValueError(
                f"judge_decisions[{i}].spec_alignment must be one of "
                f"{sorted(VALID_JUDGE_SPEC_ALIGNMENT)}, got {sa!r}"
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
