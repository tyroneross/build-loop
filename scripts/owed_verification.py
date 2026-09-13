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
        # No lock module reachable means no peer writer is honouring one either.
        yield
        return
    # ACQUIRE outside the body. Wrapping the yield in the same `try` meant an
    # exception raised INSIDE the protected mutation was caught by the lock's
    # own handler, which then yielded a second time -- `RuntimeError: generator
    # didn't stop after throw()`, a crash replacing the caller's real error.
    # A lock timeout also must not silently degrade to the unlocked read-modify-
    # write this wrapper exists to prevent, so it is raised, not swallowed.
    with LockedFile(path):
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


def _debt_key(debt: dict[str, Any]) -> tuple[str, str]:
    return (str(debt.get("verifier") or ""), str(debt.get("run_id") or ""))


def _load_debts(existing: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read `debts` / `cleared_debts` from a manifest, upgrading the old shape.

    A pre-upgrade manifest keyed debts by VERIFIER NAME alone, which cannot
    represent two runs owing the same verifier: the second write overwrote the
    first run's obligation and the first diff shipped unreviewed. `owed_runs`
    patched the read side but the underlying list still had one slot per name.
    The record is now one row per (verifier, run_id) -- the thing a debt
    actually is -- and `owed` / `owed_runs` / `dispatch_commands` are derived
    views kept for every existing reader.
    """
    debts: list[dict[str, Any]] = []
    cleared: list[dict[str, Any]] = []
    if not isinstance(existing, dict) or existing.get("_malformed"):
        return debts, cleared

    raw_debts = existing.get("debts")
    if isinstance(raw_debts, list):
        debts = [d for d in raw_debts if isinstance(d, dict) and d.get("verifier")]
    else:
        # Upgrade the flat shape.
        owners = existing.get("owed_runs")
        owners = owners if isinstance(owners, dict) else {}
        commands = existing.get("dispatch_commands")
        commands = commands if isinstance(commands, dict) else {}
        reasons = existing.get("reasons")
        reasons = reasons if isinstance(reasons, dict) else {}
        fallback_run = str(existing.get("run_id") or "")
        fallback_range = str(existing.get("diff_range") or "unknown")
        for name in existing.get("owed") or []:
            name = str(name)
            debts.append({
                "verifier": name,
                "run_id": str(owners.get(name) or fallback_run),
                "diff_range": fallback_range,
                "dispatch_command": commands.get(name),
                "reason": reasons.get(name),
            })

    raw_cleared = existing.get("cleared_debts")
    if isinstance(raw_cleared, list):
        cleared = [d for d in raw_cleared if isinstance(d, dict) and d.get("verifier")]
    else:
        fallback_run = str(existing.get("run_id") or "")
        for name in existing.get("cleared") or []:
            cleared.append({"verifier": str(name), "run_id": fallback_run})
    return debts, cleared


def _manifest_payload(
    debts: list[dict[str, Any]],
    cleared: list[dict[str, Any]],
    *,
    run_id: str,
    diff_range: str,
    chunk_id: str | None,
    reason: str | None,
    written_by: str,
) -> dict[str, Any]:
    """Assemble the manifest, including the derived views older readers use."""
    payload: dict[str, Any] = {
        "run_id": run_id,
        "chunk_id": chunk_id,
        "diff_range": diff_range,
        "debts": debts,
        "cleared_debts": cleared,
        # Derived views. `owed` is the de-duplicated verifier list; a verifier
        # owed by two runs appears once here and twice in `debts`.
        "owed": _dedupe(str(d.get("verifier")) for d in debts),
        "cleared": _dedupe(str(d.get("verifier")) for d in cleared),
        "owed_runs": {str(d["verifier"]): str(d.get("run_id") or "") for d in debts},
        "dispatch_commands": {
            str(d["verifier"]): d.get("dispatch_command")
            for d in debts if d.get("dispatch_command")
        },
        "reasons": {
            str(d["verifier"]): d.get("reason") for d in debts if d.get("reason")
        },
        "written_by": written_by,
        "written_at": _utcnow_iso(),
        "status": "incomplete" if debts else "complete",
    }
    if reason:
        payload["reason"] = reason
    if not payload["reasons"]:
        payload.pop("reasons")
    return payload


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

    Accumulates rather than clobbers, so a second ``write`` in the same run
    adds a debt instead of replacing one -- and a write from a DIFFERENT run
    adds its own row rather than taking over an existing verifier's slot. A
    (verifier, run_id) pair already cleared is never silently re-added; a
    waiver for one run does not suppress another run's obligation. Flips
    ``state.json.review_incomplete = true`` when anything is owed.
    """
    run_id = str(run_id)
    existing = load_manifest(workdir)
    debts, cleared = _load_debts(existing)
    cleared_keys = {_debt_key(d) for d in cleared}
    have = {_debt_key(d) for d in debts}
    overrides = dispatch_overrides or {}
    reasons = reasons or {}

    for name in _dedupe(owed):
        key = (name, run_id)
        if key in cleared_keys or key in have:
            continue
        # Each debt carries ITS OWN range, so a later run's write cannot
        # re-point an earlier run's dispatch command at the wrong diff.
        command = _dispatch_commands([name], diff_range, plan_path, overrides).get(name)
        debts.append({
            "verifier": name,
            "run_id": run_id,
            "diff_range": diff_range,
            "dispatch_command": command,
            "reason": reasons.get(name),
        })
        have.add(key)

    payload = _manifest_payload(
        debts, cleared, run_id=run_id, diff_range=diff_range,
        chunk_id=chunk_id, reason=reason, written_by=written_by,
    )
    _atomic_write_json(workdir / MANIFEST_RELPATH, payload)
    state_updated = _set_state_flag(workdir, bool(debts))
    _log(workdir, f"write run={run_id} owed={','.join(payload['owed']) or 'none'}")
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


def _resolve_range(workdir: Path, diff_range: str) -> str:
    """The run's real git range, so the emitted dispatch command is runnable.

    Both production call sites arm the debt without a range, so every manifest
    rendered `codex exec "Review the diff unknown ..."` -- a debt that arms
    correctly and hands the operator an instruction that cannot be run. The
    range resolves the same way `--files-touched-from-git` does; when even that
    is unavailable the literal "unknown" is preserved rather than invented.
    """
    if diff_range and diff_range != "unknown":
        return diff_range
    data = _read_json(workdir / STATE_RELPATH)
    pre = data.get("preBuildSha") if isinstance(data, dict) else None
    return f"{pre}..HEAD" if pre else "unknown"


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

    host = record.get("host")
    decisions = list(record.get("judge_decisions") or [])
    if workdir is not None:
        # Also read the judge-decisions FILE. The debt arms precisely because
        # the verdict was not available at run-record-write time, so the round
        # is run afterwards and appended there -- and nothing re-read it, which
        # left `clear` (the waiver) as the only visible way out of a debt the
        # operator had actually discharged.
        decisions.extend(_judge_decisions_file(workdir, str(record.get("run_id") or "")))
    if cross_vendor_present(decisions, host):
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


JUDGE_DECISIONS_RELPATH = Path(".build-loop") / "judge-decisions.json"


def _judge_decisions_file(workdir: Path, run_id: str) -> list[dict[str, Any]]:
    """Verdicts in `.build-loop/judge-decisions.json` that belong to THIS run.

    The file is append-only and outlives every run in the repository, so an
    unscoped read let a verdict explicitly stamped with an older run_id -- or
    one dropped from a widened run record because its file set had changed --
    discharge a new run's debt. An entry claims a run or it is not this run's
    evidence: a missing or mismatched `run_id` is skipped, not assumed current.
    """
    data = _read_json(workdir / JUDGE_DECISIONS_RELPATH)
    if isinstance(data, dict):
        data = data.get("decisions")
    if not isinstance(data, list) or not run_id or run_id == "unknown":
        return []
    return [
        item for item in data
        if isinstance(item, dict) and str(item.get("run_id") or "") == run_id
    ]

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
        diff_range = _resolve_range(workdir, diff_range)
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
    if manifest.get("_malformed"):
        return {
            "status": "incomplete",
            "owed": [str(v) for v in manifest.get("owed") or []],
            "cleared": [],
            "debts": [],
            "owed_runs": {},
            "run_id": None,
            "diff_range": None,
            "dispatch_commands": {},
            "review_incomplete": True,
            "malformed": True,
            "manifest_path": str(workdir / MANIFEST_RELPATH),
        }
    debts, cleared = _load_debts(manifest)
    remaining = _dedupe(str(d.get("verifier")) for d in debts)
    incomplete = bool(debts)
    return {
        "status": "incomplete" if incomplete else "complete",
        "owed": remaining,
        "cleared": _dedupe(str(d.get("verifier")) for d in cleared),
        # The per-run rows, so a caller can tell WHOSE debt is outstanding
        # instead of inferring it from the manifest's last-writer run_id.
        "debts": debts,
        "owed_runs": {str(d["verifier"]): str(d.get("run_id") or "") for d in debts},
        "run_id": manifest.get("run_id"),
        "diff_range": manifest.get("diff_range"),
        "dispatch_commands": manifest.get("dispatch_commands", {}),
        "review_incomplete": incomplete,
        "malformed": False,
        "manifest_path": str(workdir / MANIFEST_RELPATH),
    }


def _clear_verifiers_unlocked(
    workdir: Path,
    *,
    verifiers: Iterable[str] | None = None,
    clear_all: bool = False,
    reason: str | None = None,
    record_tombstone: bool = True,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Mark owed debt(s) as discharged.

    Scoped by (verifier, run_id). Clearing a verifier without naming a run
    clears it for every run that owes it, which is the historical behaviour;
    naming ``run_id`` clears only that run's debt. When the last debt clears
    the manifest is REMOVED and ``state.json.review_incomplete`` flips false.
    Idempotent: clearing an unknown or already-cleared debt is a no-op.
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

    debts, already_cleared = _load_debts(manifest)
    wanted = set(_dedupe(verifiers or []))
    scope = str(run_id) if run_id else None

    def _selected(debt: dict[str, Any]) -> bool:
        if scope is not None and str(debt.get("run_id") or "") != scope:
            return False
        return clear_all or str(debt.get("verifier")) in wanted

    newly = [d for d in debts if _selected(d)]
    remaining = [d for d in debts if d not in newly]
    newly_names = _dedupe(str(d.get("verifier")) for d in newly)

    # A waiver is recorded ONLY for a manual clear, and against the run that
    # OWED the debt -- the manifest's own run_id is just the last writer's. A
    # verdict-based discharge records none: the verdict already suppresses
    # re-arming, and a waiver would outlive it and exempt a later, wider scope.
    tombstone_persisted: bool | None = None
    if newly and record_tombstone:
        by_owner: dict[str, list[str]] = {}
        for debt in newly:
            by_owner.setdefault(str(debt.get("run_id") or ""), []).append(
                str(debt.get("verifier"))
            )
        tombstone_persisted = True
        for owner, names in by_owner.items():
            if not _record_cleared(workdir, owner, names, reason):
                tombstone_persisted = False
        if tombstone_persisted is False:
            _log(
                workdir,
                "clear WARNING tombstone not persisted for "
                f"{','.join(newly_names)}; the debt will re-arm on the next run-record write",
            )

    # The cleared record is per (verifier, run_id) too: a waiver granted to one
    # run must not suppress another run's obligation for the same verifier.
    cleared_total = list(already_cleared)
    seen = {_debt_key(d) for d in cleared_total}
    for debt in newly:
        key = _debt_key(debt)
        if key not in seen:
            seen.add(key)
            cleared_total.append({
                "verifier": debt.get("verifier"),
                "run_id": debt.get("run_id"),
                "at": _utcnow_iso(),
                "reason": reason or "",
            })

    manifest_path = workdir / MANIFEST_RELPATH
    if not remaining:
        removed = False
        try:
            if manifest_path.exists():
                manifest_path.unlink()
                removed = True
        except OSError:
            removed = False
        state_updated = _set_state_flag(workdir, False)
        _log(workdir, f"clear complete cleared={','.join(newly_names) or 'none'} ({reason or 'no reason'})")
        return {
            "action": "cleared_complete",
            "cleared": newly_names,
            "remaining": [],
            "status": "complete",
            "state_updated": state_updated,
            "manifest_removed": removed,
            "tombstone_persisted": tombstone_persisted,
        }

    payload = _manifest_payload(
        remaining, cleared_total,
        run_id=str(manifest.get("run_id") or ""),
        diff_range=str(manifest.get("diff_range") or "unknown"),
        chunk_id=manifest.get("chunk_id"),
        reason=manifest.get("reason"),
        written_by=str(manifest.get("written_by") or "nested-orchestrator"),
    )
    payload["updated_at"] = _utcnow_iso()
    _atomic_write_json(manifest_path, payload)
    state_updated = _set_state_flag(workdir, True)
    remaining_names = _dedupe(str(d.get("verifier")) for d in remaining)
    _log(workdir, f"clear partial cleared={','.join(newly_names) or 'none'} remaining={','.join(remaining_names)}")
    return {
        "action": "cleared_partial",
        "cleared": newly_names,
        "remaining": remaining_names,
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
    if payload.get("tombstone_persisted") is False:
        # Without this line the operator sees "cleared_complete" and nothing
        # else, then watches the debt re-arm on the next write with no cause.
        stream.write(
            "WARNING: the waiver was NOT persisted; this debt will re-arm on the "
            "next run-record write (state.json was unwritable or locked)\n"
        )


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
