#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rally Point channel lifecycle hygiene.

The resolved app channel is append-only across
``changes.jsonl`` and accumulates one ``sessions/<session-id>.json`` per
heartbeat-live process. Without explicit cleanup, the channel becomes a
graveyard:

    - ``sessions/`` collects stale heartbeats that mislead peer-detection
      (Rally Point may classify a dead process as a "live peer" until the
      staleness threshold passes — minutes after a run ended).
    - ``changes.jsonl`` grows unbounded; after dozens of runs it's a
      single fat file that's expensive to scan.

This module ships the three functions the orchestrator's Phase D
closeout calls (per references/coordination-rules.md §"Closeout
hygiene" — Option A accepted by Codex at rev 34):

    reap_my_sessions(channel_dir, session_id) -> int
        Delete THIS run's session presence file. Fire-and-forget.

    reap_stale_sessions(channel_dir, stale_after_seconds=3600) -> int
        Delete any presence file whose mtime is older than threshold.
        Defense-in-depth for crashed peers that never ran closeout.

    rotate_changes_log(channel_dir, max_mb=1, max_entries=500) -> Path | None
        When changes.jsonl exceeds either threshold, rotate it to
        ``changes.jsonl.<YYYY-MM-DD>`` and start a fresh file.

Plus one more, ``resolve_addressed_handoffs``, that closes the loop on
Rally handoffs a run actually addressed. Root cause: ARP's
``rally say receipt --ref <event_id>`` is the verified handoff-close
primitive (empirically closes a handoff), but no build-loop automated
path ever called it, so addressed handoffs stayed open forever. ``rally
ack`` is a rules-ack, NOT a handoff-close, and must never be used for
this purpose:

    resolve_addressed_handoffs(repo_root, tool, handoff_event_ids, dry_run=False) -> list[str]
        For each event_id, shell out to ``rally say receipt --ref
        <event_id>``. Fire-and-forget per id. Returns the subset of ids
        that actually resolved (or, under dry_run, the full input list,
        unfired).

All functions are fire-and-forget — errors are swallowed; the
orchestrator must never crash because cleanup hit a permission error.
``reap_my_sessions`` returns the count of files removed; callers that
care can log it but no caller has a hard dependency on the count.

Design notes:
    - Why a separate "reap MY session" function distinct from
      ``presence.reap_stale``: presence.reap_stale only removes a file
      when its heartbeat is older than the staleness window
      (heartbeat_minutes). Phase D closeout runs IMMEDIATELY after the
      final post; the session's heartbeat is still fresh. We want to
      delete it ANYWAY because the orchestrator knows it just finished.
    - Why ``reap_stale_sessions`` is independent of
      ``presence.reap_stale``: presence's reaper is opportunistic at
      ``checkpoint_read`` time and uses a 15-minute default. The
      lifecycle variant is for explicit cleanup at run-closeout with a
      tunable threshold (default 1 hour for defense-in-depth).
    - Why rotation uses MB + entries (either): chunks differ wildly in
      record size; either bound catches the runaway.
    - Why not delete rotated logs: rotated files are a historical record
      and may be useful for cross-run pattern analysis (Phase 6 Learn).
      Aging out is a separate concern handled by the user / cron.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

_SESSIONS_DIR_NAME = "sessions"
_LOG_NAME = "changes.jsonl"
_RECEIPT_TIMEOUT_SECONDS = 10.0


def _rally_binary(repo_root: Path) -> str:
    """Resolve the ``rally`` binary the same way the rest of the bridge does.

    Delegates to ``discovery_bridge.rust_rally_binary`` (repo-associated
    candidates, ``AGENT_RALLY_BINARY``, PATH, fetched cache); falls back to
    the bare ``"rally"`` command (resolved via the child process's PATH) so
    this stays fail-open when the discovery module is unavailable.
    """
    try:
        from . import discovery_bridge as _disc
    except ImportError:  # script-mode
        try:
            import discovery_bridge as _disc  # type: ignore
        except ImportError:
            return "rally"
    return _disc.rust_rally_binary(repo_root) or "rally"


def _full_capability_for_channel(channel_dir: Path) -> bool:
    """True only when a full-capability Rust binary owns this channel.

    Delegates to the single capability guard; fail-CLOSED on any error so a
    degraded session never reaps a peer's presence file.
    """
    try:
        from . import capability as _cap
    except ImportError:  # script-mode
        try:
            import capability as _cap  # type: ignore
        except ImportError:
            return False
    return _cap.full_capability_for_channel(channel_dir)


def _sessions_dir(channel_dir: Path) -> Path:
    return Path(channel_dir) / _SESSIONS_DIR_NAME


def reap_my_sessions(channel_dir: Path, session_id: str) -> int:
    """Delete this session's presence file. Returns count reaped.

    Fire-and-forget: returns 0 on any error. Idempotent — calling twice
    is safe. Closeout protocol calls this AFTER the final post() so the
    presence is no longer needed; peer sessions reading
    ``read_active_presence`` immediately stop counting this session.
    """
    try:
        p = _sessions_dir(Path(channel_dir)) / f"{session_id}.json"
        if p.exists():
            p.unlink()
            return 1
        return 0
    except OSError:
        return 0


def reap_stale_sessions(
    channel_dir: Path, stale_after_seconds: int = 3600
) -> int:
    """Delete every PEER presence file with mtime older than threshold.

    Defense-in-depth for crashed peers. The default 1 hour is intentionally
    larger than presence.py's 15-minute heartbeat window — this is for
    sessions that crashed and never reached Phase D closeout but whose
    heartbeat process is genuinely dead.

    RUST-ONLY guard: this physically unlinks PEER presence files, so it is gated
    behind full coordination capability exactly like ``presence.reap_stale`` — a
    degraded/unavailable session must never reap a peer it cannot prove is dead.
    Below full capability it is a fail-closed no-op (returns 0). (Reaping THIS
    run's own session is unconditional — see ``reap_my_sessions``.)

    Returns count reaped. Fire-and-forget; errors swallowed.
    """
    try:
        if not _full_capability_for_channel(Path(channel_dir)):
            return 0  # Rust-only: never reap a peer below full capability
        sd = _sessions_dir(Path(channel_dir))
        if not sd.is_dir():
            return 0
        cutoff = _now_seconds() - max(0, int(stale_after_seconds))
        reaped = 0
        for f in sd.glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    reaped += 1
            except OSError:
                continue
        return reaped
    except OSError:
        return 0


def rotate_changes_log(
    channel_dir: Path,
    *,
    max_mb: int = 1,
    max_entries: int = 500,
) -> Path | None:
    """Rotate changes.jsonl when it exceeds either threshold.

    Rotation: rename current ``changes.jsonl`` to
    ``changes.jsonl.<YYYY-MM-DD>`` (with numeric suffix if same-day
    rotation collides). Subsequent ``append_change`` calls re-create the
    main file via ``O_CREAT``.

    Returns the rotated-to path on rotation, or ``None`` when under the
    thresholds (or on error — fire-and-forget). Either bound triggers
    rotation — MB protects against record-size blowouts, entry count
    protects against many-small-records accumulation.

    Note: this function ROTATES (renames). It does not delete history.
    Aging out rotated files is the caller's / cron's concern.
    """
    try:
        cd = Path(channel_dir)
        log = cd / _LOG_NAME
        if not log.is_file():
            return None
        size_mb = log.stat().st_size / (1024 * 1024)
        if size_mb < max(0.0001, float(max_mb)):
            # Check entries too — only count if size is below
            if _count_lines(log) < max(1, int(max_entries)):
                return None
        else:
            # size already over threshold; skip entry count
            pass
        date = _dt.date.today().isoformat()
        target = cd / f"{_LOG_NAME}.{date}"
        if target.exists():
            i = 2
            while (cd / f"{_LOG_NAME}.{date}.{i}").exists():
                i += 1
            target = cd / f"{_LOG_NAME}.{date}.{i}"
        os.rename(str(log), str(target))
        return target
    except OSError:
        return None


def resolve_addressed_handoffs(
    repo_root: Path,
    tool: str,
    handoff_event_ids: list[str],
    *,
    dry_run: bool = False,
) -> list[str]:
    """Close out Rally handoffs this run actually addressed.

    For each ``event_id`` in ``handoff_event_ids``, shells out to::

        rally say receipt --tool <tool> --ref <event_id> \\
            --subject 'run-closeout: handoff addressed' --json

    ``rally say receipt --ref <event_id>`` is ARP's verified handoff-close
    primitive. This function is the only automated caller of it in
    build-loop — before this, handoffs stayed open forever because nothing
    ever called the close primitive. Do NOT reuse ``rally ack`` here: ack is
    a rules-ack, not a handoff-close, and calling it would silently leave
    the handoff open while looking like progress.

    Fire-and-forget per id: a failing or raising subprocess call for one
    event_id is swallowed and excluded from the return value; it never
    prevents the remaining ids from being attempted.

    ``dry_run=True`` performs no subprocess calls at all and returns
    ``list(handoff_event_ids)`` unchanged — the ids that WOULD be resolved.

    The caller (not this function) is responsible for knowing which
    handoffs were actually addressed this run; this function only takes an
    explicit id list so behavior stays deterministic and testable.
    """
    ids = list(handoff_event_ids)
    if dry_run:
        return ids

    binary = _rally_binary(Path(repo_root))
    resolved: list[str] = []
    for event_id in ids:
        try:
            proc = subprocess.run(
                [
                    binary,
                    "say",
                    "receipt",
                    "--tool",
                    tool,
                    "--ref",
                    event_id,
                    "--subject",
                    "run-closeout: handoff addressed",
                    "--json",
                ],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=_RECEIPT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            resolved.append(event_id)
    return resolved


def _count_lines(path: Path) -> int:
    """Count newline-terminated lines in ``path``. Fast, stdlib-only."""
    try:
        c = 0
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(64 * 1024)
                if not chunk:
                    break
                c += chunk.count(b"\n")
        return c
    except OSError:
        return 0


def _now_seconds() -> float:
    """Inject point for testing time-based reaping."""
    import time
    return time.time()
