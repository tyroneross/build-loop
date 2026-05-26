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
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

_SESSIONS_DIR_NAME = "sessions"
_LOG_NAME = "changes.jsonl"


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
    """Delete every presence file with mtime older than threshold.

    Defense-in-depth for crashed peers. The default 1 hour is intentionally
    larger than presence.py's 15-minute heartbeat window — this is for
    sessions that crashed and never reached Phase D closeout but whose
    heartbeat process is genuinely dead.

    Returns count reaped. Fire-and-forget; errors swallowed.
    """
    try:
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
