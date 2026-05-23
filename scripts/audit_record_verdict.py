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


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--verdict", required=True, choices=["yay", "nay", "suggest", "look-again"])
    p.add_argument("--reason", required=True)
    p.add_argument("--run-id", default=None)
    p.add_argument("--workdir", default=".")
    args = p.parse_args()

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
