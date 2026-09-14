#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""run_close_lint.py — assert the run-close mutation actually landed.

The problem this closes (2026-07-16, an editor-plugin workdir inside a personal vault):
six sequential dispatched ``build-loop:build-orchestrator`` agents each completed
with a high-quality report and wrote NO durable run record — no ``state.json.runs[]``
entry, no retrospective, no milestone, no feedback line. The plugin workdir had no
``.build-loop/`` at all; the only ``runs[]`` rows in that checkout came from Stop hooks.
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
    review_owed exit 1  the run record landed, but `.build-loop/owed-verification.json`
                        still owes a verifier THIS run owns (a missing independent-auditor
                        verdict, or a cross-vendor round the review profile required). The
                        record exists and the review does not, so this is checked AFTER the
                        record statuses and never masks a missing record.
    review_owed_other_run exit 0  same, but the debt belongs to a DIFFERENT run. Named and
                        reported, not blocking: refusing an unrelated run's close only
                        teaches the operator to waive the debt to get unstuck.
    acceptance_incomplete exit 1  targeted selection exists, but its exact selected lanes
                        do not have bound, passing, evidence-backed result receipts.
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

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

STATE_RELPATH = Path(".build-loop") / "state.json"

# Statuses that mean "the run closed its record".
# ``review_owed_other_run`` is OK on purpose: the debt is real and named, but it
# belongs to a DIFFERENT run, and blocking this run's close for it only teaches
# the operator to reach for the waiver -- which is the path that reopens the
# defect the debt exists to close. A gate that cries wolf gets muted.
OK_STATUSES = ("recorded", "skipped", "review_owed_other_run")

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


def _owed_verification_gap(
    workdir: Path, reconcile: bool = True
) -> dict[str, Any] | None:
    """The owed-verification manifest when it still owes a verifier, else None.

    Deferred import: this lint runs in hook paths and must never RAISE. It must
    also never report CLEAN on a check it could not perform. A bare
    ``except: return None`` conflated the two: an unimportable module, a locked
    state file, a hand-edited manifest -- any exception at all -- became "no
    gap", and a non-advisory close exited 0 with its verification debt
    unreadable. Absence of evidence was being reported as evidence of absence,
    inside the one mechanism whose entire job is to refuse that substitution.

    An exception now yields an UNREADABLE gap: `--advisory` callers still exit 0
    (their own branch in `main`, unchanged, so hook paths stay fail-open), while
    a real close fails and names what could not be read.
    """
    try:
        import owed_verification  # noqa: WPS433
        # `reconcile=False` for an ADVISORY caller. Reconciling writes: it can
        # unlink the manifest, rewrite state.json, and append to the audit log.
        # A commit-boundary hook documented as read-only advisory must not be
        # able to delete a live verification debt as a side effect of reporting
        # on it -- both review rounds on 2026-09-13 flagged that the parameter
        # existed with no caller passing it.
        result = owed_verification.check_manifest(workdir, reconcile=reconcile)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "incomplete",
            "owed": ["unknown"],
            "debts": [],
            "owed_runs": {},
            "dispatch_commands": {},
            "unreadable": True,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not isinstance(result, dict):
        return {
            "status": "incomplete",
            "owed": ["unknown"],
            "debts": [],
            "owed_runs": {},
            "dispatch_commands": {},
            "unreadable": True,
            "error": f"check_manifest returned {type(result).__name__}, not a dict",
        }
    return result if result.get("status") == "incomplete" else None


def _waiver_commands(workdir: Path, debts: list[dict[str, Any]], owed: list[str]) -> str:
    """The last-resort waiver, SCOPED so the printed command actually runs.

    An unscoped `clear --verifier <name>` is refused whenever two runs owe that
    verifier -- which is the case this whole mechanism exists for -- so the
    remediation was emitting a command that exits 2. Same defect class as an
    un-dischargeable dispatch command, in the sibling file.
    """
    if debts:
        return " ; ".join(
            f"python3 scripts/owed_verification.py clear --workdir {shlex.quote(str(workdir))} "
            f"--verifier {shlex.quote(str(d.get('verifier')))} "
            f"--run-id {shlex.quote(str(d.get('run_id') or ''))} --reason \"<why>\""
            for d in debts
        )
    return (
        f"python3 scripts/owed_verification.py clear --workdir {shlex.quote(str(workdir))} "
        f"--verifier {shlex.quote(owed[0]) if owed else '<name>'} --reason \"<why>\""
    )


