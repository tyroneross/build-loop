#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Backfill the hook-path verdict into runs[-1].judge_decisions[].

Paired with `audit_before_commit.py`. After Claude renders a verdict in
conversation, it invokes this to persist it. Per Verifiability-First Agents
(arXiv:2512.17259), audit trails must be reconstructable across both dispatch
paths. Exit 0 always — observability never blocks.

    python3 scripts/audit_record_verdict.py --verdict yay --reason "..."
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Single source of truth for the staged-diff hash — imported, never
# re-derived, so this script and audit_before_commit.py can never drift on
# what "the same staged diff" means (learn/risk-gated-commit-audit).
from audit_before_commit import staged_diff_hash  # noqa: E402


_VALID_ORACLE_COVERAGE = {"full", "partial", "thin"}


def _parse_oracle_completeness(raw: str | None) -> dict | None:
    """Parse + lightly validate the optional --oracle-completeness JSON note.

    Observability never blocks (this script exits 0 always), so a malformed note is
    WARNed to stderr and dropped rather than raised. Shape: object with optional
    string `covered`/`uncovered` and optional `coverage` in {full,partial,thin}.
    """
    if not raw:
        return None
    try:
        oc = json.loads(raw)
    except ValueError as exc:
        sys.stderr.write(f"[audit_record_verdict] --oracle-completeness not valid JSON, dropping: {exc}\n")
        return None
    if not isinstance(oc, dict):
        sys.stderr.write("[audit_record_verdict] --oracle-completeness must be an object, dropping\n")
        return None
    cov = oc.get("coverage")
    if cov is not None and cov not in _VALID_ORACLE_COVERAGE:
        sys.stderr.write(
            f"[audit_record_verdict] --oracle-completeness.coverage must be one of "
            f"{sorted(_VALID_ORACLE_COVERAGE)}, dropping note\n"
        )
        return None
    note: dict = {}
    for key in ("covered", "uncovered"):
        val = oc.get(key)
        if isinstance(val, str):
            note[key] = val[:300]
    if cov is not None:
        note["coverage"] = cov
    return note or None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--verdict", required=True, choices=["yay", "nay", "suggest", "look-again"])
    p.add_argument("--reason", required=True)
    p.add_argument("--run-id", default=None)
    p.add_argument("--workdir", default=".")
    p.add_argument(
        "--oracle-completeness",
        default=None,
        help=(
            "Optional advisory JSON note recording WHAT the check actually covered, e.g. "
            '\'{"covered":"auth+schema","uncovered":"rate-limit path","coverage":"partial"}\'. '
            "coverage must be one of full|partial|thin. A green gate with a thin oracle is a "
            "known false-confidence source (arXiv:2606.09863); recording completeness makes it "
            "visible. Additive + optional — omit to write no note."
        ),
    )
    args = p.parse_args()

    oracle_completeness = _parse_oracle_completeness(args.oracle_completeness)

    state_path = Path(args.workdir) / ".build-loop" / "state.json"
    if not state_path.is_file():
        sys.stderr.write(f"[audit_record_verdict] no state.json at {state_path}\n")
        return 0
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"[audit_record_verdict] read failed: {exc}\n")
        return 0

    runs = data.get("runs") or []
    if not runs:
        sys.stderr.write("[audit_record_verdict] no runs[]\n")
        return 0

    run = next((r for r in runs if r.get("run_id") == args.run_id), None) if args.run_id else runs[-1]
    if run is None:
        sys.stderr.write(f"[audit_record_verdict] run_id {args.run_id} not found\n")
        return 0

    decisions = run.setdefault("judge_decisions", [])
    target = next(
        (e for e in reversed(decisions)
         if e.get("judge_id") == "independent-auditor-hook" and e.get("verdict") == "pending"),
        None,
    )
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    if target is None:
        target = {"judge_id": "independent-auditor-hook", "target": "unspecified",
                  "status": "verdict_only", "ts": now}
        decisions.append(target)
    target["verdict"] = args.verdict
    target["reason"] = args.reason[:200]
    target["verdict_ts"] = now
    if oracle_completeness is not None:
        target["oracle_completeness"] = oracle_completeness

    # Diff-hash binding (learn/risk-gated-commit-audit tightening): bind this
    # verdict to the EXACT staged content at record time, not just the risky
    # file names already on `target` (left untouched above). Computed against
    # --workdir so the CLI's --workdir contract stays honored. Fail-safe: when
    # nothing is staged or the hash can't be computed, write no diff_hash at
    # all — a missing key can never satisfy the equality check in
    # audit_before_commit._has_matching_risk_verdict, so this never fails
    # open into a false pass.
    diff_hash = staged_diff_hash(cwd=Path(args.workdir))
    if diff_hash:
        target["diff_hash"] = diff_hash

    tmp = state_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, state_path)
    except OSError as exc:
        sys.stderr.write(f"[audit_record_verdict] write failed: {exc}\n")
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        return 0

    sys.stderr.write(
        f"[audit_record_verdict] verdict={args.verdict} run={run.get('run_id', '?')}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
