#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""self_review_efficiency.py — state.json + git-churn efficiency scan for self_review.py.

No LLM calls, no network, stdlib only.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

# Heuristic thresholds (duplicated from self_review.py constants for module isolation)
_CHURN_THRESHOLD = 5
_FAILURE_THRESHOLD = 2
_ITERATION_THRESHOLD = 3

# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git_churn_files(workdir: Path, since_date: str, errors: list[str]) -> Counter[str]:
    """Return Counter(file -> touches) for files changed since since_date."""
    counter: Counter[str] = Counter()
    try:
        out = subprocess.check_output(
            [
                "git", "-C", str(workdir),
                "log",
                f"--since={since_date}",
                "--name-only",
                "--pretty=format:",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        errors.append(f"git churn scan error: {exc}")
        return counter
    for line in out.splitlines():
        line = line.strip()
        if line:
            counter[line] += 1
    return counter


# ---------------------------------------------------------------------------
# State.json helpers — one signal per helper to keep each under threshold
# ---------------------------------------------------------------------------

def _signal_phase_failures(
    window_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Signal 1: phases that failed repeatedly across runs."""
    phase_failures: Counter[str] = Counter()
    for run in window_runs:
        phases = run.get("phases") or {}
        if not isinstance(phases, dict):
            continue
        for phase_name, phase_data in phases.items():
            if not isinstance(phase_data, dict):
                continue
            status = str(phase_data.get("status") or "").lower()
            if status in ("fail", "failed", "error"):
                phase_failures[phase_name] += 1

    findings: list[dict[str, Any]] = []
    for phase, count in phase_failures.most_common():
        if count < _FAILURE_THRESHOLD:
            continue
        findings.append({
            "kind": "phase_repeated_failure",
            "signal": f"Phase '{phase}' failed in {count}/{len(window_runs)} runs",
            "evidence": f"phase={phase} failure_count={count} window_runs={len(window_runs)}",
            "suggested_action": (
                f"Investigate recurring '{phase}' failures: review phase criteria, "
                "tooling, or common root cause patterns"
            ),
            "severity": "HIGH" if count >= _FAILURE_THRESHOLD * 2 else "MEDIUM",
        })
    return findings


def _criterion_label(criterion: Any) -> str:
    """Extract a string label from a criterion entry (str or dict)."""
    if isinstance(criterion, str):
        return criterion
    if isinstance(criterion, dict):
        return str(criterion.get("name") or criterion.get("label") or "")
    return ""


def _labels_from_phase(phase_data: Any) -> list[str]:
    """Return criterion labels from a single phase dict."""
    if not isinstance(phase_data, dict):
        return []
    criteria = phase_data.get("failed_criteria") or phase_data.get("criteria") or []
    if not isinstance(criteria, list):
        return []
    return [lb for c in criteria if (lb := _criterion_label(c))]


def _labels_from_run(run: dict[str, Any]) -> list[str]:
    """Return all criterion failure labels across all phases of a run."""
    phases = run.get("phases") or {}
    if not isinstance(phases, dict):
        return []
    labels: list[str] = []
    for phase_data in phases.values():
        labels.extend(_labels_from_phase(phase_data))
    return labels


def _count_criteria_failures(window_runs: list[dict[str, Any]]) -> Counter[str]:
    """Walk all runs and count how often each criterion label appears as a failure."""
    counts: Counter[str] = Counter()
    for run in window_runs:
        for label in _labels_from_run(run):
            counts[label] += 1
    return counts


def _signal_criteria_failures(
    window_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Signal 2: criteria that recurred as failures."""
    counts = _count_criteria_failures(window_runs)
    findings: list[dict[str, Any]] = []
    for crit, count in counts.most_common(5):
        if count < _FAILURE_THRESHOLD:
            continue
        findings.append({
            "kind": "criterion_recurring_failure",
            "signal": f"Criterion '{crit}' failed {count} times in window",
            "evidence": f"criterion={crit!r} failure_count={count}",
            "suggested_action": (
                f"Add or strengthen automated check for criterion: {crit!r}"
            ),
            "severity": "HIGH" if count >= _FAILURE_THRESHOLD * 2 else "MEDIUM",
        })
    return findings


def _signal_high_iteration_runs(
    window_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Signal 3: long-running or repeatedly-iterating runs."""
    findings: list[dict[str, Any]] = []
    for run in window_runs:
        run_id = run.get("run_id", "(unknown)")
        execution = run.get("execution") or {}
        if isinstance(execution, dict):
            iterate_count = int(execution.get("iterate_attempt") or 0)
        else:
            iterate_count = int(run.get("iterations") or 0)
        if iterate_count < _ITERATION_THRESHOLD:
            continue
        findings.append({
            "kind": "high_iteration_run",
            "signal": f"Run {run_id} iterated {iterate_count} times",
            "evidence": f"run_id={run_id} iterate_attempt={iterate_count}",
            "suggested_action": (
                "Review this run's manual interventions and failed criteria; "
                "high iteration count signals unclear criteria or test gaps"
            ),
            "severity": "MEDIUM",
        })
    return findings


def _signal_escalations(
    window_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Signal 4: escalations in runs."""
    escalation_count = sum(
        len(run.get("escalations") or [])
        for run in window_runs
        if isinstance(run.get("escalations"), list)
    )
    if escalation_count == 0:
        return []
    return [{
        "kind": "escalations_observed",
        "signal": f"{escalation_count} escalation(s) recorded in window",
        "evidence": f"total_escalations={escalation_count} window_runs={len(window_runs)}",
        "suggested_action": (
            "Review escalation contexts: ambiguous scope or missing decision rules "
            "are the most common root causes"
        ),
        "severity": "LOW",
    }]


def _filter_window_runs(
    runs: list[dict[str, Any]],
    cutoff: dt.datetime,
) -> list[dict[str, Any]]:
    """Filter runs list to those within the time window."""
    window: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        date_str = run.get("date") or run.get("created_at") or ""
        if date_str:
            try:
                parsed = dt.datetime.fromisoformat(
                    date_str.replace("Z", "+00:00") if date_str.endswith("Z") else date_str
                )
                if parsed < cutoff:
                    continue
            except (ValueError, TypeError):
                pass  # include if date unparseable
        window.append(run)
    return window


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_state(
    workdir: Path,
    window_days: int,
    errors: list[str],
) -> list[dict[str, Any]]:
    """Read .build-loop/state.json runs[] and produce ranked efficiency_findings[]."""
    state_path = workdir / ".build-loop" / "state.json"
    if not state_path.exists():
        return []

    try:
        state = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"state.json parse error: {exc}")
        return []

    if not isinstance(state, dict):
        errors.append("state.json is not a JSON object")
        return []

    runs: list[dict[str, Any]] = state.get("runs") or []
    if not isinstance(runs, list):
        errors.append("state.json.runs is not a list")
        return []

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)
    window_runs = _filter_window_runs(runs, cutoff)
    if not window_runs:
        return []

    findings: list[dict[str, Any]] = []
    findings.extend(_signal_phase_failures(window_runs))
    findings.extend(_signal_criteria_failures(window_runs))
    findings.extend(_signal_high_iteration_runs(window_runs))
    findings.extend(_signal_escalations(window_runs))
    return findings


def scan_churn(
    workdir: Path,
    window_days: int,
    errors: list[str],
) -> list[dict[str, Any]]:
    """Scan git log for high-churn files."""
    findings: list[dict[str, Any]] = []
    since_date = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)
    ).strftime("%Y-%m-%d")
    churn = _git_churn_files(workdir, since_date, errors)
    for filepath, count in churn.most_common(5):
        if count < _CHURN_THRESHOLD:
            continue
        findings.append({
            "kind": "high_churn_file",
            "signal": f"'{filepath}' changed {count} times in {window_days}d",
            "evidence": f"file={filepath!r} git_touches={count}",
            "suggested_action": (
                f"Consider splitting or stabilising high-churn file: {filepath!r}. "
                "Frequent edits often indicate unclear scope or missing tests."
            ),
            "severity": "LOW",
        })
    return findings
