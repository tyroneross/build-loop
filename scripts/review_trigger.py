#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Deterministic quality-gate trigger profiler for build-loop Review.

The script is intentionally host-neutral: it reads JSON context from a file,
optional changed-file paths from the CLI, and emits a compact JSON profile that
Claude/Codex/Gemini adapters can consume without calling an LLM.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PROFILE_VERSION = "1.0"
UNKNOWN_VALUES = {"unknown", "uncertain", "ambiguous", "tbd", "unverified"}
TRUE_VALUES = {"1", "true", "yes", "y", "on", "required", "high", "critical"}
FALSE_VALUES = {"0", "false", "no", "n", "off", "none", "low", "minor", "trivial"}

HIGH_RISK_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("risk_surface_change", ("riskSurfaceChange", "risk_surface_change", "risk")),
    ("architecture_boundary", ("architectureBoundaryCrossed", "architecture_boundary_crossed", "arch_boundary")),
    ("reviewability_budget_breach", ("reviewabilityBudgetBreached", "reviewability_budget_breached")),
    ("new_dependency", ("newDependency", "new_dependency", "dependency_delta")),
    ("runtime_integration", ("newRuntimeIntegration", "runtime_integration", "runtimeServer")),
    ("auth_change", ("auth_change", "authentication_change", "auth")),
    ("file_boundary_change", ("file_change", "filesystem_change", "file_boundary_change")),
    ("network_change", ("network_change", "http_change", "api_change")),
    ("persistence_change", ("persistence_change", "storage_change", "database_change")),
    ("security_change", ("security_change", "security")),
    ("model_tool_change", ("model_tool_change", "llm_change", "mcp_change", "tool_change")),
    ("new_approach", ("newApproach", "new_approach")),
)

PLAN_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("non_trivial", ("nonTrivial", "non_trivial")),
)

FILE_REASON_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("new_dependency", ("package.json", "package-lock.json", "pyproject.toml", "requirements", "cargo.toml", "go.mod")),
    ("auth_change", ("auth", "login", "session", "oauth", "permission")),
    ("security_change", ("security", "secret", "crypto", "permission")),
    ("network_change", ("api", "http", "fetch", "request", "route", "server")),
    ("persistence_change", ("db", "database", "storage", "persist", "schema", "migration")),
    ("model_tool_change", ("llm", "model", "agent", "tool", "mcp")),
    ("runtime_integration", ("runtime", "server", "worker", "daemon")),
)

COMPREHENSION_REASONS = {
    "architecture_boundary",
    "reviewability_budget_breach",
    "large_diff",
    "high_complexity",
}


def _norm_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _flatten(obj: Any, out: dict[str, Any] | None = None) -> dict[str, Any]:
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str):
                out.setdefault(_norm_key(key), value)
            if isinstance(value, dict):
                _flatten(value, out)
    return out


def _truth_state(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if value is None:
        return False
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in UNKNOWN_VALUES:
            return None
        if lowered in TRUE_VALUES:
            return True
        if lowered in FALSE_VALUES:
            return False
    return bool(value)


def _signal_state(flat: dict[str, Any], aliases: Iterable[str]) -> bool | None:
    for alias in aliases:
        key = _norm_key(alias)
        if key in flat:
            return _truth_state(flat[key])
    return False


def _changed_files_from_context(context: dict[str, Any]) -> list[str]:
    raw = context.get("changed_files") or context.get("changedFiles") or context.get("files") or []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def _numeric(flat: dict[str, Any], aliases: Iterable[str]) -> float | None:
    for alias in aliases:
        key = _norm_key(alias)
        if key not in flat:
            continue
        try:
            return float(flat[key])
        except (TypeError, ValueError):
            return None
    return None


def _path_reasons(changed_files: list[str]) -> list[str]:
    reasons: list[str] = []
    lowered = [path.lower() for path in changed_files]
    for reason, needles in FILE_REASON_PATTERNS:
        if any(needle in path for path in lowered for needle in needles):
            reasons.append(reason)
    return reasons


def _reasons_from_signals(
    flat: dict[str, Any],
    signals: Iterable[tuple[str, tuple[str, ...]]],
    *,
    include_ambiguous: bool = False,
) -> list[str]:
    reasons: list[str] = []
    for reason, aliases in signals:
        state = _signal_state(flat, aliases)
        if state is True:
            reasons.append(reason)
        elif include_ambiguous and state is None:
            reasons.append(f"ambiguous_{reason}")
    return reasons


def _plan_reasons(flat: dict[str, Any], file_count: int) -> list[str]:
    reasons = _reasons_from_signals(flat, PLAN_SIGNALS)
    trivial_aliases = ("trivial", "is_trivial")
    trivial_is_present = any(_norm_key(alias) in flat for alias in trivial_aliases)
    if trivial_is_present and _signal_state(flat, trivial_aliases) is False:
        reasons.append("non_trivial")
    if file_count > 1:
        reasons.append("multi_file")
    return reasons


def _metric_reasons(flat: dict[str, Any]) -> tuple[list[str], list[str]]:
    high_risk_reasons: list[str] = []
    plan_reasons: list[str] = []

    confidence = _numeric(flat, ("criticConfidence", "critic_confidence", "confidence"))
    if confidence is not None and confidence < 0.65:
        high_risk_reasons.append("low_confidence")

    loc_delta = _numeric(flat, ("loc_delta", "lines_changed", "changed_lines"))
    if loc_delta is not None and abs(loc_delta) >= 20:
        plan_reasons.append("loc_delta")
    if loc_delta is not None and abs(loc_delta) >= 200:
        high_risk_reasons.append("large_diff")

    return high_risk_reasons, plan_reasons


def build_profile(context: dict[str, Any], changed_files: list[str] | None = None) -> dict[str, Any]:
    files = [*(changed_files or []), *_changed_files_from_context(context)]
    flat = _flatten(context)
    metric_high_risk, metric_plan = _metric_reasons(flat)
    high_risk_reasons = [
        *_reasons_from_signals(flat, HIGH_RISK_SIGNALS, include_ambiguous=True),
        *metric_high_risk,
        *_path_reasons(files),
    ]
    plan_reasons = [
        *_plan_reasons(flat, len(files)),
        *metric_plan,
    ]

    reasons = sorted(set(high_risk_reasons + plan_reasons))
    high_risk = bool(high_risk_reasons)
    comprehension = any(
        reason in COMPREHENSION_REASONS or reason.removeprefix("ambiguous_") in COMPREHENSION_REASONS
        for reason in high_risk_reasons
    )

    return {
        "profile_version": PROFILE_VERSION,
        "plan_failure_modes_required": bool(plan_reasons or high_risk),
        "independent_review_required": high_risk,
        "cross_vendor_required": high_risk,
        "comprehension_artifact_required": comprehension,
        "reasons": reasons,
        "changed_files": sorted(set(files)),
    }


def _load_context(path: str | None) -> dict[str, Any]:
    if path is None:
        default = Path(".build-loop/state.json")
        if default.exists():
            path = str(default)
        else:
            return {}
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"failed to read context JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SystemExit("context JSON must be an object")
    return loaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit build-loop quality gate trigger profile")
    parser.add_argument("--context", help="JSON context file; defaults to .build-loop/state.json when present")
    parser.add_argument("--changed-file", action="append", default=[], help="Changed file path; repeatable")
    parser.add_argument("--json", action="store_true", help="emit JSON (default)")
    args = parser.parse_args(argv)

    context = _load_context(args.context)
    print(json.dumps(build_profile(context, args.changed_file), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
