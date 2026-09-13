#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""owed_verification.py — the owed-verification manifest for GAP-1.

WHY
---
A nested / background ``build-orchestrator`` has no Agent tool, so it cannot
dispatch its own Fable-tier verification layer (``plan-critic``,
``independent-auditor``, ``security-reviewer``).  The GAP-1 "auditor dispatch
ladder" already records this honestly in ``auditor_status`` as
``not-run:parent-must-dispatch`` and documents (in prose) that the DISPATCHING
PARENT then owes the audit.  Prose is a memory, not a mechanism: nothing forced
the parent to actually run the owed verifiers, so a run could close having
silently skipped the verdicts the org depends on.

This script turns that prose contract into a machine-checkable MANIFEST.  When
a nested orchestrator reaches Review with verifiers un-run, it ``write``s the
owed list to ``.build-loop/owed-verification.json`` and flips
``state.json.review_incomplete = true``.  Sub-step G surfaces the manifest as a
non-optional "PARENT MUST DISPATCH" block.  The parent (which HAS the Agent
tool) dispatches each owed verifier, then ``clear``s it; when the last owed
verifier is cleared, the flag flips back to ``false`` and the manifest is
removed.  ``check`` answers "is this run's review complete?" for any caller.

THE SECOND DEBT
---------------
``scripts/review_trigger.py`` computes ``cross_vendor_required`` -- whether the
diff needs a review round from a DIFFERENT vendor -- and for a long time nothing
owed that round back, so it degraded to an advisory note a busy agent skipped in
good faith while the run still closed review-complete.  ``cross-vendor-audit``
is therefore a first-class debt with the same lifecycle as the auditor: armed by
``enforce_for_run_record`` at the same point, discharged only by a rendered
second-vendor verdict in ``judge_decisions[]`` (a same-vendor auditor verdict
does NOT discharge it), and refused by the run-close gate until then.  Measured
on bl-20260912T180923Z-claude_code-selfmodrevert: skipping the round hid 11
findings, 6 Critical, disjoint from the same-vendor auditor's own 11.

The manifest is a plain JSON file so anything — a script, a human, another
agent — can inspect it without depending on this CLI.

CLI
---

::

    owed_verification.py write  --workdir <repo> --run-id <id>
                                --diff-range <base>..<head>
                                --owe independent-auditor [--owe plan-critic ...]
                                [--chunk-id <id>] [--reason "<text>"]
                                [--written-by <label>]
                                [--dispatch-command verifier=<cmd> ...]
                                [--json]

    owed_verification.py check  --workdir <repo> [--json]

    owed_verification.py clear  --workdir <repo>
                                (--verifier <name> ... | --all)
                                [--reason "<text>"] [--json]

Exit codes
----------

- ``write`` — 0 on success (manifest written / refreshed).
- ``check`` — 0 when review is COMPLETE (nothing owed) or NO manifest exists;
              1 when review is INCOMPLETE (verifiers still owed).  This lets a
              parent gate on ``owed_verification.py check && proceed``.
- ``clear`` — 0 on success (verifier(s) cleared, whether or not any remain).
- 2 on argument errors (handled by argparse).

Importable surface
------------------

- ``load_manifest(workdir) -> dict | None``
- ``write_manifest(workdir, *, run_id, diff_range, owed, ...) -> Path``
- ``check_manifest(workdir) -> dict``
- ``clear_verifiers(workdir, *, verifiers=None, clear_all=False, ...) -> dict``
- ``owed_verifiers_for_record(record) -> dict[verifier, reason]``
- ``cross_vendor_reason_for_record(record) -> str | None``
- ``enforce_for_run_record(workdir, record, *, written_by, diff_range)``
- ``MANIFEST_RELPATH`` / ``STATE_RELPATH`` / ``MANAGED_VERIFIERS``
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Sibling modules (`review_trigger`, the `write_run_entry` package) are imported
# lazily inside the enforcement path, which is fail-open -- so an unimportable
# sibling would silently disarm the debt instead of raising. Putting this file's
# own directory on the path makes the import work no matter which caller loaded
# the module, so fail-open never has to cover a resolvable import.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MANIFEST_RELPATH = Path(".build-loop") / "owed-verification.json"
STATE_RELPATH = Path(".build-loop") / "state.json"
LOG_RELPATH = Path(".build-loop") / "audit-log.md"

