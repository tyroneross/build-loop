#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""run_close_lint.py — assert the run-close mutation actually landed.

The problem this closes (2026-07-16, ObsidianVault/.obsidian/plugins/daily-planner):
six sequential dispatched ``build-loop:build-orchestrator`` agents each completed
with a high-quality report and wrote NO durable run record — no ``state.json.runs[]``
entry, no retrospective, no milestone, no feedback line. The plugin workdir had no
``.build-loop/`` at all; the only ``runs[]`` rows in the vault came from Stop hooks.
Phase 6 Learn saw zero signal from a six-run day.

Why the existing controls could not catch it. ``references/phase-4-review.md`` already
says the ``runs[]`` write "MUST fire on every Phase 4G regardless of dispatch path",
and ``write_run_entry --scope build`` already carries a review-completeness gate. Both
are inside the write path, so both are silent on the one failure mode that actually
happened: **non-invocation**. A gate that lives in the script cannot fire when nobody
runs the script. So this lint reads DURABLE STATE (``state.json``) instead of
participating in the write, which makes it the one check that a skipped Review-G
cannot also skip past — provided a caller outside that Review-G runs it.

Three callers, by design:

1. **Review-G self-assert** (``agents/build-orchestrator.md`` §G) — immediately after
   ``write_run_entry``, with ``--run-id <this run>``. Catches a write that was attempted
   and failed (bad flags, exit 3 review-completeness, unwritable state, wrong workdir).
2. **The dispatching parent** (``skills/build-loop/references/verify-dispatch.md`` step 6)
   — at the completion boundary, against the workdir it dispatched INTO. Catches an
   orchestrator that never reached Review-G at all, which is the observed failure.
   A child that skipped Review-G also reports no run_id, so the parent falls back to
   ``--expect-recent-minutes``.
3. **Fail-open hook callers** — with ``--advisory`` (always exit 0), for Stop-boundary
   or closeout paths that must never wedge a session.

Statuses (``status`` in the JSON envelope):

    recorded    exit 0  a qualifying runs[] entry exists
    skipped     exit 0  no run identity to check and no execution block — nothing ran here
    missing     exit 1  state.json is present but runs[] has no qualifying entry
    floor_only  exit 1  only a hook-written floor entry, and --require-orchestrator was set
    learn_missing exit 1 run record exists but --require-learn found no complete receipt
    no_state    exit 1  no .build-loop/state.json in this workdir at all (the loudest case:
                        the run produced no durable footprint whatsoever)

Every non-``recorded`` status carries ``remediation`` — the exact command to run — because
a lint that reports a gap without naming the fix just relocates the guesswork.

