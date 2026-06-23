#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rally Point leadership lease (G1).

A multi-agent run has exactly ONE lead. "Lead" used to be implicit in
whoever opened the coordination file; this module makes it a first-class,
lease-based role so a crashed lead expires and another agent can take over.

State lives at ``<channel_dir>/rally/lead.json`` and is mutated only under
``<channel_dir>/rally/lead.lock`` (its own lock, separate from
``rally/current.lock``) reusing the ``fcntl.LOCK_EX`` pattern from
``rally.py``. Writes are atomic (tmp + ``os.replace``). The lease is
monotonic by ``lease_until``: a claim only succeeds when the file is
absent or the recorded lease has expired.

Two clocks — kept deliberately separate:
  * Lease liveness — ``renew_every_minutes`` (default 15). How long a lead
    holds the role before it must renew. NOT a polling cadence.
  * Watch poll — ``coordination_watch.py`` ~5s adaptive cadence. Untouched
    by this module.

Right-sized per the approved plan: a single ``lead`` per run.
``parent_lead`` and ``max_direct_reports`` are present in the schema for
forward-compatibility with a nested hierarchy but are NOT acted on here —
no multi-tier election, no report fan-out.

Public API:
    claim_lead, renew_lease, transfer_lead, relinquish_lead,
    is_lease_valid, read_lead

Each mutating call posts a durable ``lead-*`` record to ``changes.jsonl``
via ``post()`` so the lead history survives even if ``lead.json`` is
deleted (``rebuild_lead_from_changes`` reconstructs it from the tail).

Fire-and-forget on the channel post: a failed post never fails the claim.
Stdlib only.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # package import
    from . import changes
    from . import decay
    from .coordination_policy import CoordinationPolicy, load_policy
    from .post import post
except ImportError:  # script import
    import changes  # type: ignore
    import decay  # type: ignore
    from coordination_policy import CoordinationPolicy, load_policy  # type: ignore
    from post import post  # type: ignore

_RALLY_DIR = "rally"
_LEAD_NAME = "lead.json"
_LOCK_NAME = "lead.lock"
_SCHEMA_VERSION = "1.0"
_DEFAULT_RENEW_MINUTES = 15

# changes.jsonl kinds emitted by this module (also registered in
# changes.KNOWN_KINDS — kept in sync there).
_KIND_CLAIM = "lead-claim"
_KIND_RENEW = "lead-renew"
_KIND_TRANSFER = "lead-transfer"
_KIND_RELINQUISH = "lead-relinquish"
# Emitted when a claim succeeds by reclaiming an EXPIRED prior lease (stale lead
# auto-released by timeout), as opposed to a fresh claim into an empty seat.
_KIND_RECLAIM = "lead-reclaim"


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def _rally_dir(channel_dir: Path) -> Path:
    return Path(channel_dir) / _RALLY_DIR


def lead_path(channel_dir: Path) -> Path:
    """Path to ``rally/lead.json`` for a channel."""
    return _rally_dir(channel_dir) / _LEAD_NAME


def _lock_path(channel_dir: Path) -> Path:
    return _rally_dir(channel_dir) / _LOCK_NAME


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    # Treat a naive timestamp as UTC so comparisons never raise.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _atomic_write(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp.write_text(
        json.dumps(obj, separators=(",", ":"), sort_keys=True), encoding="utf-8"
    )
    os.replace(str(tmp), str(path))


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