def _apply_owed_verification(
    workdir: Path, envelope: dict[str, Any], reconcile: bool = True
) -> dict[str, Any]:
    """Refuse a close whose review is still owed.

    Ordered AFTER the record statuses on purpose: a missing run record is the
    more fundamental failure and must not be relabelled as an owed review. Only
    an otherwise-passing envelope is downgraded.

    Scoped by OWNER. A debt this run owes BLOCKS its close (``review_owed``,
    exit 1). A debt another run owes is reported, named, and does NOT block
    (``review_owed_other_run``, exit 0) -- the diff is still un-audited and the
    reason says whose it is, but refusing an unrelated run's close only drives
    the operator to waive the debt to get unstuck, and the waiver is the path
    that reopens the escape. Report the gap to the party who can close it.
    """
    if envelope.get("status") not in OK_STATUSES:
        return envelope
    gap = _owed_verification_gap(workdir, reconcile=reconcile)
    if gap is None:
        return envelope
    if gap.get("unreadable"):
        # Not "another run's debt" and not waivable by run id: nobody knows
        # whose debt it is, because the check did not run. Blocks this close.
        envelope.update(
            status="review_owed",
            review_incomplete=True,
            owed=["unknown"],
            owed_run_id=None,
            owed_run_ids=[],
            reason=(
                "review completeness could not be determined: "
                f"{gap.get('error')}. An unreadable verification debt is not a "
                "discharged one."
            ),
            remediation=(
                "repair or inspect .build-loop/owed-verification.json and re-run "
                "`python3 scripts/owed_verification.py check --workdir . --json`; "
                "fix the underlying error rather than deleting the manifest"
            ),
        )
        return envelope
    owed = [str(v) for v in gap.get("owed") or []]
    this_run_id = str(envelope.get("run_id") or "")
    # Ownership comes from the per-debt rows, NOT the manifest's `run_id`. That
    # field is only the last writer's, so keying on it reported "another run's
    # debt" to the run that actually owed it and let it close at exit 0.
    # Ownership comes from the per-debt ROWS. `owed_runs` is a name-keyed view
    # where the last row wins, so two runs owing one verifier left only the
    # later named as owner -- and the earlier run was told the debt was someone
    # else's and closed at exit 0 with its diff un-reviewed. The name-keyed
    # views remain only as a fallback for a pre-upgrade manifest.
    debts = gap.get("debts")
    debts = [d for d in debts if isinstance(d, dict)] if isinstance(debts, list) else []
    owner_ids = {str(d.get("run_id") or "") for d in debts} - {""}
    if not owner_ids:
        owners = gap.get("owed_runs")
        owners = owners if isinstance(owners, dict) else {}
        owner_ids = {str(v) for v in owners.values() if str(v)}
    if not owner_ids:
        owner_ids = {str(gap.get("run_id") or "")} - {""}
    owned_by_this_run = (
        not owner_ids or not this_run_id or this_run_id in owner_ids
    )
    owed_run_id = ", ".join(sorted(owner_ids)) if owner_ids else ""
    # Shape-guard, not decoration: a hand-edited or malformed manifest can carry
    # a LIST here, and `.get` on it raises outside this function's caller's
    # handler -- which meant `--advisory` never reached its exit-0 branch and a
    # hook caller got a traceback instead of a JSON envelope.
    raw_commands = gap.get("dispatch_commands")
    commands = raw_commands if isinstance(raw_commands, dict) else {}
    # One line PER DEBT, each naming its own run and range. Printing the
    # name-keyed view showed one command for two debts -- always the later
    # run's range -- so following it audited the wrong diff.
    if debts:
        remediation = " ; ".join(
            f"{d.get('verifier')} (run {d.get('run_id') or '?'}, "
            f"{d.get('diff_range') or '?'}): "
            f"{d.get('dispatch_command') or 'dispatch ' + str(d.get('verifier'))}"
            for d in debts
        )
    else:
        remediation = " ; ".join(
            f"{name}: {commands.get(name, 'dispatch ' + name)}" for name in owed
        ) or "resolve .build-loop/owed-verification.json"
    envelope.update(
        status="review_owed" if owned_by_this_run else "review_owed_other_run",
        review_incomplete=True,
        owed=owed,
        # The OWNERS, matching what `reason` says. This field used to report the
        # manifest's last writer while the prose named someone else, so a JSON
        # consumer and a human reading the same envelope disagreed.
        # Single-valued, as it has always been; the full set is owed_run_ids.
        # Joining every owner into this field silently broke any consumer
        # comparing it to a run id.
        owed_run_id=(
            this_run_id if this_run_id in owner_ids
            else (sorted(owner_ids)[0] if owner_ids else gap.get("run_id"))
        ),
        owed_run_ids=sorted(owner_ids),
        reason=(
            f"review is not complete: {', '.join(owed) or 'a verifier'} still owed on "
            f"run {owed_run_id or '(unnamed)'!r} per {gap.get('manifest_path')}"
        ),
        # Run the verifier FIRST. The waiver is last and labelled, because a
        # remediation that leads with `clear` teaches the operator to waive.
        remediation=(
            f"{remediation}  "
            "# record the verdict, then re-run write_run_entry --scope build "
            "(a verdict in .build-loop/judge-decisions.json discharges the debt "
            "automatically).  LAST RESORT, only when no second vendor is "
            f"reachable: {_waiver_commands(workdir, debts, owed)}"
        ),
    )
    return envelope


