# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""locate.py — find the Claude Code session transcript for a given cwd.

Claude Code stores transcripts at::

    ~/.claude/projects/<cwd-slug>/<session-uuid>.jsonl

where ``<cwd-slug>`` is the absolute working directory with ``/`` replaced by
``-`` (e.g. ``/Users/tyroneross/dev/git-folder/build-loop`` →
``-Users-tyroneross-dev-git-folder-build-loop``).

This module returns the **most-recently-modified** JSONL for the given cwd,
or None when none exists. It is the locator the ``transcript_pattern_miner``
package uses, exposed as a public helper for the retrospective agent.
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    import temporal_membership as _tm
except ImportError:  # pragma: no cover - path fallback when scripts/ not on sys.path
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import temporal_membership as _tm


def cwd_to_slug(cwd: Path | str) -> str:
    """Convert an absolute cwd to its Claude Code slug.

    The slug is the absolute path with leading slash stripped and remaining
    ``/`` replaced by ``-`` (Claude Code's convention).
    """
    p = Path(cwd).resolve()
    abs_str = str(p)
    # Strip the leading '/' (POSIX absolute path) so the slug starts with '-'.
    if abs_str.startswith("/"):
        abs_str = abs_str[1:]
    return "-" + abs_str.replace("/", "-")


def sessions_root() -> Path:
    """Return the Claude Code sessions root (``~/.claude/projects/``)."""
    return Path.home() / ".claude" / "projects"


def find_transcript_for_cwd(cwd: Path | str) -> Path | None:
    """Return the most-recently-modified JSONL for ``cwd``, or None.

    Args:
        cwd: absolute working directory of the build-loop run.

    Returns:
        Path to the JSONL transcript, or None if no transcript directory or
        no JSONL files exist for this cwd.

    Never raises — IO errors return None.
    """
    try:
        slug = cwd_to_slug(cwd)
        root = sessions_root() / slug
        if not root.is_dir():
            return None
        jsonls = sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return jsonls[0] if jsonls else None
    except (OSError, ValueError):
        return None


def transcript_time_span(path: Path | str) -> tuple:
    """Return ``(first_ts, last_ts)`` datetimes from a transcript JSONL, or ``(None, None)``.

    Reads the ``timestamp`` field Claude Code stamps on each record. Used to decide whether
    a candidate transcript's time span overlaps a run's window. Never raises.
    """
    first = last = None
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _tm.parse_ts(rec.get("timestamp"))
                if ts is None:
                    continue
                if first is None:
                    first = ts
                last = ts
    except OSError:
        return None, None
    return first, last


def find_transcript_for_run(
    cwd: Path | str,
    *,
    run_start=None,
    run_end=None,
    run_host: str | None = None,
    bound_hours: float | None = None,
):
    """Locate the transcript that PROVABLY belongs to this run.

    Unlike :func:`find_transcript_for_cwd` (newest-wins, no time check), this walks
    candidates newest-first and returns the first whose time span AND host pass the
    temporal-membership check against the run window. Returns ``(path, None)`` on a match,
    or ``(None, reason)`` with an explicit absence marker when no candidate belongs to the
    run — e.g. a codex-hosted run for which no Claude Code transcript exists. Never raises.

    This is the fix for RCA 2026-07-11: the old locator silently substituted a ~3-week-stale
    transcript for a codex run, fabricating 6 of 11 retrospective sections.
    """
    kwargs = {} if bound_hours is None else {"bound_hours": bound_hours}
    marker = _tm.absence_marker(run_host, run_start, run_end, kind="transcript")
    try:
        slug = cwd_to_slug(cwd)
        root = sessions_root() / slug
        if not root.is_dir():
            return None, marker
        jsonls = sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except (OSError, ValueError):
        return None, marker
    if not jsonls:
        return None, marker
    # A Claude Code transcript is, by construction, a claude_code-host record.
    record_host = "claude_code"
    last_reason = None
    for path in jsonls:
        first, last = transcript_time_span(path)
        ok, reason = _tm.is_member(
            first, last, run_start, run_end,
            record_host=record_host, run_host=run_host, **kwargs,
        )
        if ok:
            return path, None
        last_reason = reason
    if last_reason:
        marker += f" — nearest candidate {last_reason}"
    return None, marker