# Canonical verifiers the parent may owe, each with a default dispatch command
# template.  ``{range}`` / ``{plan}`` are filled from the manifest at write time
# so the emitted command is copy-paste runnable by the parent.  A caller can
# override any command via ``--dispatch-command verifier=<cmd>``.
KNOWN_VERIFIERS: dict[str, str] = {
    "independent-auditor": (
        'Agent(subagent_type="build-loop:independent-auditor", '
        'prompt="audit {range} at build scope; append the verdict to '
        '.build-loop/judge-decisions.json")'
    ),
    "plan-critic": (
        'Agent(subagent_type="build-loop:plan-critic", '
        'prompt="critique the Phase 2 plan for {range}; append the verdict to '
        '.build-loop/judge-decisions.json")'
    ),
    "security-reviewer": (
        'Agent(subagent_type="build-loop:security-reviewer", '
        'prompt="adversarial security review of {range}; append findings to '
        '.build-loop/judge-decisions.json")'
    ),
    "scope-auditor": (
        'Agent(subagent_type="build-loop:scope-auditor", '
        'prompt="Plan→Execute boundary check on {range}")'
    ),
    # Second-vendor round. NOT an Agent(...) dispatch: the point is a different
    # vendor's model, which the host's own Agent tool cannot reach. `< /dev/null`
    # is mandatory — `codex exec` blocks forever on an inherited stdin when run
    # non-interactively. The framing is defensive ("find defects a same-vendor
    # reviewer would miss"), because Codex refuses an explicitly adversarial
    # audit brief and still exits 0, which reads as a clean round.
    "cross-vendor-audit": (
        'codex exec "Review the diff {range} for defects a same-vendor reviewer '
        'would miss: destructive or irreversible paths, silent failure modes, '
        'unowned edge cases. Report file:line evidence per finding." < /dev/null'
    ),
}


# ---------------------------------------------------------------------------
# Time / IO helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic-ish write so a concurrent reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{int(time.time() * 1000)}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def _manifest_lock(workdir: Path):
    """Serialise the manifest's read-modify-WRITE, not just each write.

    An atomic replace makes a single write safe; it does nothing for a
    transaction. Without this, a `clear` that read the last owed verifier can
    `unlink()` a manifest another writer added a verifier to microseconds
    earlier, and report the review complete. Degrades to a no-op lock when
    `atomic_io` is unreachable (outside the repo, where no peer writer exists
    to race).
    """
    path = workdir / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from atomic_io import LockedFile  # noqa: WPS433
    except Exception:  # noqa: BLE001
        yield
        return
    try:
        with LockedFile(path):
            yield
    except Exception:  # noqa: BLE001 — a lock timeout must not wedge the run
        yield


def _log(workdir: Path, line: str) -> None:
    """Best-effort audit-log line; never fatal."""
    try:
        log = workdir / LOG_RELPATH
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"- {_utcnow_iso()} owed_verification {line}\n")
    except OSError:
        pass


