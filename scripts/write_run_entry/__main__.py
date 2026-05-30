#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""write_run_entry/__main__.py — CLI entry for the deterministic Review-F writer.

Appends a run entry to `.build-loop/state.json.runs[]` and, for each active
experimental artifact, appends an `applied` row to
`.build-loop/experiments/<name>.jsonl` with correct confound tracking.

Contract:
  stdout      -> run_id on success, nothing else
  stderr      -> human-readable log lines
  exit 0      -> success
  exit 1      -> validation error (bad args, missing required field, wrong type)
  exit 2      -> filesystem error (permission denied, disk full, lock timeout)

Atomicity: fcntl.flock(LOCK_EX) on a sidecar .lock file + tmpfile + os.replace.
Additive migration: existing non-runs[] top-level keys are never touched.
Zero dependencies. Python 3.11+.

Canonical invocation:
  python3 scripts/write_run_entry/__main__.py --workdir <dir> --goal <goal> --outcome <pass|fail|partial>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# When run directly (`python3 scripts/write_run_entry/__main__.py`), sys.path[0]
# is the package directory, so flat sibling imports work.  When imported via
# `python3 -m write_run_entry` with scripts/ on sys.path, __init__.py has already
# inserted the package dir.  Either way, also ensure the scripts/ parent is
# reachable so atomic_io is importable.
_PKG_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _PKG_DIR.parent
for _d in (str(_PKG_DIR), str(_SCRIPTS_DIR)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from iohelpers import (  # type: ignore  # noqa: E402
    CorruptStateError,
    append_experiment_rows,
    append_run_entry,
    log,
    read_json,
)
from idtime import compute_run_id, iso_utc  # type: ignore  # noqa: E402
from validators import (  # type: ignore  # noqa: E402
    VALID_OUTCOMES,
    load_budget_summary,
    load_judge_decisions,
    load_security_findings,
    validate_entry,
)

VALID_OUTCOMES = VALID_OUTCOMES  # re-exported for help text


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deterministic Review-F writer for build-loop.")
    p.add_argument("--workdir", required=True, help="Project root containing .build-loop/")
    p.add_argument("--goal", required=True, help="Short goal text for this build")
    p.add_argument("--outcome", required=True, choices=sorted(VALID_OUTCOMES))
    p.add_argument("--phases-json", default="{}", help="Per-phase status dict as JSON string")
    p.add_argument("--files-touched", default="", help="Comma-separated list of files touched")
    p.add_argument(
        "--files-touched-from-git",
        action="store_true",
        help="Derive from git diff <preBuildSha>..HEAD (preBuildSha read from state.json)",
    )
    p.add_argument(
        "--diagnostic-commands",
        default="",
        help="Newline-separated commands run during build",
    )
    p.add_argument(
        "--manual-interventions-json",
        default="[]",
        help="JSON list of {phase, note} objects",
    )
    p.add_argument(
        "--active-experimental-artifacts",
        default="",
        help="Comma-separated experimental artifact names that triggered this run",
    )
    p.add_argument("--run-id", default=None, help="Override run_id (default: compute from goal + now)")
    p.add_argument(
        "--security-findings-json",
        default=None,
        help=(
            "Path to a JSON file containing a list of security-reviewer findings (or '-' for stdin). "
            "Each element must be an object with at minimum 'mapped_risks' (list of strings) and "
            "'severity' (CRITICAL|HIGH|MEDIUM|LOW). Other fields (id, title, evidence, snippet, "
            "recommendation) pass through. When omitted, no 'security_findings' key is written."
        ),
    )
    p.add_argument(
        "--judge-decisions-json",
        default=None,
        help=(
            "Path to a JSON file containing a list of advisory judge_decisions (or '-' for stdin). "
            "Each element must have 'judge_id' (str) and 'verdict' (approve|rethink|new_approach). "
            "Optional fields: checkpoint_id, confidence, spec_alignment, variances, meta_guidance, "
            "policy_refs, implementer_response, outcome. Judges are advisory — verdicts never block "
            "execution. Used by self-improvement-architect for prompt/rubric tuning."
        ),
    )
    p.add_argument(
        "--budget-summary-json",
        default=None,
        help=(
            "Path to a JSON file containing the autonomous-mode budget summary (or '-' for stdin). "
            "Shape: {mode: default|long|custom, budget_seconds: int, used_seconds: int, "
            "items_closed: int, items_deferred: int, commits: int, pushes: int}. Mirrors the "
            "--judge-decisions-json pattern. Omit for non-autonomous runs (default-mode 5-phase "
            "loop). Captured per plan §14.4 + §14.5 for telemetry / Phase 6 Learn pattern mining."
        ),
    )
    return p.parse_args(argv)


def _split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def files_touched_from_git(workdir: Path, pre_sha: str) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(workdir), "diff", "--name-only", f"{pre_sha}..HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _load_optional_payloads(args: argparse.Namespace) -> tuple[dict, list, object, object, object]:
    """Parse phases, manual_interventions, and optional security/judge/budget JSON sources."""
    phases = json.loads(args.phases_json)
    if not isinstance(phases, dict):
        raise ValueError("--phases-json must decode to an object")
    manual_interventions = json.loads(args.manual_interventions_json)
    if not isinstance(manual_interventions, list):
        raise ValueError("--manual-interventions-json must decode to a list")
    security_findings = load_security_findings(args.security_findings_json) if args.security_findings_json else None
    judge_decisions = load_judge_decisions(args.judge_decisions_json) if args.judge_decisions_json else None
    budget_summary = load_budget_summary(args.budget_summary_json) if args.budget_summary_json else None
    return phases, manual_interventions, security_findings, judge_decisions, budget_summary


def _resolve_files_touched(args: argparse.Namespace, state_path: Path, workdir: Path) -> list[str]:
    """Combine --files-touched CSV with optional git-diff expansion."""
    files_touched = _split_csv(args.files_touched)
    if not args.files_touched_from_git:
        return files_touched
    state_existing = read_json(state_path) if state_path.exists() else {}
    pre_sha = state_existing.get("preBuildSha") if isinstance(state_existing, dict) else None
    if pre_sha:
        files_touched.extend(f for f in files_touched_from_git(workdir, pre_sha) if f not in files_touched)
    else:
        log("warn: --files-touched-from-git set but state.json has no preBuildSha; skipping git diff")
    return files_touched


def _build_entry(
    args: argparse.Namespace,
    run_id: str,
    date: str,
    phases: dict,
    files_touched: list[str],
    manual_interventions: list,
    active: list[str],
    diagnostic_commands: list[str],
    security_findings: object,
    judge_decisions: object,
    budget_summary: object,
) -> dict:
    """Assemble the run entry dict from validated inputs."""
    entry: dict = {
        "run_id": run_id,
        "date": date,
        "goal": args.goal,
        "outcome": args.outcome,
        "phases": phases,
        "diagnosticCommands": diagnostic_commands,
        "filesTouched": files_touched,
        "manualInterventions": manual_interventions,
        "active_experimental_artifacts": active,
    }
    if security_findings is not None:
        entry["security_findings"] = security_findings
    if judge_decisions is not None:
        entry["judge_decisions"] = judge_decisions
    if budget_summary is not None:
        entry["budget_summary"] = budget_summary
    return entry


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return 1 if e.code else 0

    workdir = Path(args.workdir).resolve()
    state_path = workdir / ".build-loop" / "state.json"
    experiments_dir = workdir / ".build-loop" / "experiments"

    try:
        phases, manual_interventions, security_findings, judge_decisions, budget_summary = (
            _load_optional_payloads(args)
        )
    except (json.JSONDecodeError, ValueError) as e:
        log(f"validation error: {e}")
        return 1

    files_touched = _resolve_files_touched(args, state_path, workdir)
    active = _split_csv(args.active_experimental_artifacts)
    diagnostic_commands = [c for c in args.diagnostic_commands.splitlines() if c.strip()]
    run_id = args.run_id or compute_run_id(args.goal)
    date = iso_utc()

    entry = _build_entry(
        args, run_id, date, phases, files_touched, manual_interventions, active,
        diagnostic_commands, security_findings, judge_decisions, budget_summary,
    )

    try:
        validate_entry(entry)
    except ValueError as e:
        log(f"validation error: {e}")
        return 1

    try:
        append_run_entry(state_path, entry)
        log(f"appended run entry to {state_path} (run_id={run_id})")
        if active:
            append_experiment_rows(experiments_dir, run_id, active, args.outcome, date)
    except CorruptStateError as e:
        log(f"validation error: {e}")
        return 1
    except TimeoutError as e:
        log(f"filesystem error: {e}")
        return 2
    except OSError as e:
        log(f"filesystem error: {e}")
        return 2

    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