def read_lead(channel_dir: Path) -> dict[str, Any] | None:
    """Read ``rally/lead.json``. Returns None when absent or invalid."""
    try:
        data = json.loads(lead_path(Path(channel_dir)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def is_lease_valid(channel_dir: Path, *, at: datetime | None = None) -> bool:
    """True when a lead exists and its lease has NOT expired.

    ``at`` defaults to now(); pass an explicit time for deterministic tests.
    """
    doc = read_lead(channel_dir)
    if not doc:
        return False
    lead = doc.get("lead")
    if not isinstance(lead, dict):
        return False
    expiry = _parse_iso(lead.get("lease_until"))
    if expiry is None:
        return False
    return (at or _now()) < expiry


def _lease_expired(doc: dict[str, Any] | None, at: datetime) -> bool:
    """True when there is no lead or the recorded lease is at/after expiry."""
    if not doc:
        return True
    lead = doc.get("lead")
    if not isinstance(lead, dict):
        return True
    expiry = _parse_iso(lead.get("lease_until"))
    if expiry is None:
        return True
    return at >= expiry


def _reclaimable(doc: dict[str, Any] | None, at: datetime) -> bool:
    """FAIL-CLOSED auto-reclaim decision for an EXISTING incumbent lease.

    Distinct from ``_lease_expired``: that function returns ``True`` for a
    missing/malformed lead so the very first agent can seize an empty seat.
    Auto-reclaim is a destructive-class action (it takes the role away from a
    peer), so a present-but-unparseable lease must NEVER be reclaimable — we
    refuse rather than reclaim on a timestamp we cannot trust. This matches the
    Rust ``takeover_eligible_owners`` fail-closed invariant.

    Returns ``True`` only when there IS an incumbent lead whose lease has a
    VALID, parseable ``lease_until`` that is at/after expiry.
    """
    if not doc:
        return False  # no incumbent to reclaim FROM (this is a fresh claim)
    lead = doc.get("lead")
    if not isinstance(lead, dict):
        return False
    expiry = _parse_iso(lead.get("lease_until"))
    if expiry is None:
        return False  # FAIL-CLOSED: malformed lease never reclaims
    return at >= expiry


def _build_lead_doc(
    *,
    run_id: str,
    session_id: str,
    tool: str,
    model: str,
    renew_every_minutes: int,
    now: datetime,
    owns: list[str] | None,
    parent_lead: str | None,
    max_direct_reports: int,
    chunk_owners: dict[str, str] | None,
    work_size: str = decay.LARGE,
    lease_secs: int | None = None,
) -> dict[str, Any]:
    # The lease window is SIZE-SCALED: a small (single-file) claim expires
    # sooner than a large (multi-file/coarse) claim. ``lease_secs`` (when given)
    # wins; otherwise it derives from ``work_size`` via the coordination policy
    # defaults. ``renew_every_minutes`` stays the RENEW cadence hint for the live
    # lead; the lease WINDOW is what governs auto-reclaim.
    if lease_secs is None:
        lease_secs = decay.reclaim_timeout_seconds(work_size)
    lease_until = (now + timedelta(seconds=lease_secs)).isoformat()
    return {
        "schema_version": _SCHEMA_VERSION,
        "run_id": run_id,
        "lead": {
            "session_id": session_id,
            "tool": tool,
            "model": model,
            "lease_until": lease_until,
            "renew_every_minutes": renew_every_minutes,
            "work_size": work_size,
            "parent_lead": parent_lead,
            "max_direct_reports": max_direct_reports,
            "current_reports": [],
            "owns": list(owns) if owns else ["plan", "merge_order", "closeout"],
        },
        "chunk_owners": dict(chunk_owners) if chunk_owners else {},
        "conflict_rule": (
            "owner decides owned chunk; lead decides integration; "
            "user decides strategy conflict"
        ),
    }


def _audit_log(kind: str, *, tool: str, run_id: str, session_id: str | None,
               extra: dict[str, Any] | None = None) -> None:
    """Emit a stderr audit line for a lead-* mutation (SEC-003).

    The leadership lease is **advisory coordination, not access control**:
    every mutating call trusts a caller-supplied ``session_id`` with no
    identity verification, so a lead claim is forgeable. A signing/auth
    layer is disproportionate for a local single-user dev tool. The
    proportionate control is observability — a durable, greppable record
    of which tool/run touched the lead, so a forged or unexpected
    transfer is at least visible after the fact. Fire-and-forget: a
    logging failure never affects the lease operation.
    """
    try:
        suffix = ""
        if extra:
            suffix = " " + " ".join(f"{k}={v}" for k, v in extra.items())
        print(
            f"[rally-point audit] {kind} tool={tool} run_id={run_id} "
            f"session_id={session_id}{suffix}",
            file=sys.stderr,
        )
    except Exception:  # noqa: BLE001 — audit logging never fails the op
        pass


def _post_lead_record(
    channel_dir: Path,
    *,
    kind: str,
    tool: str,
    model: str,
    run_id: str,
    app_slug: str,
    lead: dict[str, Any],
    extra: dict[str, Any] | None = None,
    workdir: Path | None = None,
) -> None:
    """Fire-and-forget durable audit record for a lead-* event."""
    _audit_log(
        kind, tool=tool, run_id=run_id,
        session_id=lead.get("session_id"), extra=extra,
    )
    payload: dict[str, Any] = {
        "session_id": lead.get("session_id"),
        "lease_until": lead.get("lease_until"),
        "run_id": run_id,
    }
    if extra:
        payload.update(extra)
    try:
        post(
            channel_dir=channel_dir,
            kind=kind,
            tool=tool,
            model=model,
            run_id=run_id,
            app_slug=app_slug,
            payload=payload,
            workdir=workdir,
        )
    except Exception:  # noqa: BLE001 — channel post never fails the claim
        pass


# --------------------------------------------------------------------------
# Mutating operations (all under rally/lead.lock)
# --------------------------------------------------------------------------

def _with_lock(channel_dir: Path, fn):
    """Run ``fn()`` while holding LOCK_EX on rally/lead.lock."""
    d = _rally_dir(Path(channel_dir))
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_lock_path(channel_dir)), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fn()
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def claim_lead(
    channel_dir: Path,
    *,
    run_id: str,
    session_id: str,
    tool: str,
    model: str,
    app_slug: str,
    renew_every_minutes: int = _DEFAULT_RENEW_MINUTES,
    owns: list[str] | None = None,
    parent_lead: str | None = None,
    max_direct_reports: int = 4,
    chunk_owners: dict[str, str] | None = None,
    work_size: str | None = None,
    effort: str | None = None,
    workdir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Attempt to become lead. Race-safe via rally/lead.lock.

    Under the lock: read ``lead.json``.

      * If absent (empty seat) -> claim, write self.
      * If a PRESENT incumbent's lease is reclaimable (valid, parseable,
        at/after expiry) -> AUTO-RECLAIM, write self, and post a durable
        ``lead-reclaim`` record naming who reclaimed and why (stale-by-timeout).
        FAIL-CLOSED: a present-but-unparseable lease is NEVER reclaimed.
      * Otherwise -> ``{"claimed": False, "lead": <incumbent doc>}`` unchanged.

    The new lease WINDOW is size-scaled: ``work_size`` (or ``effort``, or the
    ``owns`` file list) decides small (default 30m) vs large (default 2h) via
    the coordination policy. Two concurrent ``claim_lead`` calls serialize on
    the lock — exactly one wins.
    """
    at = now or _now()
    policy: CoordinationPolicy = load_policy(Path(workdir) if workdir else Path.cwd())
    # Size-scaling for the lease WINDOW is OPT-IN to preserve the prior lead
    # behavior exactly: if the caller gives no size signal (no work_size, no
    # effort), the window stays `renew_every_minutes` (the historical lead
    # lease). Ownership/lead claims that DO carry a size signal get the
    # size-scaled window (small 30m / large 2h) per the coordination policy.
    if work_size is not None or effort is not None:
        size = work_size or decay.classify_work_size(effort=effort, scope_paths=owns)
        lease_secs = decay.reclaim_timeout_seconds(
            size,
            small_minutes=policy.reclaim_small_minutes,
            large_minutes=policy.reclaim_large_minutes,
        )
    else:
        size = decay.LARGE  # recorded label; window stays the renew cadence
        lease_secs = renew_every_minutes * 60

    def _do() -> dict[str, Any]:
        existing = read_lead(channel_dir)
        # An incumbent that is present but NOT reclaimable blocks the claim.
        # (No incumbent -> _reclaimable False but _lease_expired True -> fresh.)
        is_reclaim = _reclaimable(existing, at)
        if existing and not is_reclaim:
            return {"claimed": False, "lead": existing, "reclaimed": False}
        if not existing and not _lease_expired(existing, at):
            # Defensive: should not happen (no doc => expired), keep explicit.
            return {"claimed": False, "lead": existing, "reclaimed": False}
        prior_lead = (existing or {}).get("lead") if isinstance(existing, dict) else None
        doc = _build_lead_doc(
            run_id=run_id,
            session_id=session_id,
            tool=tool,
            model=model,
            renew_every_minutes=renew_every_minutes,
            now=at,
            owns=owns,
            parent_lead=parent_lead,
            max_direct_reports=max_direct_reports,
            chunk_owners=chunk_owners,
            work_size=size,
            lease_secs=lease_secs,
        )
        _atomic_write(lead_path(channel_dir), doc)
        return {"claimed": True, "lead": doc, "reclaimed": is_reclaim, "prior_lead": prior_lead}

    result = _with_lock(channel_dir, _do)
    if result["claimed"]:
        _post_lead_record(
            channel_dir,
            kind=_KIND_CLAIM,
            tool=tool,
            model=model,
            run_id=run_id,
            app_slug=app_slug,
            lead=result["lead"]["lead"],
            workdir=workdir,
        )
        if result.get("reclaimed"):
            # Auditable auto-release record: who reclaimed, from whom, why.
            prior = result.get("prior_lead") or {}
            _post_lead_record(
                channel_dir,
                kind=_KIND_RECLAIM,
                tool=tool,
                model=model,
                run_id=run_id,
                app_slug=app_slug,
                lead=result["lead"]["lead"],
                extra={
                    "reclaim_reason": "stale-by-timeout",
                    "work_size": size,
                    "reclaimed_from_tool": prior.get("tool"),
                    "reclaimed_from_session": prior.get("session_id"),
                },
                workdir=workdir,
            )
    return result


def renew_lease(
    channel_dir: Path,
    *,
    session_id: str,
    app_slug: str,
    tool: str = "unknown",
    model: str = "unknown",
    renew_every_minutes: int | None = None,
    workdir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Extend the current lead's lease. Only the current lead may renew.

    Returns ``{"renewed": True, "lead": <doc>}`` on success, or
    ``{"renewed": False, "reason": "...", "lead": <doc-or-None>}`` when the
    caller is not the current lead or no lead exists.
    """
    at = now or _now()

    def _do() -> dict[str, Any]:
        doc = read_lead(channel_dir)
        if not doc or not isinstance(doc.get("lead"), dict):
            return {"renewed": False, "reason": "no_lead", "lead": None}
        lead = doc["lead"]
        if lead.get("session_id") != session_id:
            return {"renewed": False, "reason": "not_lead", "lead": doc}
        window = renew_every_minutes or int(
            lead.get("renew_every_minutes") or _DEFAULT_RENEW_MINUTES
        )
        lead["renew_every_minutes"] = window
        lead["lease_until"] = (at + timedelta(minutes=window)).isoformat()
        _atomic_write(lead_path(channel_dir), doc)
        return {"renewed": True, "lead": doc}

    result = _with_lock(channel_dir, _do)
    if result.get("renewed"):
        _post_lead_record(
            channel_dir,
            kind=_KIND_RENEW,
            tool=tool,
            model=model,
            run_id=result["lead"].get("run_id", "unknown"),
            app_slug=app_slug,
            lead=result["lead"]["lead"],
            workdir=workdir,
        )
    return result


def transfer_lead(
    channel_dir: Path,
    *,
    from_session_id: str,
    to_session_id: str,
    to_tool: str,
    to_model: str,
    app_slug: str,
    tool: str = "unknown",
    model: str = "unknown",
    workdir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Hand the lead from the current lead to another session.

    Rejected when the caller (``from_session_id``) is not the current lead.
    The lease window carries over from the outgoing lead. Returns
    ``{"transferred": bool, "reason"?: str, "lead": <doc-or-None>}``.
    """
    at = now or _now()

    def _do() -> dict[str, Any]:
        doc = read_lead(channel_dir)
        if not doc or not isinstance(doc.get("lead"), dict):
            return {"transferred": False, "reason": "no_lead", "lead": None}
        lead = doc["lead"]
        if lead.get("session_id") != from_session_id:
            return {"transferred": False, "reason": "not_lead", "lead": doc}
        window = int(lead.get("renew_every_minutes") or _DEFAULT_RENEW_MINUTES)
        lead["session_id"] = to_session_id
        lead["tool"] = to_tool
        lead["model"] = to_model
        lead["lease_until"] = (at + timedelta(minutes=window)).isoformat()
        _atomic_write(lead_path(channel_dir), doc)
        return {"transferred": True, "lead": doc}

    result = _with_lock(channel_dir, _do)
    if result.get("transferred"):
        _post_lead_record(
            channel_dir,
            kind=_KIND_TRANSFER,
            tool=tool,
            model=model,
            run_id=result["lead"].get("run_id", "unknown"),
            app_slug=app_slug,
            lead=result["lead"]["lead"],
            extra={"from_session_id": from_session_id,
                   "to_session_id": to_session_id},
            workdir=workdir,
        )
    return result


def relinquish_lead(
    channel_dir: Path,
    *,
    session_id: str,
    app_slug: str,
    tool: str = "unknown",
    model: str = "unknown",
    workdir: Path | None = None,
) -> dict[str, Any]:
    """Voluntarily give up the lead. Only the current lead may relinquish.

    Deletes ``lead.json`` so the next ``claim_lead`` succeeds immediately
    (rather than waiting for lease expiry). Returns
    ``{"relinquished": bool, "reason"?: str}``.
    """

    def _do() -> dict[str, Any]:
        doc = read_lead(channel_dir)
        if not doc or not isinstance(doc.get("lead"), dict):
            return {"relinquished": False, "reason": "no_lead", "lead": None}
        lead = doc["lead"]
        if lead.get("session_id") != session_id:
            return {"relinquished": False, "reason": "not_lead", "lead": doc}
        try:
            lead_path(channel_dir).unlink()
        except OSError:
            pass
        return {"relinquished": True, "lead": doc}

    result = _with_lock(channel_dir, _do)
    if result.get("relinquished"):
        _post_lead_record(
            channel_dir,
            kind=_KIND_RELINQUISH,
            tool=tool,
            model=model,
            run_id=result["lead"].get("run_id", "unknown"),
            app_slug=app_slug,
            lead=result["lead"]["lead"],
            workdir=workdir,
        )
    return result


def rebuild_lead_from_changes(channel_dir: Path) -> dict[str, Any] | None:
    """Reconstruct lead state from the ``changes.jsonl`` tail.

    Used when ``lead.json`` is missing/corrupt but the durable audit trail
    survives. Walks records newest-first; a ``lead-relinquish`` means there
    is no current lead, any other ``lead-*`` kind names the last holder.
    Returns a minimal ``{"schema_version", "run_id", "lead"}`` doc or None.
    """
    records, _offset = changes.read_changes_since(Path(channel_dir), 0)
    for record in reversed(records):
        kind = record.get("kind")
        if kind == _KIND_RELINQUISH:
            return None
        if kind in (_KIND_CLAIM, _KIND_RENEW, _KIND_TRANSFER):
            payload = record.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            return {
                "schema_version": _SCHEMA_VERSION,
                "run_id": payload.get("run_id") or record.get("run_id"),
                "lead": {
                    "session_id": payload.get("session_id"),
                    "tool": record.get("tool"),
                    "model": record.get("model"),
                    "lease_until": payload.get("lease_until"),
                },
                "_rebuilt_from": "changes.jsonl",
            }
    return None
