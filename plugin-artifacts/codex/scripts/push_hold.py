#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""push_hold.py — manage and evaluate the build-loop "push HOLD" marker.

WHY
---
An autonomous background path (self-review ``apply_push`` / the codex-autonomy
poller / a future automation) can race a ``git push origin main`` even when a
run was briefed "do NOT push" or when an ``independent-auditor`` verdict has
unresolved blocking findings.  ``scripts/deployment_policy.py`` classifies push
commands by *target branch name* only — it has no awareness of either signal.

This script is the single place that answers two questions:

1. Is a "hold" currently active in this repo? (file marker + state.json signal)
2. Does a particular push (a git pre-push hook call) need to be blocked?

It is consumed by:

- ``hooks/git/pre-push`` — the path-agnostic git hook.  Runs for every push
  regardless of which process initiated it.  Calls ``evaluate_push`` and exits
  per its verdict.  FAILS OPEN on any internal exception (a broken hook must
  never permanently wedge the user's ability to push; we distinguish
  "hold active → block" from "hook internal error → allow + log").
- ``agents/build-orchestrator.md`` — Phase 1 (briefed do-not-push) and
  Phase 4 Review-A (blocking auditor verdict).  Sets/clears the marker.
- ``scripts/install_git_hooks.py`` — installs the pre-push hook.

The marker is a plain JSON file at ``.build-loop/.push-hold`` so anything (a
script, a human, another agent) can inspect it without depending on this CLI.

CLI
---

::

    push_hold.py --status [--json]
    push_hold.py --set --reason "<text>" [--source orchestrator|review-a|manual]
                                          [--finding-ids id1,id2]
                                          [--run-id <id>]
                                          [--auditor-verdict suggest_correction]
                                          [--json]
    push_hold.py --release [--reason "<text>"] [--json]

Exit codes
----------

- 0 on every successful call (including ``--status`` whether or not a hold is
  active).  Status callers parse the JSON / stdout to decide.
- 2 on argument errors (handled by argparse).

Bypass
------

Mirrors ``audit_before_commit.py``: setting ``BUILDLOOP_PUSH_HOLD_BYPASS=1``
in the environment causes ``evaluate_push`` to allow the push and log a one-line
entry.  Intended for the rare case where the user has knowingly overridden the
hold from a terminal.  The hook still runs; bypass is observable in the log.

Importable surface
------------------

- ``load_marker(workdir) -> dict | None``
- ``set_marker(workdir, *, reason, source, ...) -> Path``
- ``clear_marker(workdir, *, reason=None) -> bool``
- ``detect_blocking_verdict(workdir) -> dict | None``
- ``is_hold_active(workdir) -> tuple[bool, str | None, str]``
- ``evaluate_push(workdir, stdin_lines, *, env=None,
                  protected_branches=None) -> dict``
- ``release_if_briefed(workdir, *, reason=None) -> dict``
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Verdicts the independent-auditor (and equivalent) report that constitute an
# unresolved BLOCKING finding when not marked resolved.  Lowercased for
# case-insensitive matching.  Mirrors the verdict taxonomy in
# ``references/independent-auditor.md``.
BLOCKING_VERDICTS: frozenset[str] = frozenset(
    {"nay", "suggest_correction", "look_again", "block"}
)

# Bypass env var name — kept consistent with the audit gate pattern.
BYPASS_ENV = "BUILDLOOP_PUSH_HOLD_BYPASS"

# Maximum age (hours) for a state.json blocking verdict to be treated as active.
# Older verdicts are ignored so a stale unresolved entry can never permanently
# wedge autonomous pushes.  Override via env or .build-loop/config.json.
_DEFAULT_MAX_VERDICT_AGE_HOURS = 24
_MAX_AGE_ENV = "BUILDLOOP_PUSH_HOLD_MAX_AGE_H"

# Relative paths inside the workdir.
MARKER_RELPATH = Path(".build-loop") / ".push-hold"
STATE_RELPATH = Path(".build-loop") / "state.json"
LOG_RELPATH = Path(".build-loop") / "audit-log.md"