def _dedupe(items: Iterable[str]) -> list[str]:
    """Order-preserving de-duplication of non-empty stripped tokens."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        tok = str(raw).strip()
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


# ---------------------------------------------------------------------------
# state.json mirror flag (best-effort, never authoritative)
# ---------------------------------------------------------------------------


def _mutate_state(workdir: Path, mutate) -> bool:
    """Read-modify-write state.json under the repo's file lock.

    state.json has FOUR writers (write_run_entry, append_run, learn, closeout).
    An unlocked read-modify-write here can silently drop a concurrent writer's
    keys, and this module now touches state.json twice per run rather than once.
    ``atomic_io.LockedFile`` is the repo's single-writer contract, so this reuses
    it rather than inventing a second one; if it cannot be imported the write
    still happens unlocked, because losing the flag entirely is worse than a
    narrow race.

    Best-effort throughout: the MANIFEST is authoritative. An absent or
    unparseable state.json is skipped rather than clobbered.
    """
    state_path = workdir / STATE_RELPATH

    def _apply() -> bool:
        data = _read_json(state_path)
        if not isinstance(data, dict):
            return False
        mutate(data)
        try:
            _atomic_write_json(state_path, data)
        except OSError:
            return False
        return True

    try:
        from atomic_io import LockedFile  # noqa: WPS433
    except Exception:  # noqa: BLE001
        # No lock module reachable means no other writer is honouring one
        # either (they all import it), so an unlocked write races nothing.
        return _apply()
    try:
        with LockedFile(state_path):
            return _apply()
    except Exception:  # noqa: BLE001
        # A lock TIMEOUT means another writer holds state.json right now.
        # Writing anyway would overwrite its keys with this process's stale
        # snapshot, so the mirror is skipped. The MANIFEST is authoritative;
        # a missing mirror flag costs an advisory signal, a clobbered
        # state.json costs another writer's evidence.
        return False


def _set_state_flag(workdir: Path, value: bool) -> bool:
    """Mirror ``review_incomplete`` onto state.json. Returns True iff written."""

    def _mutate(data: dict[str, Any]) -> None:
        data["review_incomplete"] = value

    return _mutate_state(workdir, _mutate)


# ---------------------------------------------------------------------------
# Manifest primitives
# ---------------------------------------------------------------------------


def load_manifest(workdir: Path) -> dict[str, Any] | None:
    """Return the parsed manifest, or None if absent / unreadable."""
    path = workdir / MANIFEST_RELPATH
    if not path.exists():
        return None
    data = _read_json(path)
    if not isinstance(data, dict):
        # Present but unparseable — surface as an active manifest with an
        # unknown owed set so the run is treated as INCOMPLETE (fail safe:
        # prefer one extra audit to silently closing an un-reviewed run).
        return {
            "run_id": None,
            "diff_range": None,
            "owed": ["unknown"],
            "cleared": [],
            "status": "incomplete",
            "_malformed": True,
        }
    return data


def _dispatch_commands(
    owed: list[str],
    diff_range: str,
    plan_path: str | None,
    overrides: dict[str, str],
) -> dict[str, str]:
    """Build the copy-paste dispatch command per owed verifier."""
    out: dict[str, str] = {}
    for name in owed:
        if name in overrides:
            out[name] = overrides[name]
            continue
        template = KNOWN_VERIFIERS.get(name)
        if template is None:
            out[name] = (
                f'Agent(subagent_type="build-loop:{name}", '
                f'prompt="run {name} on {diff_range}")'
            )
            continue
        out[name] = template.format(
            range=diff_range,
            plan=plan_path or ".build-loop/plans/<active-plan>.md",
        )
    return out


def _write_manifest_unlocked(
    workdir: Path,
    *,
    run_id: str,
    diff_range: str,
    owed: Iterable[str],
    chunk_id: str | None = None,
    reason: str | None = None,
    reasons: dict[str, str] | None = None,
    written_by: str = "nested-orchestrator",
    plan_path: str | None = None,
    dispatch_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create/refresh the owed-verification manifest.

    Merges with any existing manifest so a second ``write`` in the same run
    (e.g. a later chunk owing another verifier) accumulates rather than
    clobbers, and never re-adds an already-cleared verifier.  Flips
    ``state.json.review_incomplete = true`` when anything is owed.

    Returns the written manifest dict (plus a ``_state_updated`` key).
    """
    owed_list = _dedupe(owed)
    incoming = set(owed_list)
    existing = load_manifest(workdir)
    cleared: list[str] = []
    owed_runs: dict[str, str] = {}
    prior_run_id = ""
    if isinstance(existing, dict) and not existing.get("_malformed"):
        prior_run_id = str(existing.get("run_id") or "")
        prior_owed = existing.get("owed")
        if isinstance(prior_owed, list):
            owed_list = _dedupe([*prior_owed, *owed_list])
        prior_owner_map = existing.get("owed_runs")
        if isinstance(prior_owner_map, dict):
            owed_runs = {str(k): str(v) for k, v in prior_owner_map.items()}
        # A debt inherited from an EARLIER manifest keeps that manifest's
        # run_id as its owner. Without this the merge below reassigns the whole
        # manifest to the newest run, and a low-risk run's own verdict then
        # discharges an unrelated run's outstanding audit -- the earlier diff
        # ships unreviewed. Ownership is what makes "whose debt is this?"
        # answerable after the merge.
        for name in prior_owed if isinstance(prior_owed, list) else []:
            owed_runs.setdefault(str(name), prior_run_id or str(run_id))
        prior_cleared = existing.get("cleared")
        if isinstance(prior_cleared, list):
            cleared = _dedupe(prior_cleared)
    for name in incoming:
        owed_runs[name] = str(run_id)

    # A verifier already cleared must not silently re-appear as owed.
    remaining = [v for v in owed_list if v not in cleared]

    overrides = dispatch_overrides or {}
    payload: dict[str, Any] = {
        "run_id": run_id,
        "chunk_id": chunk_id,
        "diff_range": diff_range,
        "owed": remaining,
        "cleared": cleared,
        "dispatch_commands": _dispatch_commands(remaining, diff_range, plan_path, overrides),
        "owed_runs": {k: v for k, v in owed_runs.items() if k in remaining},
        "written_by": written_by,
        "written_at": _utcnow_iso(),
        "status": "incomplete" if remaining else "complete",
    }
    if reason:
        payload["reason"] = reason
    # Per-verifier reasons. A manifest can now owe MORE THAN ONE debt with
    # different causes (a missing auditor verdict and a missing second-vendor
    # round are different failures), and one joined `reason` string cannot say
    # which verifier each clause belongs to.
    merged_reasons: dict[str, str] = {}
    if isinstance(existing, dict) and isinstance(existing.get("reasons"), dict):
        merged_reasons.update({str(k): str(v) for k, v in existing["reasons"].items()})
    merged_reasons.update({str(k): str(v) for k, v in (reasons or {}).items()})
    merged_reasons = {k: v for k, v in merged_reasons.items() if k in remaining}
    if merged_reasons:
        payload["reasons"] = merged_reasons

    _atomic_write_json(workdir / MANIFEST_RELPATH, payload)
    state_updated = _set_state_flag(workdir, bool(remaining))
    _log(workdir, f"write run={run_id} owed={','.join(remaining) or 'none'}")
    payload["_state_updated"] = state_updated
    return payload


def write_manifest(workdir: Path, **kwargs: Any) -> dict[str, Any]:
    """Locked entry point for the manifest read-modify-write. See the body below."""
    with _manifest_lock(workdir):
        return _write_manifest_unlocked(workdir, **kwargs)


def clear_verifiers(workdir: Path, **kwargs: Any) -> dict[str, Any]:
    """Locked entry point for the clear transaction. See the body below."""
    with _manifest_lock(workdir):
        return _clear_verifiers_unlocked(workdir, **kwargs)


AUTO_OWED_VERIFIER = "independent-auditor"
CROSS_VENDOR_VERIFIER = "cross-vendor-audit"

