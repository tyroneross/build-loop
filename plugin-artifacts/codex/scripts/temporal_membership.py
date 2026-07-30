# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""temporal_membership.py — gate attaching an external record to a run by time + host overlap.

WHY THIS EXISTS (named, observed failure — RCA 2026-07-11)
----------------------------------------------------------
Three retro/audit code paths attached the "nearest available" record as if it belonged to
the run, with no check that the record actually came from that run:

  1. The retrospective transcript locator returned the most-recently-modified
     ``~/.claude/projects/<slug>/*.jsonl`` with no time-window check. On a codex-hosted run
     (no Claude Code transcript exists for it) it silently substituted an unrelated
     transcript spanning ~3 weeks earlier, fabricating 6 of 11 retrospective sections.
  2. Judge-decision merging folded every entry in ``judge-decisions.json`` into the target
     run with no timestamp filter — a month-old ``approve`` verdict surfaced as this run's
     "what went well".
  3. The independent-auditor hook write path appended judge packets to ``runs[-1]``
     regardless of whether the trigger time fell inside that run's own window.

The common defect is "nearest-in-time-but-wrong": a record attaches by availability, not by
verified relevance. This helper is the ONE membership test all three sites share.

CONTRACT
--------
A record (transcript, verdict, packet) may attach to a run only when BOTH hold:
  - its time range OVERLAPS the run's window (bounded by ``bound_hours`` tolerance), AND
  - where both host labels are known, the record's host MATCHES the run's host.

On failure the caller emits an explicit absence marker
(``no <kind> for this run (host=X, window=Y)``) instead of substituting the wrong record.

Pure stdlib. Non-raising on parse — an unparseable timestamp degrades to ``None`` (open
bound), never an exception, so a caller can stay fail-soft.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

# A same-day run always contains its own records; 24h of slop absorbs timezone skew
# between a UTC ``date`` field and a local commit timestamp without admitting a
# 3-week-stale transcript (the observed failure was ~21 days out).
DEFAULT_BOUND_HOURS = 24.0

# Timestamp field names a run/record may carry, in preference order.
_START_KEYS = ("started_at", "start", "date", "timestamp", "ts")
_END_KEYS = ("ended_at", "end", "finished_at", "date", "timestamp", "ts")


def parse_ts(value: Any) -> _dt.datetime | None:
    """Parse a timestamp into a tz-aware UTC datetime, or None.

    Accepts ISO-8601 (``2026-07-10T08:37:46Z``, ``...+00:00``, with or without
    microseconds) and the compact build-loop run-id form ``20260710T083746Z``.
    Never raises — anything unparseable returns None (an open time bound).
    """
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # Normalize a trailing Z to an explicit UTC offset for fromisoformat.
    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = _dt.datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        pass
    # Compact form: 20260710T083746Z / 20260710T083746
    compact = s[:-1] if s.endswith("Z") else s
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            return _dt.datetime.strptime(compact, fmt).replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
    return None


def _first_ts(d: dict[str, Any], keys: tuple[str, ...]) -> _dt.datetime | None:
    for k in keys:
        if k in d:
            got = parse_ts(d.get(k))
            if got is not None:
                return got
    return None


def run_window(
    run: dict[str, Any] | None,
    *,
    commit_timestamps: list[Any] | None = None,
) -> tuple[_dt.datetime | None, _dt.datetime | None]:
    """Return the ``(start, end)`` window for a state.json ``runs[]`` entry.

    The start comes from the run's ``date`` (or ``started_at``/``start``). The end is the
    latest of that start and any supplied commit timestamps (a run whose commits land after
    its recorded ``date`` still owns them). Returns ``(None, None)`` when nothing parses —
    an open window, which ``is_member`` treats conservatively.
    """
    run = run or {}
    start = _first_ts(run, _START_KEYS)
    end = _first_ts(run, _END_KEYS) or start
    for ct in commit_timestamps or []:
        cdt = parse_ts(ct)
        if cdt is None:
            continue
        if end is None or cdt > end:
            end = cdt
        if start is None or cdt < start:
            start = cdt
    return start, end


def _days_between(a: _dt.datetime, b: _dt.datetime) -> int:
    return abs(int(round((a - b).total_seconds() / 86400.0)))


def is_member(
    record_start: _dt.datetime | None,
    record_end: _dt.datetime | None,
    run_start: _dt.datetime | None,
    run_end: _dt.datetime | None,
    *,
    record_host: str | None = None,
    run_host: str | None = None,
    bound_hours: float = DEFAULT_BOUND_HOURS,
) -> tuple[bool, str]:
    """Decide whether a record belongs to a run.

    Returns ``(is_member, reason)``. ``reason`` is empty on membership and, on rejection,
    names WHY (``host mismatch: ...`` / ``stale by Nd`` / ``postdates run window by Nd``).

    A ``None`` bound is treated as open (unknown), so a record with no timestamps is not
    rejected on time alone — the observed failures all carried concrete, provably-wrong
    timestamps; this stays conservative rather than dropping legitimately-undated records.
    """
    rh = (record_host or "").strip()
    wh = (run_host or "").strip()
    if rh and wh and rh != wh:
        return False, f"host mismatch: record host={rh}, run host={wh}"

    bound = _dt.timedelta(hours=max(0.0, bound_hours))
    # Record ends before the run window opens (minus tolerance) → stale.
    if record_end is not None and run_start is not None and record_end < run_start - bound:
        return False, (
            f"stale by {_days_between(run_start, record_end)}d "
            f"(record ends {record_end.date()}, run window opens {run_start.date()})"
        )
    # Record starts after the run window closes (plus tolerance) → postdates.
    if record_start is not None and run_end is not None and record_start > run_end + bound:
        return False, (
            f"postdates run window by {_days_between(record_start, run_end)}d "
            f"(record starts {record_start.date()}, run window closes {run_end.date()})"
        )
    return True, ""


def absence_marker(
    run_host: str | None,
    run_start: _dt.datetime | None,
    run_end: _dt.datetime | None,
    *,
    kind: str = "transcript",
) -> str:
    """Explicit marker a caller emits in place of a substituted wrong record."""
    host = (run_host or "unknown").strip() or "unknown"
    if run_start is None and run_end is None:
        window = "unknown"
    else:
        s = run_start.date().isoformat() if run_start else "?"
        e = run_end.date().isoformat() if run_end else "?"
        window = s if s == e else f"{s}..{e}"
    return f"no {kind} for this run (host={host}, window={window})"