# ---------------------------------------------------------------------------
# Time / IO helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _max_verdict_age_hours(workdir: Path) -> float:
    """Return the configured max verdict age in hours.

    Priority: env ``BUILDLOOP_PUSH_HOLD_MAX_AGE_H`` > ``.build-loop/config.json``
    ``push_hold.max_verdict_age_hours`` > default (24).
    """
    env_val = os.environ.get(_MAX_AGE_ENV)
    if env_val is not None:
        try:
            return float(env_val)
        except ValueError:
            pass
    cfg_path = workdir / ".build-loop" / "config.json"
    cfg = _read_json(cfg_path)
    if isinstance(cfg, dict):
        ph_cfg = cfg.get("push_hold")
        if isinstance(ph_cfg, dict):
            try:
                return float(ph_cfg["max_verdict_age_hours"])
            except (KeyError, (TypeError, ValueError)):
                pass
    return float(_DEFAULT_MAX_VERDICT_AGE_HOURS)


def _parse_iso_ts(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp string.  Returns None on failure.

    Robust to microseconds, ``Z`` / ``+00:00`` offsets, and naive timestamps.
    ``datetime.fromisoformat`` (Python 3.11+) covers the common variants; the
    strptime loop is a fallback. IMPORTANT: a too-narrow parser silently
    disables the staleness guard (an unparsed timestamp reads as
    "missing → don't block"), so this MUST accept whatever the state writers
    emit — including a bare ``datetime.now(...).isoformat()`` with microseconds.
    """
    if not ts:
        return None
    s = str(ts).strip()
    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (ValueError, TypeError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S+00:00", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic-ish write so a hook reading concurrently sees a complete file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{int(time.time()*1000)}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Marker primitives
# ---------------------------------------------------------------------------


def load_marker(workdir: Path) -> dict[str, Any] | None:
    """Return the parsed marker, or None if absent / unreadable.

    A malformed marker is treated as "present but unparseable" — the caller
    should still hold (fail safe: prefer to block one extra push than to allow
    a push the user explicitly held)."""
    path = workdir / MARKER_RELPATH
    if not path.exists():
        return None
    data = _read_json(path)
    if data is None:
        # Malformed marker — return a synthetic "active, unknown reason" record.
        return {
            "reason": "marker present but unparseable",
            "source": "unknown",
            "set_at": None,
            "_malformed": True,
        }
    if not isinstance(data, dict):
        return {
            "reason": "marker present but not a JSON object",
            "source": "unknown",
            "set_at": None,
            "_malformed": True,
        }
    return data


def set_marker(
    workdir: Path,
    *,
    reason: str,
    source: str = "manual",
    run_id: str | None = None,
    auditor_verdict: str | None = None,
    finding_ids: Iterable[str] | None = None,
) -> Path:
    """Create/overwrite the hold marker."""
    payload: dict[str, Any] = {
        "reason": reason,
        "source": source,
        "set_at": _utcnow_iso(),
    }
    if run_id:
        payload["run_id"] = run_id
    if auditor_verdict:
        payload["auditor_verdict"] = auditor_verdict
    finding_list = list(finding_ids) if finding_ids else []
    if finding_list:
        payload["finding_ids"] = finding_list
    marker_path = workdir / MARKER_RELPATH
    _atomic_write_json(marker_path, payload)
    return marker_path


def clear_marker(workdir: Path, *, reason: str | None = None) -> bool:
    """Remove the hold marker.  Returns True iff a marker was present."""
    marker_path = workdir / MARKER_RELPATH
    if not marker_path.exists():
        return False
    try:
        marker_path.unlink()
    except OSError:
        return False
    if reason:
        # Best-effort audit-log line; never fatal.
        try:
            log = workdir / LOG_RELPATH
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as fh:
                fh.write(f"- {_utcnow_iso()} push_hold cleared: {reason}\n")
        except OSError:
            pass
    return True


# ---------------------------------------------------------------------------
# Closeout release helper (Phase D — briefed hold self-clearing)
# ---------------------------------------------------------------------------


def release_if_briefed(workdir: Path, *, reason: str | None = None) -> dict[str, Any]:
    """Release a briefed (orchestrator or manual) hold when no blocking verdict remains.

    Called by Phase D Closeout to ensure a hold set at Phase 1 never persists
    past the end of its run.  Logic:

    1. Load the marker.  If absent → noop (already clear).
    2. If ``source`` is ``review-a`` → skip (Review-A's re-audit path owns that
       release).
    3. If ``detect_blocking_verdict`` returns a non-None result → hold stays
       (unresolved blocking findings remain; release not appropriate yet).
    4. Otherwise → clear the marker.

    Returns a dict with ``action`` ∈ {``released``, ``noop_absent``,
    ``noop_review_a``, ``noop_blocking_verdict``, ``skipped``} and a ``reason``
    field.
    """
    marker = load_marker(workdir)
    if marker is None:
        return {"action": "noop_absent", "reason": "no marker present"}

    source = str(marker.get("source") or "").strip().lower()
    if source == "review-a":
        return {
            "action": "noop_review_a",
            "reason": "marker source is review-a; that path owns its own release",
        }

    verdict = detect_blocking_verdict(workdir)
    if verdict is not None:
        return {
            "action": "noop_blocking_verdict",
            "reason": (
                f"unresolved blocking verdict ({verdict['verdict']}) still present"
                f" in run {verdict['run_id'] or 'latest'} — hold retained"
            ),
        }

    release_reason = reason or "run closed, no blocking findings"
    cleared = clear_marker(workdir, reason=release_reason)
    return {
        "action": "released" if cleared else "skipped",
        "reason": release_reason,
    }


# ---------------------------------------------------------------------------
# State.json — unresolved blocking auditor verdict detection
# ---------------------------------------------------------------------------


def detect_blocking_verdict(workdir: Path) -> dict[str, Any] | None:
    """Inspect state.json's latest run for an unresolved blocking verdict.

    Returns a small record naming the verdict and finding ids when one is
    present, else None.  Conservative: any parse error returns None (we do NOT
    block on a corrupt state.json — the marker file is the authoritative
    explicit hold).  A verdict is considered RESOLVED when its record carries
    ``"resolved": true`` (or ``"status": "resolved"``).

    Staleness guard: the run's timestamp (``created_at``, ``ts``, or
    ``started_at``) is compared against ``MAX_VERDICT_AGE_HOURS`` (default 24h;
    override via env ``BUILDLOOP_PUSH_HOLD_MAX_AGE_H`` or
    ``.build-loop/config.json push_hold.max_verdict_age_hours``).  When the
    timestamp is missing OR older than the window the verdict is treated as
    stale → returns None.  This prevents a permanently-wedging stale entry.
    """
    state_path = workdir / STATE_RELPATH
    data = _read_json(state_path)
    if not isinstance(data, dict):
        return None
    runs = data.get("runs")
    if not isinstance(runs, list) or not runs:
        return None
    latest = runs[-1]
    if not isinstance(latest, dict):
        return None

    # --- Staleness guard ---
    run_ts_raw = (
        latest.get("created_at")
        or latest.get("ts")
        or latest.get("started_at")
    )
    run_ts = _parse_iso_ts(run_ts_raw)
    max_age_h = _max_verdict_age_hours(workdir)
    now = datetime.now(timezone.utc)
    if run_ts is None or (now - run_ts).total_seconds() > max_age_h * 3600:
        # Missing timestamp OR too old — do NOT block.
        return None

    decisions = latest.get("judge_decisions")
    if not isinstance(decisions, list):
        return None
    blocking: list[dict[str, Any]] = []
    for entry in decisions:
        if not isinstance(entry, dict):
            continue
        verdict_raw = entry.get("verdict") or entry.get("status") or ""
        verdict = str(verdict_raw).strip().lower()
        if verdict not in BLOCKING_VERDICTS:
            continue
        if entry.get("resolved") is True:
            continue
        if str(entry.get("status", "")).strip().lower() == "resolved":
            continue
        blocking.append(entry)
    if not blocking:
        return None
    first = blocking[0]
    finding_ids: list[str] = []
    raw_ids = first.get("finding_ids")
    if isinstance(raw_ids, list):
        finding_ids = [str(x) for x in raw_ids if x]
    return {
        "verdict": str(first.get("verdict") or first.get("status") or "").strip(),
        "judge": str(first.get("judge") or first.get("agent") or "independent-auditor"),
        "run_id": str(latest.get("run_id") or latest.get("id") or ""),
        "finding_ids": finding_ids,
        "count": len(blocking),
    }


# ---------------------------------------------------------------------------
# Aggregate "is the hold active?" question
# ---------------------------------------------------------------------------


def is_hold_active(workdir: Path) -> tuple[bool, str | None, str]:
    """Return ``(active, reason, source)``.

    Source is one of ``"marker"``, ``"state"``, or ``"none"``.  The marker file
    takes precedence over the state.json signal — the marker is the *explicit*
    user/orchestrator intent."""
    marker = load_marker(workdir)
    if marker is not None:
        return True, str(marker.get("reason", "hold marker present")), "marker"
    verdict = detect_blocking_verdict(workdir)
    if verdict is not None:
        ids_part = f" (finding_ids={','.join(verdict['finding_ids'])})" if verdict["finding_ids"] else ""
        reason = (
            f"unresolved {verdict['verdict']} from {verdict['judge']}"
            f" in run {verdict['run_id'] or 'latest'}{ids_part}"
        )
        return True, reason, "state"
    return False, None, "none"


# ---------------------------------------------------------------------------
# pre-push hook entry point
# ---------------------------------------------------------------------------


def _parse_push_lines(stdin_lines: Iterable[str]) -> list[tuple[str, str, str, str]]:
    """Parse git's pre-push stdin into ``(local_ref, local_sha, remote_ref, remote_sha)``.

    Lines we can't parse are dropped silently — git always emits well-formed
    lines, so a malformed line means someone wired the hook wrong, not that
    they're trying to bypass the gate.  Returning an empty list = no refs
    being pushed → allow.
    """
    parsed: list[tuple[str, str, str, str]] = []
    for raw in stdin_lines:
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        parsed.append((parts[0], parts[1], parts[2], parts[3]))
    return parsed


def _branch_from_ref(ref: str) -> str | None:
    """Map ``refs/heads/main`` → ``main``; return None for tags/notes/etc."""
    if not ref or ref == "(delete)":
        return None
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/") :]
    # Bare branch name (some hosting providers / older git invocations)
    if "/" not in ref and ref not in {"HEAD"}:
        return ref
    return None


def evaluate_push(
    workdir: Path,
    stdin_lines: Iterable[str],
    *,
    env: dict[str, str] | None = None,
    protected_branches: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Decide whether a pre-push hook call should block.

    Returns ``{"action": "allow"|"block"|"bypass"|"error", "exit_code": int,
    "reason": str, "source": str, "protected_targets": [str, ...]}``.

    NEVER raises.  The caller (the hook) is expected to print ``reason`` to
    stderr and exit with ``exit_code``.
    """
    env_map = env if env is not None else os.environ

    # Protected-branch set: prefer caller override; else load_protected_branches.
    if protected_branches is None:
        try:
            from deployment_policy import load_protected_branches  # type: ignore

            protected_set = {b.lower() for b in load_protected_branches(workdir)}
        except Exception:
            # Fail-safe to the documented default set rather than guessing.
            protected_set = {
                "main",
                "master",
                "production",
                "prod",
                "release",
                "stable",
                "trunk",
                "live",
            }
    else:
        protected_set = {b.lower() for b in protected_branches}

    refs = _parse_push_lines(stdin_lines)
    targets: list[str] = []
    for _, _, remote_ref, _ in refs:
        branch = _branch_from_ref(remote_ref)
        if branch and branch.lower() in protected_set:
            targets.append(branch)

    if not targets:
        return {
            "action": "allow",
            "exit_code": 0,
            "reason": "no protected refs in push",
            "source": "none",
            "protected_targets": [],
        }

    active, reason, source = is_hold_active(workdir)
    if not active:
        return {
            "action": "allow",
            "exit_code": 0,
            "reason": "no hold active",
            "source": "none",
            "protected_targets": targets,
        }

    # Hold is active AND a protected ref is being pushed.
    if env_map.get(BYPASS_ENV) == "1":
        # Best-effort log; never raise.
        try:
            log = workdir / LOG_RELPATH
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"- {_utcnow_iso()} push_hold BYPASS via {BYPASS_ENV}=1 "
                    f"(reason={reason}, targets={','.join(targets)})\n"
                )
        except OSError:
            pass
        return {
            "action": "bypass",
            "exit_code": 0,
            "reason": f"BYPASS via {BYPASS_ENV}=1 (held: {reason})",
            "source": source,
            "protected_targets": targets,
        }

    return {
        "action": "block",
        "exit_code": 1,
        "reason": reason or "hold active",
        "source": source,
        "protected_targets": targets,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _emit(payload: dict[str, Any], *, as_json: bool, stream=sys.stdout) -> None:
    if as_json:
        stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        if "action" in payload:
            stream.write(f"action: {payload['action']}\n")
        for k in ("active", "reason", "source", "marker_path"):
            if k in payload:
                stream.write(f"{k}: {payload[k]}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Manage and evaluate the build-loop push-hold marker. "
            "See module docstring for full design."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true", help="Report current hold state.")
    mode.add_argument("--set", action="store_true", dest="set_mode", help="Write/refresh the hold marker.")
    mode.add_argument("--release", action="store_true", help="Remove the hold marker.")
    mode.add_argument(
        "--release-if-briefed",
        action="store_true",
        dest="release_if_briefed",
        help=(
            "Phase D closeout helper: release an orchestrator/manual briefed hold "
            "when no unresolved blocking verdict remains.  Skips if source=review-a. "
            "Safe to run unconditionally at closeout (idempotent)."
        ),
    )

    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    parser.add_argument("--reason", type=str, default=None)
    parser.add_argument("--source", type=str, default="manual",
                        help="Where the hold originated (orchestrator|review-a|manual|...).")
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--auditor-verdict", type=str, default=None)
    parser.add_argument("--finding-ids", type=str, default=None,
                        help="Comma-separated finding ids.")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    workdir = args.workdir.resolve()

    if args.status:
        active, reason, source = is_hold_active(workdir)
        marker = load_marker(workdir)
        payload = {
            "active": active,
            "reason": reason,
            "source": source,
            "marker_path": str(workdir / MARKER_RELPATH),
            "marker": marker,
        }
        _emit(payload, as_json=args.json)
        return 0

    if args.set_mode:
        if not args.reason:
            parser.error("--set requires --reason")
        finding_ids: list[str] = []
        if args.finding_ids:
            finding_ids = [s.strip() for s in args.finding_ids.split(",") if s.strip()]
        path = set_marker(
            workdir,
            reason=args.reason,
            source=args.source,
            run_id=args.run_id,
            auditor_verdict=args.auditor_verdict,
            finding_ids=finding_ids,
        )
        _emit(
            {"action": "set", "marker_path": str(path), "reason": args.reason, "source": args.source},
            as_json=args.json,
        )
        return 0

    if args.release:
        removed = clear_marker(workdir, reason=args.reason)
        _emit(
            {"action": "release" if removed else "noop", "removed": removed, "reason": args.reason},
            as_json=args.json,
        )
        return 0

    if args.release_if_briefed:
        result = release_if_briefed(workdir, reason=args.reason)
        _emit(result, as_json=args.json)
        return 0

    # argparse guarantees one of the four above; safeguard:
    parser.error("no mode selected")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