# The verifiers this module ARMS and DISCHARGES on its own from a run record.
# Every other name in KNOWN_VERIFIERS is armed only by an explicit ``write``
# call, so the auto-discharge below must never touch one.
MANAGED_VERIFIERS = (AUTO_OWED_VERIFIER, CROSS_VENDOR_VERIFIER)

# Where a cleared MANAGED debt is remembered, keyed by run_id. The manifest is
# DELETED when its last verifier clears, taking the ``cleared`` list with it --
# so without this the very next run-record write re-arms a debt the operator
# just discharged, and a run with no reachable peer host could never close.
CLEARED_STATE_KEY = "review_cleared_verifiers"
_CLEARED_RUN_CAP = 20

# Auditor statuses that SAY the auditor did not render a verdict. The
# orchestrator contract already requires the manifest on exactly these, which
# is why they are the first trigger: the branch was recorded honestly and the
# manifest still never appeared.
_NOT_RUN_PREFIXES = ("not-run:",)
_NOT_RUN_EXACT = {"cross-vendor-deferred"}


def owed_reason_for_record(record: dict[str, Any]) -> str | None:
    """Why this run record owes an independent-auditor verdict, or None.

    Three triggers, each one a real run that closed owing nothing on disk
    (RossLabs-AI-Assistant/.build-loop/state.json, 2026-07-21 .. 2026-08-04):

    1. ``auditor_status`` names a not-run branch. The contract calls the
       manifest MANDATORY here; prose cannot enforce itself.
    2. The auditor appears in ``judge_decisions[]`` with no rendered verdict --
       an emitted packet, which is a request for a verdict, not one.
    3. The run touched files and carries no auditor verdict at all.

    Deliberately NOT a blanket "no verdict means owed": a run that touched
    nothing and never engaged an auditor is not an escaped review, and owing on
    it would set ``review_incomplete`` on nearly every run. A flag that is
    always on is a flag nobody reads.
    """
    status = str(record.get("auditor_status") or "").strip().lower()
    if status.startswith(_NOT_RUN_PREFIXES) or status in _NOT_RUN_EXACT:
        return f"auditor_status={record.get('auditor_status')!r} names a not-run branch"

    try:
        from write_run_entry.validators import AUDITOR_JUDGE_MARKER, auditor_present
    except Exception:  # noqa: BLE001 — enforcement must never break the write
        return None

    if auditor_present(record.get("judge_decisions")):
        return None

    decisions = record.get("judge_decisions")
    if isinstance(decisions, list):
        for item in decisions:
            if isinstance(item, dict) and AUDITOR_JUDGE_MARKER in str(item.get("judge_id", "")):
                return (
                    "judge_decisions[] names the auditor but carries no rendered "
                    "verdict (an emitted packet is a request for one)"
                )

    if record.get("filesTouched"):
        return "the run touched files and recorded no independent-auditor verdict"
    return None


def _explicit_cross_vendor_flag(record: dict[str, Any]) -> bool | None:
    """The ``cross_vendor_required`` verdict the run RECORDED, or None.

    Precedence over re-derivation in both directions: a recorded ``true`` arms
    the debt without re-deriving, and a recorded ``false`` suppresses it. The
    recorded value came from running ``review_trigger.py`` on the REAL diff,
    which is strictly better evidence than the reconstruction below, whose only
    inputs are the file names the record kept.
    """
    for container in (record.get("review_trigger"), record.get("reviewTrigger"), record):
        if isinstance(container, dict) and isinstance(
            container.get("cross_vendor_required"), bool
        ):
            return bool(container["cross_vendor_required"])
    return None


