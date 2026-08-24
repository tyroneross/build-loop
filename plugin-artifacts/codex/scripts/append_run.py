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

The caller's `commit` and `goal` are corroborated against git and the run's
`intent.md` by `run_provenance` before the record is validated (enforce-candidate
E3). An unreachable SHA is downgraded to `pending` rather than written, because a
record that names the wrong commit is worse than one that names none.
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
import run_provenance  # noqa: E402  (commit/goal corroboration — E3)

try:
    from write_run_entry.validators import validate_entry  # noqa: E402
except Exception:  # validators is optional; canonical shape is the real fix
    validate_entry = None

# Human-friendly CLI outcomes → canonical runs[] vocabulary (validators.VALID_OUTCOMES).
_OUTCOME_MAP = {"done": "pass", "partial": "partial", "blocked": "fail"}
# Never overridable via --extra-json. `provenance` is the record of what the gate
# below decided, so a caller that could set it could certify its own SHA.
_IMMUTABLE = {"run_id", "date", "source", "provenance"}
_PENDING = "pending"


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


def _apply_provenance(args: argparse.Namespace, workdir: Path, record: dict) -> None:
    """Corroborate `record["commit"]` + `record["goal"]`, mutating the record.

    A `block` finding means the SHA is not reachable from the run's push range
    (or HEAD): the caller's value is replaced with `pending`, and the refusal is
    recorded on `provenance.supplied_commit` so the wrong SHA stays auditable
    without being presented as fact. `--strict-provenance` raises instead, for
    callers that would rather review than record.

    Uses `getattr` for every new field: `stop_closeout` builds its own
    SimpleNamespace by hand, and this must not require it to grow arguments.
    """
    supplied = record.get("commit") or ""
    result = run_provenance.validate_run_provenance(
        run_id=record["run_id"],
        commit=supplied,
        goal=record.get("goal") or "",
        repo_root=str(workdir),
        push_range=getattr(args, "push_range", None) or None,
        intent_path=(
            getattr(args, "intent", None) or run_provenance.resolve_intent_path(workdir)
        ),
    )
    provenance = {"ok": result["ok"], "findings": result["findings"]}
    if not result["ok"]:
        detail = "; ".join(
            f["detail"] for f in result["findings"] if f.get("severity") == "block"
        )
        if getattr(args, "strict_provenance", False):
            raise SystemExit(f"run provenance rejected for {record['run_id']}: {detail}")
        provenance["supplied_commit"] = supplied
        record["commit"] = _PENDING
    for line in run_provenance.format_findings(result["findings"]):
        print(line, file=sys.stderr)
    record["provenance"] = provenance


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
    # E3: corroborate the commit + goal the caller handed us. Runs AFTER the
    # --extra-json merge because that merge can set `commit`, and an unvalidated
    # override is the same defect by another door.
    _apply_provenance(args, workdir, record)
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


def _enforce_owed_verification(workdir: Path, record: dict) -> dict | None:
    """Deferred import so a missing/broken module cannot block a run record."""
    try:
        import owed_verification  # noqa: WPS433

        return owed_verification.enforce_for_run_record(
            workdir, record, written_by="append_run")
    except Exception:  # noqa: BLE001 — the run record is the priority
        return None


def _merge_run_record(existing: dict, incoming: dict) -> dict:
    """Merge repeat inline writes without erasing stronger terminal evidence.

    Stop hooks can observe an in-progress top-level phase after an explicit rich
    run record has already landed. Replacing that row makes the last, thinnest
    writer win. This merge is monotonic: non-empty evidence accumulates, exact
    commits beat pending/shorter prefixes, and outcomes can improve but never
    regress from pass to partial/fail.
    """
    merged = dict(existing)
    outcome_rank = {"fail": 0, "partial": 1, "pass": 2}

    for key, value in incoming.items():
        current = merged.get(key)
        if key in ("run_id", "source", "date"):
            continue
        if key == "outcome":
            if outcome_rank.get(str(value), -1) > outcome_rank.get(str(current), -1):
                merged[key] = value
            continue
        if key == "commit":
            current_text = str(current or "").strip()
            value_text = str(value or "").strip()
            if current_text.lower() in ("", _PENDING) or (
                value_text.startswith(current_text) and len(value_text) > len(current_text)
            ):
                merged[key] = value
            continue
        if isinstance(current, list) and isinstance(value, list):
            seen = {json.dumps(item, sort_keys=True, default=str) for item in current}
            merged[key] = list(current)
            for item in value:
                identity = json.dumps(item, sort_keys=True, default=str)
                if identity not in seen:
                    seen.add(identity)
                    merged[key].append(item)
            continue
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**value, **current}
            continue
        if current in (None, "", [], {}) and value not in (None, "", [], {}):
            merged[key] = value
    return merged


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
                runs[i] = _merge_run_record(r, record)
                action = "merged"
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
    p.add_argument("--push-range", default="",
                   help="git range the run actually pushed (e.g. cb9cba9..3cb8295); "
                        "--commit must be reachable from it")
    p.add_argument("--intent", default="",
                   help="intent/plan markdown to check --goal against "
                        "(default: <workdir>/.build-loop/intent.md)")
    p.add_argument("--strict-provenance", action="store_true",
                   help="raise on an unreachable --commit instead of recording 'pending'")
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
    if record.get("provenance", {}).get("findings"):
        result["provenance"] = record["provenance"]
    # GAP-1: a run closes with a real independent-auditor verdict OR with a
    # manifest naming what is owed -- never with neither. Bound to the write
    # that persists the record rather than left to the caller, because the
    # caller remembering is exactly what failed: five runs closed here with
    # `auditor_status: not-run:parent-must-dispatch` and no manifest, while the
    # contract calling that write MANDATORY sat in markdown. Fail-open.
    manifest = _enforce_owed_verification(workdir, record)
    if manifest is not None:
        result["owed_verification"] = {
            "owed": manifest.get("owed", []),
            "reason": manifest.get("reason"),
        }
    if args.json:
        print(json.dumps(result))
    else:
        print(f"{result['action']} run {result['run_id']} → runs[{result['runs_count']}] in {result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
