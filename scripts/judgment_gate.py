#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""judgment_gate.py — Phase 4 Review-G gate: did the Frontier judgment layer run?

A stakes-gated run MUST route the verification verdict (independent-auditor) and
Phase-2 plan synthesis (advisor) to the Frontier tier (Fable) via dispatch —
that is the whole point of the model org. The advisor/auditor dispatch ladders
already RECORD which rung fired (`advisor_status` / `auditor_status`) but nothing
ENFORCES it: an inline run (skill-as-methodology, no orchestrator dispatch)
silently sits at the inline-Opus floor and the high-tier judgment never happens.
This gate catches that.

Stakes-conditional, mirroring the ladders:
  - No stakes trigger fired → PASS (inline judgment is the documented Rung-3 floor).
  - Stakes fired AND the auditor/advisor actually dispatched to Frontier/peer
    (`ran:dispatched-agent` / `ran:peer-host`, or advisor `inline-frontier`) → PASS.
  - Stakes fired AND it sat at the floor (`fallback:inline-opus`,
    `not-run:parent-must-dispatch`, or no status) →
      * agent-tool reachable (top-level run) → FAIL — judgment was skippable but
        skipped; the run is NOT review-complete until the auditor is dispatched.
      * agent-tool unreachable (nested / no Agent tool) → WARN — the dispatching
        parent owes the audit (parent-dispatch contract), not a hard block here.
  - Ledger cross-check: a recorded auditor `verify` / advisor `author` action
    whose `tier` is not `frontier` → FAIL (ran at the wrong tier).

Reads `.build-loop/state.json` (stakes + `*_status`, top-level or latest run)
and `.build-loop/agent-ledger.jsonl`. Deterministic; the orchestrator runs it at
Review-G and treats `fail` as "not review-complete".
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

_STAKES_LEVELS = {"medium", "high", "critical"}
_OK_AUDITOR = ("ran:dispatched-agent", "ran:peer-host")
_OK_ADVISOR = ("ran:dispatched-agent", "ran:peer-host", "inline-frontier")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        d = json.loads(path.read_text() or "{}")
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def _latest_run(state: dict) -> dict:
    runs = state.get("runs")
    if isinstance(runs, list) and runs and isinstance(runs[-1], dict):
        return runs[-1]
    return {}


def _lookup(state: dict, key: str, default=None):
    """Look up a field at state top-level, under state.triggers, or in the latest run."""
    triggers = state.get("triggers") if isinstance(state.get("triggers"), dict) else {}
    for src in (state, triggers, _latest_run(state)):
        if isinstance(src, dict) and key in src and src[key] is not None:
            return src[key]
    return default


def stakes_reasons(state: dict) -> list[str]:
    reasons: list[str] = []
    sd = _lookup(state, "synthesisDensity", 0)
    try:
        sd = int(sd)
    except (TypeError, ValueError):
        sd = 0
    if sd > 5:
        reasons.append(f"synthesisDensity={sd}>5")
    if bool(_lookup(state, "riskSurfaceChange", False)):
        reasons.append("riskSurfaceChange")
    stakes = str(_lookup(state, "stakes", "") or "").lower()
    if stakes in _STAKES_LEVELS:
        reasons.append(f"stakes={stakes}")
    if str(_lookup(state, "dispatch_tier", "") or "").lower() == "frontier":
        reasons.append("dispatch_tier:frontier")
    return reasons


def _ledger_tier_violations(ledger_path: Path) -> list[str]:
    """Frontier-tier actions recorded at a non-frontier tier = a real mis-route."""
    if not ledger_path.exists():
        return []
    viol: list[str] = []
    frontier_actions = {"verify": "independent-auditor", "author": "advisor", "re-plan": "advisor"}
    for line in ledger_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = str(row.get("action", ""))
        if action in frontier_actions and str(row.get("tier", "")).lower() not in ("frontier", ""):
            viol.append(
                f"ledger: {row.get('agent', frontier_actions[action])} {action} ran at "
                f"tier={row.get('tier')} model={row.get('model')} (expected frontier)"
            )
    return viol


def evaluate(state: dict, ledger_path: Path, agent_tool_available: bool) -> dict:
    reasons = stakes_reasons(state)
    findings: list[dict] = []

    if not reasons:
        verdict, why = "pass", "no stakes trigger fired; inline judgment is the documented Rung-3 floor"
    else:
        # Auditor — the load-bearing verification verdict (Review-A build scope).
        auditor = str(_lookup(state, "auditor_status", "") or "")
        if not auditor.startswith("ran:"):
            sev = "fail" if agent_tool_available else "warn"
            findings.append({
                "severity": sev,
                "layer": "independent-auditor",
                "status": auditor or "<unrecorded>",
                "detail": (
                    "stakes-gated run did not dispatch the independent-auditor to Frontier; "
                    + ("Agent tool reachable, so the inline floor is not acceptable — dispatch it before Report"
                       if agent_tool_available
                       else "nested/no-Agent-tool — the dispatching parent owes the audit (parent-dispatch contract)")
                ),
            })

        # Advisor — Phase-2 plan synthesis (only checked when a status was recorded).
        advisor = _lookup(state, "advisor_status", None)
        if advisor is not None:
            advisor = str(advisor)
            if not (advisor.startswith("ran:") or advisor == "inline-frontier"):
                findings.append({
                    "severity": "fail" if agent_tool_available else "warn",
                    "layer": "advisor",
                    "status": advisor,
                    "detail": "stakes-gated plan synthesis sat at the inline-Opus floor (advisor not dispatched)",
                })

        for v in _ledger_tier_violations(ledger_path):
            findings.append({"severity": "fail", "layer": "agent-ledger", "status": "wrong-tier", "detail": v})

        if any(f["severity"] == "fail" for f in findings):
            verdict = "fail"
        elif any(f["severity"] == "warn" for f in findings):
            verdict = "warn"
        else:
            verdict = "pass"
        why = "frontier judgment layer dispatched" if verdict == "pass" else "frontier judgment layer skipped at the inline floor"

    return {
        "verdict": verdict,
        "stakes_gated": bool(reasons),
        "stakes_reasons": reasons,
        "agent_tool_available": agent_tool_available,
        "findings": findings,
        "summary": why,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Phase-4 gate: was the Frontier judgment layer dispatched on a stakes-gated run?")
    p.add_argument("--workdir", required=True)
    p.add_argument("--agent-tool-available", choices=["true", "false"], default="true",
                   help="Whether Rung-1 (Agent dispatch) was reachable; top-level interactive runs = true (default).")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    workdir = Path(args.workdir).expanduser().resolve()
    state = _load_json(workdir / ".build-loop" / "state.json")
    ledger = workdir / ".build-loop" / "agent-ledger.jsonl"
    result = evaluate(state, ledger, args.agent_tool_available == "true")

    if args.json:
        print(json.dumps(result))
    else:
        print(f"judgment_gate: {result['verdict'].upper()} — {result['summary']}")
        if result["stakes_reasons"]:
            print(f"  stakes: {', '.join(result['stakes_reasons'])}")
        for f in result["findings"]:
            print(f"  [{f['severity']}] {f['layer']} ({f['status']}): {f['detail']}")
    # Exit code: 0 pass/warn (advisory by default), 1 on fail so the orchestrator/CI can gate.
    return 1 if result["verdict"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