def _git_loc_delta(workdir: Path, diff_range: str) -> int | None:
    """Absolute line delta for the run, from git, or None.

    Needed because the review profile's single strongest cross-vendor signal is
    `large_diff` (>=200 lines) and NO run record carries a line count: the
    closing writers record file NAMES only. Re-deriving from names alone missed
    exactly the case this debt was built for -- the measured 1282-line
    self-mod-revert diff whose file names carried no risk keyword.

    Range resolution mirrors `--files-touched-from-git`: the caller's range when
    it has one, else `state.json.preBuildSha..HEAD`. Bounded and fail-open; a
    missing sha, a rebase, or no git at all returns None and the derivation
    simply proceeds without the signal.
    """
    import subprocess  # noqa: WPS433 (deferred; enforcement is fail-open)

    rng = diff_range if diff_range and diff_range != "unknown" else ""
    if not rng:
        data = _read_json(workdir / STATE_RELPATH)
        pre = data.get("preBuildSha") if isinstance(data, dict) else None
        if not pre:
            return None
        rng = f"{pre}..HEAD"
    try:
        out = subprocess.run(
            ["git", "-C", str(workdir), "diff", "--numstat", rng],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except Exception:  # noqa: BLE001
        return None
    if out.returncode != 0:
        return None
    total = 0
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        for token in parts[:2]:
            if token.isdigit():
                total += int(token)
    return total or None


def _derived_cross_vendor_flag(
    record: dict[str, Any],
    workdir: Path | None = None,
    diff_range: str = "unknown",
) -> tuple[bool, list[str]]:
    """Re-derive the review profile from the record's own evidence.

    The same ``scripts/review_trigger.py`` the Review-A contract already calls,
    fed the record's ``filesTouched`` plus any ``triggers`` block, so the debt
    and the documented requirement can never disagree by construction. Returns
    ``(required, reasons)``; fail-open to ``(False, [])`` on any import or
    profiling error, because a run record must still land.
    """
    files = [str(f) for f in (record.get("filesTouched") or []) if str(f).strip()]
    if not files:
        return False, []
    try:
        import review_trigger  # noqa: WPS433 (deferred; enforcement is fail-open)
    except Exception:  # noqa: BLE001
        return False, []
    context: dict[str, Any] = {}
    triggers = record.get("triggers")
    if isinstance(triggers, dict):
        context.update(triggers)
    for key in ("loc_delta", "lines_changed", "changed_lines", "net_loc"):
        value = record.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            context["lines_changed"] = abs(value)
            break
    else:
        if workdir is not None:
            measured = _git_loc_delta(workdir, diff_range)
            if measured is not None:
                context["lines_changed"] = measured
    try:
        profile = review_trigger.build_profile(context, files)
    except Exception:  # noqa: BLE001
        return False, []
    return bool(profile.get("cross_vendor_required")), [
        str(r) for r in (profile.get("reasons") or [])
    ]


def cross_vendor_reason_for_record(
    record: dict[str, Any],
    workdir: Path | None = None,
    diff_range: str = "unknown",
) -> str | None:
    """Why this run owes a SECOND-VENDOR review round, or None.

    ``scripts/review_trigger.py`` has computed ``cross_vendor_required`` since
    QM v0.13.0 and the orchestrator prose has told the agent to run the round.
    Nothing tracked the requirement, so a busy agent skipped it in good faith
    and the run still read review-complete. Measured on run
    bl-20260912T180923Z-claude_code-selfmodrevert: the trigger returned true for
    a 1282-line diff, the round was skipped, the run reported clean, and running
    the round afterwards returned 11 findings (6 Critical) DISJOINT from the
    same-vendor auditor's 11 -- one of them a live path that globbed a caller
    pathspec and destroyed two files' uncommitted edits.

    Same shape as ``owed_reason_for_record``: a rendered second-vendor verdict
    discharges it, nothing else does. A same-vendor ``independent-auditor``
    verdict deliberately does NOT, because the measured value of the round was
    precisely the findings the same-vendor auditor did not reach.

    Cost note, measured before shipping: re-deriving over this repo's own 28
    files-touching historical runs arms the debt on 17 of them (61%). That is
    the documented policy's own trigger rate, not an inflation of it -- the
    profile already said those runs required the round. Discharge is a recorded
    verdict; when no peer host is reachable, ``clear --verifier
    cross-vendor-audit --reason "<why>"`` records the skip in the audit log
    rather than hiding it.
    """
    try:
        from write_run_entry.validators import cross_vendor_present
    except Exception:  # noqa: BLE001 — enforcement must never break the write
        return None

    if cross_vendor_present(record.get("judge_decisions")):
        return None

    explicit = _explicit_cross_vendor_flag(record)
    if explicit is False:
        return None
    if explicit is True:
        return (
            "the run recorded review_trigger cross_vendor_required=true and "
            "judge_decisions[] carries no second-vendor verdict"
        )

    required, reasons = _derived_cross_vendor_flag(record, workdir, diff_range)
    if not required:
        return None
    signals = ", ".join(reasons[:6]) or "a high-risk signal"
    return (
        "review_trigger re-derived cross_vendor_required=true from the run's own "
        f"filesTouched ({signals}) and judge_decisions[] carries no second-vendor verdict"
    )


def owed_verifiers_for_record(
    record: dict[str, Any],
    workdir: Path | None = None,
    diff_range: str = "unknown",
) -> dict[str, str]:
    """The MANAGED verifiers this run record owes, mapped to why.

    One function so the two debts share a lifecycle instead of the auditor
    having a mechanism and the cross-vendor round having a paragraph.
    """
    owed: dict[str, str] = {}
    auditor = owed_reason_for_record(record)
    if auditor:
        owed[AUTO_OWED_VERIFIER] = auditor
    cross_vendor = cross_vendor_reason_for_record(record, workdir, diff_range)
    if cross_vendor:
        owed[CROSS_VENDOR_VERIFIER] = cross_vendor
    return owed


def _persisted_record(workdir: Path, run_id: str) -> dict[str, Any] | None:
    """The run's row as state.json actually holds it, or None.

    Both writers UPSERT-MERGE the incoming entry onto the stored row and then
    hand enforcement the INCOMING entry, which is a strictly thinner view. A
    goal-only correction that restates no file set and no verdicts therefore
    looked like a run that touched nothing and owed nothing, and cleared live
    debts on a diff still carrying `auth.py`. Enforcement reads the row that
    was actually persisted so a caller cannot discharge an audit by omission.
    """
    if not run_id or run_id == "unknown":
        return None
    data = _read_json(workdir / STATE_RELPATH)
    if not isinstance(data, dict):
        return None
    runs = data.get("runs")
    if not isinstance(runs, list):
        return None
    for row in reversed(runs):
        if isinstance(row, dict) and row.get("run_id") == run_id:
            return row
    return None

def _host_dispatch_overrides(record: dict[str, Any], diff_range: str) -> dict[str, str]:
    """Keep the emitted cross-vendor command off the run's OWN vendor.

    The default template is `codex exec`, which on a run whose `host` is already
    `codex` would have the same vendor review itself -- a second opinion that
    shares the first one's blind spots is the thing this debt exists to prevent.
    """
    host = str(record.get("host") or "").strip().lower()
    if host != "codex":
        return {}
    return {
        CROSS_VENDOR_VERIFIER: (
            f"# this run's host IS codex -- dispatch the round on a DIFFERENT vendor.\n"
            f'# Claude Code: Agent(subagent_type="build-loop:independent-auditor", '
            f'prompt="second-vendor review of {diff_range}; write the verdict to '
            f'.build-loop/judge-decisions.json with judge_id cross-vendor-audit")\n'
            f"# or hand the range to a peer session over the rally channel."
        )
    }

def _cleared_for_run(workdir: Path, run_id: str) -> set[str]:
    """MANAGED verifiers already discharged for this run_id (see CLEARED_STATE_KEY)."""
    data = _read_json(workdir / STATE_RELPATH)
    if not isinstance(data, dict):
        return set()
    registry = data.get(CLEARED_STATE_KEY)
    if not isinstance(registry, dict):
        return set()
    entry = registry.get(run_id)
    if not isinstance(entry, dict):
        return set()
    return {str(name) for name in entry}


def _record_cleared(workdir: Path, run_id: str, verifiers: Iterable[str], reason: str | None) -> bool:
    """Remember a discharged MANAGED verifier so the next write cannot re-arm it."""
    names = [v for v in _dedupe(verifiers) if v in MANAGED_VERIFIERS]
    if not names or not run_id:
        return False
    def _mutate(data: dict[str, Any]) -> None:
        registry = data.get(CLEARED_STATE_KEY)
        if not isinstance(registry, dict):
            registry = {}
        entry = registry.get(run_id)
        if not isinstance(entry, dict):
            entry = {}
        for name in names:
            entry[name] = {"at": _utcnow_iso(), "reason": reason or ""}
        registry[run_id] = entry
        # Bounded: this is a tombstone, not a ledger. Keep the newest N run ids.
        if len(registry) > _CLEARED_RUN_CAP:
            registry = dict(list(registry.items())[-_CLEARED_RUN_CAP:])
        data[CLEARED_STATE_KEY] = registry

    return _mutate_state(workdir, _mutate)


def enforce_for_run_record(
    workdir: Path,
    record: dict[str, Any],
    *,
    written_by: str,
    diff_range: str = "unknown",
) -> dict[str, Any] | None:
    """Write the owed manifest when a closing run record lacks a required verdict.

    This is the whole GAP-1 closure. ``owed_verification.py`` was correct and
    complete and had ZERO executable call sites: every reference to it lived in
    markdown an agent had to remember to obey, so the escape hatch never fired
    even on runs that explicitly recorded ``not-run:parent-must-dispatch``.

    Making the manifest a side effect of the same write that persists the run
    record is what removes the remembering. A run now closes with the required
    verdicts or with a manifest naming what is owed; it can no longer close with
    neither, because the code path that writes one also writes the other.

    Two debts share this lifecycle: ``independent-auditor`` (a missing same-
    vendor verdict) and ``cross-vendor-audit`` (a diff the review profile says
    needs a second vendor). Both are armed here, both are discharged here the
    moment the record carries their verdict.

    Fail-open by construction: a run record must still land even if this
    cannot. Returns the manifest when written, else None.
    """
    try:
        run_id = str(record.get("run_id") or "unknown")
        record = _persisted_record(workdir, run_id) or record
        owed = owed_verifiers_for_record(record, workdir, diff_range)
        for name in _cleared_for_run(workdir, run_id):
            owed.pop(name, None)

        # DISCHARGE. A MANAGED verifier that THIS run owes and that the record
        # now satisfies is cleared here, so recording a verdict is what closes
        # the debt -- no second step for an agent to forget.
        #
        # Ownership, not manifest identity, is the scope. A manifest accumulates
        # debts across runs and its `run_id` is simply the last writer's, so
        # keying on that let a low-risk run's own verdict discharge an earlier
        # run's outstanding cross-vendor round and ship that diff unreviewed.
        # `owed_runs` records who owes what; a verifier with no recorded owner
        # is left alone rather than assumed to be this run's.
        manifest = load_manifest(workdir)
        if isinstance(manifest, dict) and not manifest.get("_malformed"):
            owners = manifest.get("owed_runs")
            owners = owners if isinstance(owners, dict) else {}
            on_manifest = [
                str(v) for v in (manifest.get("owed") or [])
                if str(v) in MANAGED_VERIFIERS and str(owners.get(str(v), "")) == run_id
            ]
            satisfied = [v for v in on_manifest if v not in owed]
            if satisfied:
                clear_verifiers(
                    workdir,
                    verifiers=satisfied,
                    reason=f"verdict recorded on run record ({written_by})",
                    record_tombstone=False,
                )

        if not owed:
            return None
        return write_manifest(
            workdir,
            run_id=run_id,
            diff_range=diff_range,
            owed=list(owed),
            reason="; ".join(f"{name}: {why}" for name, why in owed.items()),
            reasons=dict(owed),
            written_by=written_by,
            dispatch_overrides=_host_dispatch_overrides(record, diff_range),
        )
    except Exception:  # noqa: BLE001 — never break the run-record write
        return None


def check_manifest(workdir: Path) -> dict[str, Any]:
    """Answer 'is this run's review complete?'.

    Returns a dict with ``status`` ∈ {``complete``, ``incomplete``, ``absent``},
    the remaining ``owed`` list, ``cleared`` list, and ``review_incomplete``
    (the boolean a gate keys on).
    """
    manifest = load_manifest(workdir)
    if manifest is None:
        return {
            "status": "absent",
            "owed": [],
            "cleared": [],
            "run_id": None,
            "diff_range": None,
            "review_incomplete": False,
            "manifest_path": str(workdir / MANIFEST_RELPATH),
        }
    owed = manifest.get("owed") if isinstance(manifest.get("owed"), list) else []
    cleared = manifest.get("cleared") if isinstance(manifest.get("cleared"), list) else []
    remaining = [str(v) for v in owed]
    incomplete = bool(remaining)
    return {
        "status": "incomplete" if incomplete else "complete",
        "owed": remaining,
        "cleared": [str(v) for v in cleared],
        "run_id": manifest.get("run_id"),
        "diff_range": manifest.get("diff_range"),
        "dispatch_commands": manifest.get("dispatch_commands", {}),
        "review_incomplete": incomplete,
        "malformed": bool(manifest.get("_malformed")),
        "manifest_path": str(workdir / MANIFEST_RELPATH),
    }


def _clear_verifiers_unlocked(
    workdir: Path,
    *,
    verifiers: Iterable[str] | None = None,
    clear_all: bool = False,
    reason: str | None = None,
    record_tombstone: bool = True,
) -> dict[str, Any]:
    """Mark owed verifier(s) as dispatched-and-cleared by the parent.

    When the last owed verifier is cleared the manifest is REMOVED and
    ``state.json.review_incomplete`` flips to ``false``.  Idempotent: clearing
    an already-cleared or unknown verifier is a no-op for that name.

    Returns ``{"action", "cleared", "remaining", "status", "state_updated",
    "manifest_removed"}``.
    """
    manifest = load_manifest(workdir)
    if manifest is None:
        return {
            "action": "noop_absent",
            "cleared": [],
            "remaining": [],
            "status": "absent",
            "state_updated": False,
            "manifest_removed": False,
        }

    owed = list(manifest.get("owed") or []) if isinstance(manifest.get("owed"), list) else []
    already_cleared = (
        list(manifest.get("cleared") or []) if isinstance(manifest.get("cleared"), list) else []
    )

    to_clear = list(owed) if clear_all else _dedupe(verifiers or [])
    newly_cleared = [v for v in to_clear if v in owed]
    remaining = [v for v in owed if v not in newly_cleared]
    cleared_total = _dedupe([*already_cleared, *newly_cleared])

    # Remember the discharge BEFORE the manifest is touched, but ONLY for a
    # MANUAL clear. A manual clear is a waiver -- "no peer host could run this
    # round" -- and must survive the manifest deletion that a full clear
    # performs, or the next run-record write re-arms it and a machine with no
    # second vendor can never close a run.
    #
    # A VERDICT-based discharge must NOT leave one. The verdict already
    # suppresses re-arming on its own, and a tombstone outlives the verdict:
    # when a later correction expands the run's file set, `upsert_merge` drops
    # the now-stale verdict (it was rendered against different files) and the
    # debt SHOULD re-arm. A tombstone would permanently exempt the run instead.
    tombstone_persisted: bool | None = None
    if newly_cleared and record_tombstone:
        tombstone_persisted = _record_cleared(
            workdir, str(manifest.get("run_id") or ""), newly_cleared, reason
        )
        if tombstone_persisted is False:
            # The waiver could not be persisted, so the debt WILL re-arm on the
            # next write. Say so rather than reporting a durable clear that is
            # not durable.
            _log(
                workdir,
                "clear WARNING tombstone not persisted for "
                f"{','.join(newly_cleared)}; the debt will re-arm on the next run-record write",
            )

    manifest_path = workdir / MANIFEST_RELPATH
    if not remaining:
        # Review complete — remove the manifest, flip the state flag.
        removed = False
        try:
            if manifest_path.exists():
                manifest_path.unlink()
                removed = True
        except OSError:
            removed = False
        state_updated = _set_state_flag(workdir, False)
        _log(workdir, f"clear complete cleared={','.join(newly_cleared) or 'none'} ({reason or 'no reason'})")
        return {
            "action": "cleared_complete",
            "cleared": newly_cleared,
            "remaining": [],
            "status": "complete",
            "state_updated": state_updated,
            "manifest_removed": removed,
            "tombstone_persisted": tombstone_persisted,
        }

    # Verifiers still owed — persist the reduced manifest.
    payload = dict(manifest)
    payload.pop("_malformed", None)
    payload["owed"] = remaining
    payload["cleared"] = cleared_total
    payload["status"] = "incomplete"
    commands = manifest.get("dispatch_commands")
    payload["dispatch_commands"] = {
        k: v for k, v in (commands if isinstance(commands, dict) else {}).items()
        if k in remaining
    }
    owners = manifest.get("owed_runs")
    payload["owed_runs"] = {
        k: v for k, v in (owners if isinstance(owners, dict) else {}).items()
        if k in remaining
    }
    payload["updated_at"] = _utcnow_iso()
    _atomic_write_json(manifest_path, payload)
    state_updated = _set_state_flag(workdir, True)
    _log(workdir, f"clear partial cleared={','.join(newly_cleared) or 'none'} remaining={','.join(remaining)}")
    return {
        "action": "cleared_partial",
        "cleared": newly_cleared,
        "remaining": remaining,
        "status": "incomplete",
        "state_updated": state_updated,
        "manifest_removed": False,
        "tombstone_persisted": tombstone_persisted,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _emit(payload: dict[str, Any], *, as_json: bool, stream=sys.stdout) -> None:
    if as_json:
        stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return
    for key in ("action", "status", "run_id", "diff_range"):
        if key in payload and payload[key] is not None:
            stream.write(f"{key}: {payload[key]}\n")
    if payload.get("owed"):
        stream.write(f"owed: {', '.join(payload['owed'])}\n")
    if payload.get("cleared"):
        stream.write(f"cleared: {', '.join(payload['cleared'])}\n")
    if payload.get("remaining"):
        stream.write(f"remaining: {', '.join(payload['remaining'])}\n")


def _parse_dispatch_overrides(pairs: Iterable[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in pairs or []:
        if "=" not in raw:
            continue
        name, _, cmd = raw.partition("=")
        name = name.strip()
        if name:
            out[name] = cmd
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Owed-verification manifest for GAP-1 — makes the parent-must-"
            "dispatch contract machine-checkable.  See module docstring."
        ),
    )
    sub = parser.add_subparsers(dest="op", required=True)

    p_write = sub.add_parser("write", help="Write/refresh the owed-verification manifest.")
    p_write.add_argument("--workdir", type=Path, default=Path.cwd())
    p_write.add_argument("--run-id", required=True)
    p_write.add_argument("--diff-range", required=True, help="e.g. HEAD~3..HEAD")
    p_write.add_argument(
        "--owe",
        action="append",
        default=[],
        dest="owe",
        help="A verifier the parent owes (repeatable). e.g. independent-auditor",
    )
    p_write.add_argument(
        "--owed",
        default=None,
        help="Comma-separated verifiers (alternative to repeated --owe).",
    )
    p_write.add_argument("--chunk-id", default=None)
    p_write.add_argument("--reason", default=None)
    p_write.add_argument("--written-by", default="nested-orchestrator")
    p_write.add_argument("--plan-path", default=None)
    p_write.add_argument(
        "--dispatch-command",
        action="append",
        default=[],
        dest="dispatch_command",
        help="Override a verifier's dispatch command: verifier=<cmd> (repeatable).",
    )
    p_write.add_argument("--json", action="store_true")

    p_check = sub.add_parser("check", help="Report whether review is complete.")
    p_check.add_argument("--workdir", type=Path, default=Path.cwd())
    p_check.add_argument("--json", action="store_true")

    p_clear = sub.add_parser("clear", help="Clear owed verifier(s) after the parent dispatched them.")
    p_clear.add_argument("--workdir", type=Path, default=Path.cwd())
    p_clear.add_argument(
        "--verifier",
        action="append",
        default=[],
        dest="verifier",
        help="A verifier to clear (repeatable).",
    )
    p_clear.add_argument("--all", action="store_true", dest="clear_all")
    p_clear.add_argument("--reason", default=None)
    p_clear.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    workdir = args.workdir.resolve()

    if args.op == "write":
        owed = list(args.owe)
        if args.owed:
            owed.extend(s for s in args.owed.split(",") if s.strip())
        if not owed:
            p_write.error("write requires at least one --owe / --owed verifier")
        payload = write_manifest(
            workdir,
            run_id=args.run_id,
            diff_range=args.diff_range,
            owed=owed,
            chunk_id=args.chunk_id,
            reason=args.reason,
            written_by=args.written_by,
            plan_path=args.plan_path,
            dispatch_overrides=_parse_dispatch_overrides(args.dispatch_command),
        )
        _emit({"action": "write", **payload}, as_json=args.json)
        return 0

    if args.op == "check":
        result = check_manifest(workdir)
        _emit(result, as_json=args.json)
        # Exit 1 when review is INCOMPLETE so a caller can gate on it.
        return 1 if result["status"] == "incomplete" else 0

    if args.op == "clear":
        if not args.verifier and not args.clear_all:
            p_clear.error("clear requires --verifier <name> (repeatable) or --all")
        result = clear_verifiers(
            workdir,
            verifiers=args.verifier,
            clear_all=args.clear_all,
            reason=args.reason,
        )
        _emit(result, as_json=args.json)
        return 0

    parser.error("no op selected")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
