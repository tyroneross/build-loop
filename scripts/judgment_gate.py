#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""judgment_gate.py — Phase 4 Review-G gate: did the Frontier judgment layer run?

A stakes-gated run MUST route the verification verdict (independent-auditor) and
Phase-2 plan synthesis (advisor) to the Frontier tier (Fable) via dispatch. The
advisor/auditor ladders RECORD which rung fired (`advisor_status`/`auditor_status`)
but nothing ENFORCES it: an inline run silently sits at the inline-Opus floor and
the high-tier judgment never happens. This gate catches that.

Everything is scoped to the CURRENT run (`--run-id`, else the latest `runs[]`
entry): stakes, statuses, and the agent-ledger are all read for that run only, so
a stale trigger or a historical ledger row can never latch every future run into a
permanent FAIL.

Stakes-conditional, mirroring the ladders:
  - No stakes trigger on this run → PASS (inline is the documented Rung-3 floor).
  - Stakes fired AND the auditor/advisor dispatched to Frontier/peer
    (`ran:dispatched-agent` / `ran:peer-host`, or advisor `inline-frontier`) → PASS.
  - Stakes fired AND it sat at the floor (`fallback:inline-opus`,
    `not-run:parent-must-dispatch`, or no status) →
      * agent-tool reachable (top-level run) → FAIL.
      * agent-tool unreachable (nested / no Agent tool) → WARN (parent owes it).
  - A this-run agent-ledger `verify`/`author` action by the auditor/advisor at a
    non-`frontier` tier → FAIL.

Deterministic; the orchestrator runs it at Review-G and treats `fail` as
"not review-complete".
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

_STAKES_LEVELS = {"medium", "high", "critical"}
# Agents this gate governs; deliberate Sonnet verification agents (synthesis-critic,
# alignment-checker) are NOT auditor/advisor and must not trip the tier check.
_GOVERNED_AGENTS = {"independent-auditor", "advisor"}
_FRONTIER_ACTIONS = {"verify", "author", "re-plan"}

# C1 (opt-in via --require-seats): verification seats whose activation is required
# when a given stakes REASON fires. auditor/advisor are governed separately via
# their dedicated *_status fields, so they are excluded here to avoid double-count.
# A seat is "present" if the agent-ledger carries a row for THIS run naming it.
_REQUIRED_SEATS_BY_REASON = {
    "synthesisDensity": ("plan-critic", "scope-auditor"),
    "riskSurfaceChange": ("security-reviewer",),
}


def _seats_present(ledger_path: Path, run_id: str | None) -> set:
    """Agent names with at least one agent-ledger row for THIS run."""
    present: set = set()
    if not ledger_path.exists():
        return present
    for line in ledger_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if run_id and row.get("run_id") != run_id:
            continue
        agent = row.get("agent")
        if agent:
            present.add(str(agent))
    return present


def _missing_required_seats(reasons: list[str], present: set) -> list[str]:
    """Seats a stakes reason requires that have no activation evidence this run."""
    required: set = set()
    for reason in reasons:
        # reasons are formatted like "synthesisDensity=6>5" / "riskSurfaceChange"
        key = reason.split("=", 1)[0].split(":", 1)[0]
        required.update(_REQUIRED_SEATS_BY_REASON.get(key, ()))
    return sorted(s for s in required if s not in present)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        d = json.loads(path.read_text() or "{}")
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def resolve_run(state: dict, run_id: str | None) -> dict:
    """The run record this gate evaluates: the --run-id match, else the latest."""
    runs = state.get("runs")
    if not isinstance(runs, list):
        return {}
    if run_id:
        for r in runs:
            if isinstance(r, dict) and r.get("run_id") == run_id:
                return r
        return {}
    for r in reversed(runs):
        if isinstance(r, dict):
            return r
    return {}


def stakes_reasons(run: dict) -> list[str]:
    """Stakes read from THIS RUN's record only — never stale top-level/triggers."""
    reasons: list[str] = []
    # synthesisDensity is written by Phase 1 Assess as the dict {count, escalated,
    # reason} (skills/build-loop/references/phase-1-assess.md). Accept both that
    # canonical shape and a bare int so the gate reads the signal every writer
    # actually produces — otherwise int({...}) → 0 and a stakes-gated run is
    # silently treated as un-gated.
    sd_raw = run.get("synthesisDensity", 0)
    if isinstance(sd_raw, dict):
        sd_raw = sd_raw.get("count", 0)
    try:
        sd = int(sd_raw)
    except (TypeError, ValueError):
        sd = 0
    if sd > 5:
        reasons.append(f"synthesisDensity={sd}>5")
    triggers = run.get("triggers") if isinstance(run.get("triggers"), dict) else {}
    if bool(run.get("riskSurfaceChange", triggers.get("riskSurfaceChange", False))):
        reasons.append("riskSurfaceChange")
    if str(run.get("stakes", "") or "").lower() in _STAKES_LEVELS:
        reasons.append(f"stakes={str(run.get('stakes')).lower()}")
    if str(run.get("dispatch_tier", "") or "").lower() == "frontier":
        reasons.append("dispatch_tier:frontier")
    return reasons


