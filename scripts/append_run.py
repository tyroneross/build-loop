#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Append a Learn-visible run record to `.build-loop/state.json.runs[]`.

The Phase 6 recurring-pattern-detector scans `state.json.runs[]` for pain
signals (phase failures, manual interventions, security findings). That array
is normally written only by the orchestrator's Review-G, so an INLINE build-loop
run (skill-as-methodology on the host loop, never dispatching the orchestrator)
records nothing and is invisible to Learn. This script lets any run-close path —
inline or the closeout — append the same-shaped record so Learn can see it.

Append-only and idempotent on `run_id`: re-appending the same run_id replaces
that record in place (no duplicate). All other `state.json` keys are preserved.

Usage:
  append_run.py --workdir W --run-id ID --goal "..." --outcome done
                [--commit SHA] [--files-touched a.py,b.sh]
                [--manual-intervention "phase:note" ...]
                [--phase "1:pass" --phase "4:fail" ...]
                [--extra-json '{"security_findings": [...]}'] [--json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_head(workdir: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def build_record(args: argparse.Namespace, workdir: Path) -> dict:
    # Canonical run-record shape the detector scans (matches Review-G output).
    record: dict = {
        "run_id": args.run_id,
        "date": _utc_date(),
        "goal": args.goal or "",
        "outcome": args.outcome,
        "host": args.host,
        "commit": args.commit or _git_head(workdir),
        "phases": [],
        "manualInterventions": [],
        "diagnosticCommands": [],
        "filesTouched": [],
        "judge_decisions": [],
        "security_findings": [],
        "active_experimental_artifacts": [],
        "source": "append_run",  # marks inline-recorded runs (vs orchestrator)
    }
    if args.files_touched:
        record["filesTouched"] = [f.strip() for f in args.files_touched.split(",") if f.strip()]
    for mi in args.manual_intervention or []:
        phase, _, note = mi.partition(":")
        record["manualInterventions"].append({"phase": phase.strip(), "note": note.strip()})
    for ph in args.phase or []:
        pid, _, status = ph.partition(":")
        record["phases"].append({"phase": pid.strip(), "status": (status.strip() or "pass")})
    if args.extra_json:
        try:
            extra = json.loads(args.extra_json)
            if isinstance(extra, dict):
                record.update(extra)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--extra-json is not valid JSON: {exc}")
    return record


def append_run(state_path: Path, record: dict) -> dict:
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text() or "{}")
        except json.JSONDecodeError:
            state = {}
    else:
        state = {}
    if not isinstance(state, dict):
        state = {}
    runs = state.get("runs")
    if not isinstance(runs, list):
        runs = []
    # Idempotent on run_id: replace in place rather than duplicate.
    replaced = False
    for i, r in enumerate(runs):
        if isinstance(r, dict) and r.get("run_id") == record["run_id"]:
            runs[i] = record
            replaced = True
            break
    if not replaced:
        runs.append(record)
    state["runs"] = runs
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    return {
        "run_id": record["run_id"],
        "action": "replaced" if replaced else "appended",
        "runs_count": len(runs),
        "path": str(state_path),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Append a Learn-visible run to state.json.runs[]")
    p.add_argument("--workdir", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--goal", default="")
    p.add_argument("--outcome", default="done", choices=["done", "partial", "blocked"])
    p.add_argument("--host", default="claude_code", choices=["claude_code", "codex", "gemini", "other"])
    p.add_argument("--commit", default="")
    p.add_argument("--files-touched", default="")
    p.add_argument("--manual-intervention", action="append", help='"<phase>:<note>" (repeatable)')
    p.add_argument("--phase", action="append", help='"<phase-id>:<status>" (repeatable)')
    p.add_argument("--extra-json", default="", help="JSON object merged into the record")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    workdir = Path(args.workdir).expanduser().resolve()
    state_path = workdir / ".build-loop" / "state.json"
    record = build_record(args, workdir)
    result = append_run(state_path, record)
    if args.json:
        print(json.dumps(result))
    else:
        print(f"{result['action']} run {result['run_id']} → runs[{result['runs_count']}] in {result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
