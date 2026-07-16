#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Append a Learn-visible run record to `.build-loop/state.json.runs[]`.

The Phase 6 recurring-pattern-detector scans `state.json.runs[]` for pain
signals (phase failures, manual interventions, security findings). That array is
normally written only by the orchestrator's Review-G (`write_run_entry`), so an
INLINE build-loop run (skill-as-methodology, no orchestrator dispatch) records
nothing and is invisible to Learn. This lets any run-close path — inline or the
closeout — append a CANONICAL run record so Learn can see it.

Record shape matches `write_run_entry/validators.py` (phases as a dict, outcome
in {pass,fail,partial}) and is validated before write. The read-modify-write goes
through `atomic_io.LockedFile` + `atomic_write_bytes` (the single-failure-site
write contract) so it can't race the orchestrator or corrupt state.json on crash.
Append-only and idempotent on `run_id`; refuses to clobber an unparseable file or
to replace a richer orchestrator-written record.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_io import LockedFile, atomic_write_bytes  # noqa: E402

try:
    from write_run_entry.validators import validate_entry  # noqa: E402
except Exception:  # validators is optional; canonical shape is the real fix
    validate_entry = None

# Human-friendly CLI outcomes → canonical runs[] vocabulary (validators.VALID_OUTCOMES).
_OUTCOME_MAP = {"done": "pass", "partial": "partial", "blocked": "fail"}
_IMMUTABLE = {"run_id", "date", "source"}  # never overridable via --extra-json


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
    # Canonical run-record shape (write_run_entry/validators REQUIRED_FIELDS):
    # phases is a DICT keyed by phase id; outcome ∈ {pass,fail,partial}.
    record: dict = {
        "run_id": args.run_id,
        "date": _utc_date(),
        "goal": args.goal or "",
        "outcome": _OUTCOME_MAP[args.outcome],
        "host": args.host,
        "commit": args.commit or _git_head(workdir),
        "phases": {},
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
        record["phases"][pid.strip()] = {"status": (status.strip() or "pass")}
    if args.extra_json:
        try:
            extra = json.loads(args.extra_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--extra-json is not valid JSON: {exc}")
        if isinstance(extra, dict):
            for k in _IMMUTABLE:
                extra.pop(k, None)  # identity fields are not overridable
            record.update(extra)
    # Item 3B: never stamp a SHIPPED run as fail. Reconcile the proposed outcome
    # against ground truth (git merge state + auditor verdict + Rally facts) BEFORE
    # the record is validated/written. A crash-orphaned run whose work actually
    # merged would otherwise poison Phase 6 Learn with a false-negative fail.
    if record.get("outcome") == "fail":
        try:
            import outcome_reconcile  # noqa: WPS433 (deferred; fail-open if absent)

            rec = outcome_reconcile.reconcile(
                workdir, "fail", record, run_id=record.get("run_id"))
            if rec.get("changed"):
                record["outcome"] = rec["outcome"]
                record["outcome_reconciled"] = {
                    "proposed": "fail",
                    "final": rec["outcome"],
                    "reason": rec.get("reason"),
                    "evidence": rec.get("evidence"),
                }
        except Exception:  # noqa: BLE001 — reconciliation must never break the write
            pass
    if validate_entry is not None:
        validate_entry(record)  # raises on a non-canonical record
    return record


def append_run(state_path: Path, record: dict) -> dict:
    # One writer contract: lock + atomic replace, never a bare read/write race.
    with LockedFile(state_path):
        if state_path.exists():
            raw = state_path.read_text()
            if raw.strip():
                try:
                    state = json.loads(raw)
                except json.JSONDecodeError:
                    raise SystemExit(
                        f"{state_path} exists but is not valid JSON; refusing to overwrite "
                        "(recover or remove it first)"
                    )
                if not isinstance(state, dict):
                    raise SystemExit(f"{state_path} is not a JSON object; refusing to overwrite")
            else:
                state = {}
        else:
            state = {}

        runs = state.get("runs")
        if not isinstance(runs, list):
            runs = []
        action = "appended"
        for i, r in enumerate(runs):
            if isinstance(r, dict) and r.get("run_id") == record["run_id"]:
                # Don't replace a richer orchestrator-written record with a thin inline one.
                if r.get("source") != "append_run":
                    raise SystemExit(
                        f"run_id {record['run_id']!r} already written by "
                        f"{r.get('source', 'the orchestrator')}; refusing to overwrite a richer record"
                    )
                runs[i] = record
                action = "replaced"
                break
        else:
            runs.append(record)
        state["runs"] = runs
        atomic_write_bytes(state_path, (json.dumps(state, indent=2) + "\n").encode())

    return {"run_id": record["run_id"], "action": action, "runs_count": len(runs), "path": str(state_path)}


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
    p.add_argument("--extra-json", default="", help="JSON object merged into the record (identity fields ignored)")
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