def _status(run: dict, state: dict, key: str):
    """Status from the run record first, then top-level (current-run, not a latch)."""
    if run.get(key) is not None:
        return run[key]
    return state.get(key)


def _ledger_tier_violations(ledger_path: Path, run_id: str | None) -> list[str]:
    """Auditor/advisor frontier-actions for THIS run recorded at a non-frontier tier."""
    if not ledger_path.exists():
        return []
    viol: list[str] = []
    for line in ledger_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if run_id and row.get("run_id") != run_id:
            continue
        if str(row.get("agent", "")) not in _GOVERNED_AGENTS:
            continue
        if str(row.get("action", "")) not in _FRONTIER_ACTIONS:
            continue
        if str(row.get("tier", "")).lower() not in ("frontier", ""):
            viol.append(
                f"ledger: {row.get('agent')} {row.get('action')} ran at "
                f"tier={row.get('tier')} model={row.get('model')} (expected frontier)"
            )
    return viol


def evaluate(state: dict, ledger_path: Path, run_id: str | None, agent_tool_available: bool,
             require_seats: bool = False) -> dict:
    run = resolve_run(state, run_id)
    effective_run_id = run.get("run_id") if run else run_id
    reasons = stakes_reasons(run)
    findings: list[dict] = []
    missing_seats: list[str] = []

    if not reasons:
        verdict, why = "pass", "no stakes trigger on this run; inline judgment is the documented Rung-3 floor"
    else:
        auditor = str(_status(run, state, "auditor_status") or "")
        if not auditor.startswith("ran:"):
            findings.append({
                "severity": "fail" if agent_tool_available else "warn",
                "layer": "independent-auditor",
                "status": auditor or "<unrecorded>",
                "detail": (
                    "stakes-gated run did not dispatch the independent-auditor to Frontier; "
                    + ("Agent tool reachable, so the inline floor is not acceptable — dispatch it before Report"
                       if agent_tool_available
                       else "nested/no-Agent-tool — the dispatching parent owes the audit (parent-dispatch contract)")
                ),
            })

        advisor = _status(run, state, "advisor_status")
        if advisor is not None:
            advisor = str(advisor)
            if not (advisor.startswith("ran:") or advisor == "inline-frontier"):
                findings.append({
                    "severity": "fail" if agent_tool_available else "warn",
                    "layer": "advisor",
                    "status": advisor,
                    "detail": "stakes-gated plan synthesis sat at the inline-Opus floor (advisor not dispatched)",
                })

        for v in _ledger_tier_violations(ledger_path, effective_run_id):
            findings.append({"severity": "fail", "layer": "agent-ledger", "status": "wrong-tier", "detail": v})

        if require_seats:
            missing_seats = _missing_required_seats(reasons, _seats_present(ledger_path, effective_run_id))
            for seat in missing_seats:
                findings.append({
                    "severity": "fail" if agent_tool_available else "warn",
                    "layer": seat,
                    "status": "not-run",
                    "detail": (
                        f"stakes-gated run did not activate the {seat} verification seat "
                        + ("(Agent tool reachable — dispatch it before Report)"
                           if agent_tool_available
                           else "(nested/no-Agent-tool — the dispatching parent owes it)")
                    ),
                })

        if any(f["severity"] == "fail" for f in findings):
            verdict = "fail"
        elif any(f["severity"] == "warn" for f in findings):
            verdict = "warn"
        else:
            verdict = "pass"
        why = "frontier judgment layer dispatched" if verdict == "pass" else "frontier judgment layer skipped at the inline floor"

    return {
        "verdict": verdict,
        "run_id": effective_run_id,
        "stakes_gated": bool(reasons),
        "stakes_reasons": reasons,
        "agent_tool_available": agent_tool_available,
        "missing_seats": missing_seats,
        "findings": findings,
        "summary": why,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Phase-4 gate: was the Frontier judgment layer dispatched on a stakes-gated run?")
    p.add_argument("--workdir", required=True)
    p.add_argument("--run-id", default="", help="Run to evaluate (default: latest runs[] entry).")
    p.add_argument("--agent-tool-available", choices=["true", "false"], default="true",
                   help="Whether Rung-1 (Agent dispatch) was reachable; top-level interactive runs = true (default).")
    p.add_argument("--require-seats", action="store_true",
                   help="C1 (opt-in): also attest stakes-required verification seats "
                        "(plan-critic/scope-auditor on synthesisDensity>5; security-reviewer on riskSurfaceChange) "
                        "via the agent-ledger; report gaps in missing_seats[]. Default off (back-compat).")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    workdir = Path(args.workdir).expanduser().resolve()
    state = _load_json(workdir / ".build-loop" / "state.json")
    ledger = workdir / ".build-loop" / "agent-ledger.jsonl"
    result = evaluate(state, ledger, args.run_id or None, args.agent_tool_available == "true",
                      require_seats=args.require_seats)

    if args.json:
        print(json.dumps(result))
    else:
        print(f"judgment_gate: {result['verdict'].upper()} — {result['summary']}")
        if result["stakes_reasons"]:
            print(f"  run={result['run_id']}  stakes: {', '.join(result['stakes_reasons'])}")
        for f in result["findings"]:
            print(f"  [{f['severity']}] {f['layer']} ({f['status']}): {f['detail']}")
    return 1 if result["verdict"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