Orchestrator-grade vs floor record: ``append_run`` (the Stop-hook/inline writer) stamps
``source: "append_run"``; ``write_run_entry`` (the orchestrator's Review-G writer) stamps
no ``source``. That is already ``append_run``'s own "don't clobber a richer record"
predicate, reused here rather than reinvented.

Pure stdlib. Never raises on malformed input — a corrupt state.json is reported as a
status, not a traceback, so a caller in a hook path stays fail-open.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STATE_RELPATH = Path(".build-loop") / "state.json"

# Statuses that mean "the run closed its record".
OK_STATUSES = ("recorded", "skipped")

# The two FLOOR writers, i.e. everything that records a run WITHOUT the orchestrator
# having reached Review-G. Both signatures are needed, and neither is guessable from
# structure alone:
#   * ``scripts/append_run.py`` (Stop hook / inline path) stamps ``source: append_run``.
#   * ``scripts/audit_before_commit.py`` (PreToolUse commit hook) stamps NO source at
#     all and a ``hook_<ts>`` run id, with ``goal: "(hook-only commit; no orchestrator
#     run)"``. Checked against a live 21-entry state.json: 16 of 21 rows carried
#     ``source: None``, so a source-only predicate calls hook rows orchestrator-grade
#     and hands back a false pass on exactly the records the 2026-07-16 vault was full of.
# ``write_run_entry`` (Review-G) writes no ``source`` either, which is why the run-id
# prefix — not the absent source — is what separates it from the commit-hook writer.
FLOOR_SOURCE = "append_run"
FLOOR_RUN_ID_PREFIX = "hook_"

DEFAULT_RECENT_MINUTES = 240

# ``append_run`` / ``write_run_entry`` emit ISO ``%Y-%m-%dT%H:%M:%SZ``;
# ``audit_before_commit`` emits compact ``%Y%m%dT%H%M%SZ``. Parse both — an unparsed
# date silently drops the row out of the recency window, which would report a run that
# IS recorded as missing.
_COMPACT_DATE_FORMAT = "%Y%m%dT%H%M%SZ"


def is_orchestrator_grade(entry: Any) -> bool:
    """True when this run entry came from the orchestrator's Review-G writer.

    False for both floor writers (Stop-hook ``append_run`` and commit-hook ``hook_*``).
    """
    if not isinstance(entry, dict):
        return False
    if entry.get("source") == FLOOR_SOURCE:
        return False
    run_id = entry.get("run_id")
    if isinstance(run_id, str) and run_id.startswith(FLOOR_RUN_ID_PREFIX):
        return False
    return True


def _parse_iso(value: Any) -> datetime | None:
    """Parse a run entry's ``date`` in either writer's format."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw, _COMPACT_DATE_FORMAT)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_state(state_path: Path) -> tuple[dict | None, str | None]:
    """Return (state, error). Never raises — a bad file is an error string."""
    try:
        raw = state_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"unreadable: {exc}"
    if not raw.strip():
        return {}, None
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(state, dict):
        return None, "state.json is not a JSON object"
    return state, None


def _runs(state: dict) -> list[dict]:
    runs = state.get("runs")
    if not isinstance(runs, list):
        return []
    return [r for r in runs if isinstance(r, dict)]


def _execution_run_id(state: dict) -> str | None:
    execution = state.get("execution")
    if not isinstance(execution, dict):
        return None
    run_id = execution.get("run_id")
    return run_id if isinstance(run_id, str) and run_id.strip() else None


def _remediation(workdir: Path, run_id: str | None) -> str:
    """The exact command that closes the gap."""
    target = f" --run-id {shlex.quote(run_id)}" if run_id else ""
    return (
        'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/write_run_entry/__main__.py"'
        f" --workdir {shlex.quote(str(workdir))} --goal \"<goal>\" --outcome <pass|fail|partial>"
        f" --scope build --files-touched-from-git{target}"
        "  # then re-run run_close_lint.py to confirm"
    )


def _learn_remediation(workdir: Path, run_id: str) -> str:
    return (
        f"cd {shlex.quote(str(workdir))} && python3 scripts/learn/__main__.py run "
        f"--workdir {shlex.quote(str(workdir))} --run-id {shlex.quote(run_id)} "
        "--source review-g --json"
        "  # complete and attest every returned work order, then re-run this lint"
    )


def _learn_complete(workdir: Path, entry: dict[str, Any]) -> tuple[bool, str]:
    run_id = str(entry.get("run_id") or "")
    summary = entry.get("learn")
    if not isinstance(summary, dict):
        return False, "runs[].learn is absent"
    expected = f".build-loop/learn/{run_id}.json"
    if summary.get("receipt") != expected:
        return False, f"runs[].learn.receipt must equal {expected!r}"
    learn_root = (workdir / ".build-loop" / "learn").resolve()
    receipt_path = (workdir / expected).resolve()
    try:
        receipt_path.relative_to(learn_root)
    except ValueError:
        return False, "Learn receipt path escapes .build-loop/learn"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"Learn receipt is unreadable: {exc}"
    if receipt.get("schema") != "build-loop.learn-receipt.v1" or receipt.get("run_id") != run_id:
        return False, "Learn receipt identity/schema mismatch"
    if receipt.get("status") != "complete" or summary.get("status") != "complete":
        return False, f"Learn status is {receipt.get('status') or 'missing'}; expected complete"
    return True, "matching complete Learn receipt and runs[].learn summary"


def check(
    workdir: Path,
    run_id: str | None = None,
    recent_minutes: int | None = None,
    require_orchestrator: bool = False,
    require_learn: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assert a run-close record exists. Returns the JSON envelope (never raises).

    Evidence modes, in precedence order:
      * ``run_id`` given            → exact ``runs[]`` membership.
      * ``recent_minutes`` given    → at least one entry with ``date`` inside the window.
      * neither                     → fall back to ``state.json.execution.run_id``; when
                                      there is no execution block either, nothing ran in
                                      this workdir and the status is ``skipped``.
    """
    workdir = Path(workdir).resolve()
    state_path = workdir / STATE_RELPATH
    now = now or datetime.now(timezone.utc)

    envelope: dict[str, Any] = {
        "workdir": str(workdir),
        "state_path": str(state_path),
        "run_id": run_id,
        "mode": None,
        "status": None,
        "reason": None,
        "runs_count": 0,
    }

    if not state_path.exists():
        envelope.update(
            status="no_state",
            mode="run-id" if run_id else "state-presence",
            reason=(
                f"no {STATE_RELPATH} in {workdir} — this run left no durable footprint "
                "(Phase 1 Assess never wrote state here, or the run executed in a "
                "different workdir than the one being checked)"
            ),
            remediation=_remediation(workdir, run_id),
        )
        return envelope

    state, error = _load_state(state_path)
    if state is None:
        envelope.update(
            status="no_state",
            mode="run-id" if run_id else "state-presence",
            reason=f"{state_path} is unusable ({error})",
            remediation=_remediation(workdir, run_id),
        )
        return envelope

    runs = _runs(state)
    envelope["runs_count"] = len(runs)

    # ---- Mode A: exact run_id membership -------------------------------------
    resolved_id = run_id or (None if recent_minutes else _execution_run_id(state))
    if resolved_id:
        envelope["run_id"] = resolved_id
        envelope["mode"] = "run-id" if run_id else "execution-run-id"
        matches = [r for r in runs if r.get("run_id") == resolved_id]
        if not matches:
            envelope.update(
                status="missing",
                reason=(
                    f"run_id {resolved_id!r} is not in state.json.runs[] "
                    f"({len(runs)} entr{'y' if len(runs) == 1 else 'ies'} present) — "
                    "Phase 6 Learn cannot see this run"
                ),
                remediation=_remediation(workdir, resolved_id),
            )
            return envelope
        if require_orchestrator and not any(is_orchestrator_grade(r) for r in matches):
            envelope.update(
                status="floor_only",
                reason=(
                    f"run_id {resolved_id!r} exists only as a hook-written floor record "
                    f"(source: {FLOOR_SOURCE} or a {FLOOR_RUN_ID_PREFIX}* commit-hook row) "
                    "— the orchestrator's Review-G write never landed"
                ),
                remediation=_remediation(workdir, resolved_id),
            )
            return envelope
        if require_learn:
            complete, reason = _learn_complete(workdir, matches[-1])
            if not complete:
                envelope.update(
                    status="learn_missing",
                    reason=f"run_id {resolved_id!r} lacks executable Phase 6 proof: {reason}",
                    remediation=_learn_remediation(workdir, resolved_id),
                    learn_complete=False,
                )
                return envelope
            envelope["learn_complete"] = True
        envelope.update(
            status="recorded",
            reason=f"run_id {resolved_id!r} present in state.json.runs[]",
            orchestrator_grade=any(is_orchestrator_grade(r) for r in matches),
        )
        return envelope

    # ---- Mode B: recency window (parent fallback when no run_id is known) ----
    if recent_minutes is not None:
        envelope["mode"] = "recent-window"
        envelope["window_minutes"] = recent_minutes
        cutoff = now - timedelta(minutes=recent_minutes)
        fresh = [r for r in runs if (dt := _parse_iso(r.get("date"))) and dt >= cutoff]
        if require_orchestrator:
            fresh = [r for r in fresh if is_orchestrator_grade(r)]
        if not fresh:
            grade = "orchestrator-written " if require_orchestrator else ""
            envelope.update(
                status="missing",
                reason=(
                    f"no {grade}run entry recorded in the last {recent_minutes} minutes "
                    f"({len(runs)} total entr{'y' if len(runs) == 1 else 'ies'} in state.json.runs[]) "
                    "— the dispatched run closed without a durable record"
                ),
                remediation=_remediation(workdir, None),
            )
            return envelope
        if require_learn:
            fresh_with_learn = [r for r in fresh if _learn_complete(workdir, r)[0]]
            if not fresh_with_learn:
                newest_id = str(fresh[-1].get("run_id") or "")
                envelope.update(
                    status="learn_missing",
                    run_id=newest_id or None,
                    reason="recent run record exists but no matching complete Learn receipt was found",
                    remediation=_learn_remediation(workdir, newest_id),
                    learn_complete=False,
                )
                return envelope
            fresh = fresh_with_learn
            envelope["learn_complete"] = True
        envelope.update(
            status="recorded",
            run_id=fresh[-1].get("run_id"),
            reason=(
                f"{len(fresh)} run entr{'y' if len(fresh) == 1 else 'ies'} recorded "
                f"within {recent_minutes}m; newest run_id={fresh[-1].get('run_id')!r}"
            ),
            orchestrator_grade=any(is_orchestrator_grade(r) for r in fresh),
        )
        return envelope

    # ---- No run identity at all: nothing ran in this workdir -----------------
    envelope.update(
        status="skipped",
        mode="no-identity",
        reason=(
            "no --run-id, no --expect-recent-minutes, and no state.json.execution.run_id "
            "— no run to assert in this workdir"
        ),
    )
    return envelope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assert a build-loop run wrote its state.json.runs[] record.",
    )
    parser.add_argument("--workdir", required=True, help="Repo the run executed in.")
    parser.add_argument("--run-id", help="Exact run id to assert (Review-G self-check).")
    parser.add_argument(
        "--expect-recent-minutes",
        type=int,
        nargs="?",
        const=DEFAULT_RECENT_MINUTES,
        help=(
            "Parent fallback when the child reported no run id: require at least one "
            f"runs[] entry within this window (default {DEFAULT_RECENT_MINUTES} when "
            "the flag is given without a value)."
        ),
    )
    parser.add_argument(
        "--require-orchestrator",
        action="store_true",
        help="Reject a hook-written floor record; demand the Review-G write.",
    )
    parser.add_argument(
        "--require-learn",
        action="store_true",
        help="Require a matching complete Learn receipt plus runs[].learn summary.",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Always exit 0 (for fail-open hook callers); status still reports the gap.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the JSON envelope.")
    args = parser.parse_args(argv)

    result = check(
        Path(args.workdir),
        run_id=args.run_id,
        recent_minutes=args.expect_recent_minutes,
        require_orchestrator=args.require_orchestrator,
        require_learn=args.require_learn,
    )
    result["advisory"] = bool(args.advisory)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['status']}: {result['reason']}")
        if result.get("remediation"):
            print(f"remediation: {result['remediation']}")

    if args.advisory:
        return 0
    return 0 if result["status"] in OK_STATUSES else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
