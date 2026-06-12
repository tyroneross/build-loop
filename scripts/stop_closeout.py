#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""stop_closeout.py — structural run-close for INLINE build-loop runs (f6).

Backs ``hooks/closeout.sh`` (the Claude Code ``Stop`` hook) and the Codex ``Stop``
equivalent. The problem it closes: build-loop's run-close artifacts
(``append_run.py`` → ``state.json.runs[]`` for Phase 6 Learn, ``judgment_gate.py``
→ the Frontier-judgment check) only fire inside the orchestrator's Review-G. An
INLINE run (skill-as-methodology on the host loop, no orchestrator dispatch)
reaches NEITHER, so it stays invisible to Learn and the judgment gap is silent
until a human prompts for the closeout. This fires both STRUCTURALLY at the Stop
boundary — no human prompt.

Honest scope limit: a Stop hook cannot dispatch agents, so this auto-RECORDS the
run and auto-SURFACES the judgment gap (WARN). It does NOT make the Frontier
(Fable) judgment happen, and it leaves a ``closeout-pending/<run-id>.md`` marker
that the next SessionStart surfaces, reminding to run the retrospective-synthesizer
+ memory closeout (also agent dispatches a Stop hook cannot do).

Contract (mirrors build-loop's hook charter):
  * Advisory + fail-open: ``exit 0`` always; valid JSON on stdout; never blocks.
  * Self-gated: caller checks ``.build-loop/`` presence; this skips cleanly when
    no run touched THIS session.
  * Idempotent with Review-G: never double-records (the marker file is the
    inline-path sentinel; ``runs[]`` membership is the Review-G sentinel), and
    refuses to clobber a richer orchestrator record (``append_run`` enforces it).
  * Honest labeling: a Stop-recorded run carries ``source: append_run`` and a
    FLOOR ``auditor_status`` (``not-run:parent-must-dispatch``) — it never marks
    the run as judged. The gate WARNs that the Frontier layer was skipped.

Two modes:
  --mode stop           (default) record the run + run the gate + write the marker,
                        emit Stop-hook JSON (advisory ``systemMessage`` on WARN/FAIL).
  --mode session-start  surface any ``closeout-pending/*.md`` marker ONCE (emit
                        SessionStart ``additionalContext``), then move it to
                        ``closeout-pending/surfaced/``.

DRY: imports ``append_run`` and ``judgment_gate`` (the existing, already-tested
run-record writer and Frontier-judgment gate) rather than re-implementing or
shelling out — one python process, no output parsing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import append_run  # noqa: E402  (run-record writer + idempotent append)
import judgment_gate  # noqa: E402  (Frontier-judgment gate evaluator)

# No-session fallback window: when the host did not pass a session id (e.g. a
# Codex Stop hook), treat the run as "this session" only if its heartbeat is
# fresher than this. Generous enough for a long inline run, tight enough to
# exclude a months-old abandoned state.json. The session-id match is the primary
# gate; this is the proxy only when no session id is available.
_HEARTBEAT_FRESH_MINUTES = 120


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_state(workdir: Path) -> dict | None:
    """Parse .build-loop/state.json; None when absent or unparseable (fail-open)."""
    path = workdir / ".build-loop" / "state.json"
    if not path.exists():
        return None
    try:
        raw = path.read_text()
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return state if isinstance(state, dict) else None


def _derive_goal(workdir: Path, state: dict) -> str:
    """Best-effort goal: goal.md first non-empty line, else run_label, else generic."""
    goal_md = workdir / ".build-loop" / "goal.md"
    try:
        if goal_md.exists():
            for line in goal_md.read_text().splitlines():
                line = line.strip().lstrip("# ").strip()
                if line:
                    return line[:300]
    except OSError:
        pass
    label = ((state.get("execution") or {}).get("run_label") or "").strip()
    return label[:300] if label else "(inline build-loop run)"


def _derive_outcome(state: dict) -> str:
    """Map current phase → append_run outcome vocab (done|partial|blocked)."""
    phase = str(state.get("phase") or "").strip().lower()
    if phase in ("done", "report", "complete", "completed"):
        return "done"
    if phase in ("blocked", "abort", "aborted"):
        return "blocked"
    return "partial"  # honest: an inline run that stopped short of Review-G


def _this_session(execution: dict, session_id: str, now: datetime) -> tuple[bool, str]:
    """Did a build-loop run touch THIS session?

    Primary: the run's current/started session id equals the Stop hook's
    session id. Fallback (no session id from the host): the heartbeat is fresh.
    """
    cur = str(execution.get("current_session_id") or "").strip()
    started = str(execution.get("started_by_session_id") or "").strip()
    if session_id:
        if cur and session_id == cur:
            return True, "session matches current_session_id"
        if started and session_id == started:
            return True, "session matches started_by_session_id"
        if cur or started:
            return False, "session id does not match this run's session"
        # Run never recorded a session id; fall through to heartbeat freshness.
    last = _parse_iso(str(execution.get("last_heartbeat_at") or ""))
    if last is not None:
        age_min = (now - last).total_seconds() / 60.0
        if age_min < _HEARTBEAT_FRESH_MINUTES:
            return True, f"heartbeat fresh ({age_min:.0f}m < {_HEARTBEAT_FRESH_MINUTES}m)"
        return False, f"heartbeat stale ({age_min:.0f}m)"
    return False, "no session id and no parseable heartbeat — cannot confirm this session"


def _marker_path(workdir: Path, run_id: str) -> Path:
    return workdir / ".build-loop" / "closeout-pending" / f"{run_id}.md"


def decide(workdir: Path, state: dict, session_id: str, now: datetime) -> dict:
    """Decide what the Stop closeout should do. Pure (no writes)."""
    execution = state.get("execution") or {}
    run_id = str(execution.get("build_loop_id") or "").strip()
    if not run_id:
        return {"action": "skip", "reason": "no build_loop_id — no run initialized in this repo"}

    if _marker_path(workdir, run_id).exists():
        return {"action": "skip", "reason": "closeout already done for this run (marker present)", "run_id": run_id}

    runs = state.get("runs")
    runs = runs if isinstance(runs, list) else []
    if any(isinstance(r, dict) and r.get("run_id") == run_id for r in runs):
        # Review-G (or a prior run-close) already recorded it. Idempotent no-op.
        return {"action": "already_recorded", "reason": "run already in runs[]", "run_id": run_id}

    ok, why = _this_session(execution, session_id, now)
    if not ok:
        return {"action": "skip", "reason": why, "run_id": run_id}

    return {
        "action": "record",
        "reason": why,
        "run_id": run_id,
        "goal": _derive_goal(workdir, state),
        "outcome": _derive_outcome(state),
    }


def _stakes_extra(state: dict) -> dict:
    """Propagate this run's stakes signal + a FLOOR auditor_status into the record.

    ``judgment_gate.stakes_reasons`` reads stakes from the RUN RECORD only, so a
    Stop-recorded run must carry the signal (triggers/stakes/synthesisDensity/
    dispatch_tier) for the gate to know the run was stakes-gated. The floor
    ``auditor_status`` is set ONLY when no real ``ran:``/recorded status exists —
    a Stop hook cannot dispatch the auditor, so the honest floor is
    ``not-run:parent-must-dispatch``.
    """
    extra: dict = {}
    execution = state.get("execution") or {}
    triggers = state.get("triggers")
    if isinstance(triggers, dict) and triggers:
        extra["triggers"] = triggers
    for key in ("stakes", "synthesisDensity", "dispatch_tier", "riskSurfaceChange"):
        for src in (state, execution):
            if src.get(key) is not None:
                extra[key] = src.get(key)
                break

    existing_auditor = state.get("auditor_status") or execution.get("auditor_status")
    extra["auditor_status"] = existing_auditor if existing_auditor else "not-run:parent-must-dispatch"
    # advisor_status deliberately left unset: the gate only flags the advisor when
    # advisor_status is non-null, and a Stop hook has no advisor signal to assert.
    return extra


def _record_run(workdir: Path, decision: dict, state: dict) -> dict:
    """Append the run via append_run (idempotent; refuses to clobber richer records)."""
    extra = _stakes_extra(state)
    ns = SimpleNamespace(
        run_id=decision["run_id"],
        goal=decision["goal"],
        outcome=decision["outcome"],
        host="claude_code",
        commit="",
        files_touched="",
        manual_intervention=["closeout:fired-by-stop-hook (inline run did not reach Review-G)"],
        phase=[],
        extra_json=json.dumps(extra),
    )
    record = append_run.build_record(ns, workdir)
    state_path = workdir / ".build-loop" / "state.json"
    return append_run.append_run(state_path, record)


def _run_gate(workdir: Path, run_id: str) -> dict:
    """Evaluate judgment_gate with agent_tool_available=False (a Stop hook has none)."""
    state = _read_state(workdir) or {}
    ledger = workdir / ".build-loop" / "agent-ledger.jsonl"
    return judgment_gate.evaluate(state, ledger, run_id, agent_tool_available=False)


def _write_marker(workdir: Path, decision: dict, verdict: dict) -> Path:
    marker = _marker_path(workdir, decision["run_id"])
    marker.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        f"run_id: {decision['run_id']}\n"
        f"recorded_at: {_utc_now().strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"outcome: {decision['outcome']}\n"
        f"judgment_verdict: {verdict.get('verdict')}\n"
        f"stakes_gated: {str(bool(verdict.get('stakes_gated'))).lower()}\n"
        "source: stop_closeout\n"
        "---\n\n"
        f"# Closeout pending — {decision['run_id']}\n\n"
        "This inline build-loop run was recorded to `state.json.runs[]` by the Stop\n"
        "hook (it did not reach orchestrator Review-G). A Stop hook cannot dispatch\n"
        "agents, so two closeout steps remain — run them in this session:\n\n"
        "1. **retrospective-synthesizer** — write the 9-section retrospective.\n"
        "2. **memory closeout** — extract durable lessons via `scripts/memory_writer.py`.\n\n"
        f"judgment_gate: **{str(verdict.get('verdict')).upper()}** — {verdict.get('summary')}\n"
    )
    append_run.atomic_write_bytes(marker, body.encode())
    return marker


def _stop_message(decision: dict, verdict: dict, write_result: dict) -> str:
    v = str(verdict.get("verdict"))
    base = (
        f"build-loop closeout (Stop hook): {write_result.get('action', 'recorded')} inline run "
        f"{decision['run_id']} → runs[] (Learn-visible)."
    )
    if v in ("warn", "fail"):
        reasons = ", ".join(verdict.get("stakes_reasons") or []) or "stakes-gated"
        return (
            f"{base} ⚠ judgment_gate: {v.upper()} — Frontier (Fable) judgment was skipped at the "
            f"inline floor ({reasons}); a Stop hook cannot dispatch agents. A closeout-pending marker "
            "was left for the retrospective-synthesizer + memory closeout."
        )
    return base


def run_stop(workdir: Path, session_id: str) -> dict:
    """Stop-mode entrypoint. Returns a Stop-hook JSON dict (always exit 0 upstream)."""
    state = _read_state(workdir)
    if state is None:
        return {}
    decision = decide(workdir, state, session_id, _utc_now())
    if decision["action"] in ("skip", "already_recorded"):
        return {}
    # action == "record"
    write_result = _record_run(workdir, decision, state)
    verdict = _run_gate(workdir, decision["run_id"])
    _write_marker(workdir, decision, verdict)
    if str(verdict.get("verdict")) in ("warn", "fail"):
        return {"systemMessage": _stop_message(decision, verdict, write_result)}
    # Recorded cleanly with no judgment gap to surface; stay quiet.
    return {}


def run_session_start(workdir: Path) -> dict:
    """SessionStart-mode entrypoint. Surface pending markers once, then archive them."""
    pending_dir = workdir / ".build-loop" / "closeout-pending"
    if not pending_dir.is_dir():
        return {}
    markers = sorted(p for p in pending_dir.glob("*.md") if p.is_file())
    if not markers:
        return {}
    lines = [
        "build-loop closeout-pending — inline run(s) recorded by the Stop hook still owe "
        "a retrospective-synthesizer pass + memory closeout (a Stop hook cannot dispatch agents):",
    ]
    surfaced_dir = pending_dir / "surfaced"
    surfaced_dir.mkdir(parents=True, exist_ok=True)
    for m in markers:
        lines.append(f"  - {m.stem} (see {m.relative_to(workdir)})")
        try:
            m.rename(surfaced_dir / m.name)
        except OSError:
            pass  # surfacing is best-effort; leave the marker if the move fails
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(lines),
        }
    }


def _read_session_id(explicit: str) -> str:
    """Session id from --session-id, else the Stop/SessionStart stdin JSON payload."""
    if explicit:
        return explicit
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return str(payload.get("session_id") or "")
    except (OSError, json.JSONDecodeError):
        pass
    return ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Structural Stop/SessionStart closeout for inline build-loop runs (f6).")
    p.add_argument("--workdir", required=True)
    p.add_argument("--mode", choices=["stop", "session-start"], default="stop")
    p.add_argument("--session-id", default="", help="Stop hook session id (else read from stdin JSON).")
    p.add_argument("--hook", action="store_true", help="Emit hook JSON on stdout (the only output mode that matters).")
    args = p.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    try:
        if args.mode == "session-start":
            out = run_session_start(workdir)
        else:
            out = run_stop(workdir, _read_session_id(args.session_id))
    except Exception:
        # Fail-open: a closeout hook must never break a turn or a session start.
        out = {}

    print(json.dumps(out) if out else "{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