def _apply_acceptance_results(workdir: Path, envelope: dict[str, Any]) -> dict[str, Any]:
    """Require execution receipts when this run activated targeted selection."""
    if envelope.get("status") not in OK_STATUSES:
        return envelope
    selection_path = workdir / ".build-loop" / "acceptance-selection.json"
    files_touched = [
        str(item) for item in envelope.get("files_touched", []) if isinstance(item, str)
    ]
    if not selection_path.exists():
        non_code_suffixes = {".md", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf"}
        code_touched = [
            path for path in files_touched
            if not path.startswith("docs/") and Path(path).suffix.lower() not in non_code_suffixes
        ]
        if not code_touched:
            return envelope
        envelope.update(
            status="acceptance_incomplete",
            acceptance_complete=False,
            acceptance_errors=["code-touching run has no acceptance selection"],
            reason="code changed without a risk-targeted acceptance selection",
            remediation=(
                "run scripts/acceptance_selector.py with the closing run id and exact "
                "filesTouched set, then execute and receipt the selected lanes"
            ),
        )
        return envelope
    try:
        import acceptance_selector  # noqa: WPS433
        verdict = acceptance_selector.verify_results(
            workdir,
            expected_run_id=str(envelope.get("run_id") or "") or None,
            expected_changed_files=files_touched or None,
        )
    except Exception as exc:  # noqa: BLE001
        verdict = {"verdict": "fail", "errors": [f"{type(exc).__name__}: {exc}"]}
    if verdict.get("verdict") == "pass":
        envelope["acceptance_complete"] = True
        envelope["acceptance_selected_ids"] = verdict.get("selected_ids", [])
        return envelope
    envelope.update(
        status="acceptance_incomplete",
        acceptance_complete=False,
        acceptance_errors=verdict.get("errors", ["acceptance result verification failed"]),
        reason=(
            "targeted acceptance selection exists without matching passing execution "
            "receipts; a selection plan is not test evidence"
        ),
        remediation=(
            "execute every lane in .build-loop/acceptance-selection.json, write "
            ".build-loop/acceptance-results.json per references/targeted-acceptance.md, "
            "then run `python3 scripts/acceptance_selector.py --workdir . "
            "--verify-results --compact`"
        ),
    )
    return envelope


def check(
    workdir: Path,
    run_id: str | None = None,
    recent_minutes: int | None = None,
    require_orchestrator: bool = False,
    require_learn: bool = False,
    now: datetime | None = None,
    advisory: bool = False,
) -> dict[str, Any]:
    """Assert a run-close record exists AND its review is not still owed.

    Thin wrapper: ``_check_record`` answers the record question, then
    ``_apply_owed_verification`` downgrades an otherwise-passing envelope whose
    owed-verification manifest is still incomplete.

    ``advisory`` makes the owed-verification read NON-MUTATING. An advisory
    caller is a hook reporting on state, and a report must not change what it
    reports on.
    """
    envelope = _check_record(
        workdir,
        run_id=run_id,
        recent_minutes=recent_minutes,
        require_orchestrator=require_orchestrator,
        require_learn=require_learn,
        now=now,
    )
    resolved_workdir = Path(workdir).resolve()
    envelope = _apply_acceptance_results(resolved_workdir, envelope)
    return _apply_owed_verification(resolved_workdir, envelope, reconcile=not advisory)


def _check_record(
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
            # LAST match, not first. The cause is now historical: write_run_entry
            # used to blind-append when the existing row was already
            # orchestrator-grade, so a run_id could own two rows. It upserts as of
            # 2026-09-12, but ledgers written before that still carry duplicates
            # (repair with scripts/dedupe_run_ledger.py), and on a single row
            # last-match and first-match are the same row. learn/runner.py's
            # _canonical_run() is the writer half of this contract — move one and
            # you must move the other, or Learn stamps a row this never reads.
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
            files_touched=matches[-1].get("filesTouched", []),
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
            files_touched=fresh[-1].get("filesTouched", []),
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
        advisory=bool(args.advisory),
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
