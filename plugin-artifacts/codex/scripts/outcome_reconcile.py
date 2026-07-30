#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""outcome_reconcile.py — never stamp a SHIPPED run as fail (Item 3 part B).

A crash-orphaned run (the process dies after the work merges but before a clean
success is recorded — the easy-terminal case) gets stamped ``fail`` by the default
outcome mapping, poisoning Phase 6 Learn with a false negative. This reconciles the
PROPOSED outcome against ground truth BEFORE the runs[] write: if the run's commit
actually shipped (reachable from the default branch / origin), OR the independent
auditor passed, OR Rally facts record success, the outcome is corrected upward — a
shipped run is never recorded as ``fail``.

Correction ladder (only ``fail`` is at risk of the false-negative; pass/partial pass
through untouched):
    fail + (merged AND auditor-passed)  -> pass     (shipped and verified)
    fail + (merged OR auditor-passed OR rally-success) -> partial  (shipped; process
                                                          did not close cleanly)
    fail + no ship signal               -> fail      (a real failure — unchanged)

Every correction records ``outcome_reconciled`` evidence on the record so the change
is auditable, never silent. Pure ground-truth checks (git + on-record verdict + rally),
no LLM. Never raises — a reconciliation error leaves the proposed outcome intact.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

_DEFAULT_TARGETS = ("origin/main", "main", "origin/master", "master")


def _git(workdir: Path, *args: str, timeout: int = 10) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(workdir), *args],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip()
    except Exception:
        return 127, ""


def commit_shipped(workdir: Path, commit: str | None,
                   targets: tuple[str, ...] = _DEFAULT_TARGETS) -> tuple[bool, str]:
    """True if ``commit`` is an ancestor of any target branch (i.e. it merged/shipped)."""
    if not commit:
        return False, "no commit on record"
    rc, _ = _git(workdir, "rev-parse", "--verify", "--quiet", commit + "^{commit}")
    if rc != 0:
        return False, f"commit {commit} not found"
    for target in targets:
        trc, _ = _git(workdir, "rev-parse", "--verify", "--quiet", target)
        if trc != 0:
            continue
        arc, _ = _git(workdir, "merge-base", "--is-ancestor", commit, target)
        if arc == 0:
            return True, f"{commit} is an ancestor of {target}"
    return False, f"{commit} not reachable from any default branch"


def auditor_passed(record: dict) -> tuple[bool, str]:
    """True if an independent-auditor verdict on the record is a pass/approve."""
    for jd in record.get("judge_decisions") or []:
        if not isinstance(jd, dict):
            continue
        who = str(jd.get("judge_id") or jd.get("judge") or jd.get("agent") or "").lower()
        verdict = str(jd.get("verdict") or jd.get("decision") or jd.get("status") or "").lower()
        if "auditor" in who and verdict in ("pass", "approve", "approved", "safe_to_merge", "commit_as_planned"):
            return True, f"{who} verdict={verdict}"
    return False, "no passing auditor verdict on record"


def rally_success(workdir: Path, run_id: str | None) -> tuple[bool, str]:
    """Best-effort: a Rally fact for this run recording a success/pass. Never blocks."""
    if not run_id:
        return False, "no run_id"
    try:
        p = subprocess.run(["rally", "facts", "--json"], cwd=str(workdir),
                           capture_output=True, text=True, timeout=8)
        if p.returncode != 0 or not p.stdout.strip():
            return False, "rally unavailable"
        data = json.loads(p.stdout)
        facts = data if isinstance(data, list) else data.get("facts", [])
        for f in facts:
            blob = json.dumps(f).lower() if not isinstance(f, str) else f.lower()
            if run_id.lower() in blob and any(k in blob for k in ("success", "shipped", "passed", "merged", "pass")):
                return True, f"rally fact references {run_id} with success"
    except Exception:
        return False, "rally lookup failed"
    return False, "no rally success fact"


def reconcile(workdir: Path, proposed_outcome: str, record: dict,
              run_id: str | None = None) -> dict:
    """Return {outcome, changed, evidence}. Only ``fail`` is ever corrected upward."""
    result = {"outcome": proposed_outcome, "changed": False, "evidence": {}}
    if proposed_outcome != "fail":
        return result
    try:
        commit = record.get("commit")
        merged, merge_why = commit_shipped(Path(workdir), commit)
        audited, audit_why = auditor_passed(record)
        rallied, rally_why = rally_success(Path(workdir), run_id or record.get("run_id"))
        evidence = {
            "merged": {"value": merged, "why": merge_why},
            "auditor_passed": {"value": audited, "why": audit_why},
            "rally_success": {"value": rallied, "why": rally_why},
        }
        if merged and audited:
            final = "pass"
        elif merged or audited or rallied:
            final = "partial"
        else:
            final = "fail"
        result["evidence"] = evidence
        if final != proposed_outcome:
            result["outcome"] = final
            result["changed"] = True
            result["reason"] = (
                f"proposed fail corrected to {final}: shipped work must not be stamped fail "
                f"(merged={merged}, auditor_passed={audited}, rally_success={rallied})"
            )
    except Exception as exc:  # noqa: BLE001 — never break the run-record write
        result["error"] = repr(exc)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="outcome_reconcile", description=__doc__)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--outcome", required=True, choices=["pass", "fail", "partial"])
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--record-json", default=None, help="path to a JSON run record (else read stdin)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.record_json:
        record = json.loads(Path(args.record_json).read_text())
    else:
        import sys
        raw = sys.stdin.read().strip()
        record = json.loads(raw) if raw else {}
    out = reconcile(Path(args.workdir).expanduser(), args.outcome, record, run_id=args.run_id)
    print(json.dumps(out, indent=2) if args.json else json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
