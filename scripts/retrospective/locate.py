# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""locate.py — find the Claude Code session transcript for a given cwd.

Claude Code stores transcripts at::

    ~/.claude/projects/<cwd-slug>/<session-uuid>.jsonl

where ``<cwd-slug>`` is the absolute working directory with ``/`` replaced by
``-`` (e.g. ``/Users/<username>/dev/git-folder/build-loop`` →
``-Users-<username>-dev-git-folder-build-loop``).

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


def codex_sessions_root() -> Path:
    """Return the Codex CLI sessions root (``~/.codex/sessions/``)."""
    return Path.home() / ".codex" / "sessions"


def codex_transcript_cwd(path: Path) -> str | None:
    """Read a codex rollout's ``session_meta`` cwd (first line ``payload.cwd``)."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("type") == "session_meta":
                    return (rec.get("payload") or {}).get("cwd")
                return None  # meta is always first; bail once past it
    except (OSError, ValueError):
        return None
    return None


def _candidate_codex_rollouts(run_start, run_end) -> list[Path]:
    """Bounded set of rollout files: the run-window date dirs, else newest-by-mtime."""
    root = codex_sessions_root()
    if not root.is_dir():
        return []
    paths: list[Path] = []
    seen: set[Path] = set()
    for ts in (run_start, run_end):
        if ts is None:
            continue
        day_dir = root / f"{ts.year:04d}" / f"{ts.month:02d}" / f"{ts.day:02d}"
        if day_dir.is_dir():
            for p in day_dir.glob("rollout-*.jsonl"):
                if p not in seen:
                    paths.append(p)
                    seen.add(p)
    if paths:
        return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)
    # Fallback: newest 100 rollouts anywhere (bounds a full-tree walk).
    allp = sorted(root.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return allp[:100]


def find_codex_transcript_for_run(
    cwd: Path | str,
    *,
    run_start=None,
    run_end=None,
    run_host: str | None = None,
    bound_hours: float | None = None,
):
    """Locate the CODEX rollout that provably belongs to this run.

    Codex rollouts (``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``) are global (not
    cwd-slug-scoped like Claude transcripts), so attribution is DOUBLE-gated: the
    rollout's ``session_meta.cwd`` must match the run's repo AND its time span must pass
    temporal-membership against the run window (``record_host="codex"``). Returns
    ``(path, None)`` on a match, else ``(None, marker)``. Never raises.
    """
    kwargs = {} if bound_hours is None else {"bound_hours": bound_hours}
    marker = _tm.absence_marker(run_host, run_start, run_end, kind="codex-transcript")
    try:
        want_cwd = str(Path(cwd).expanduser().resolve())
    except (OSError, ValueError):
        want_cwd = str(cwd)
    last_reason = None
    for path in _candidate_codex_rollouts(run_start, run_end):
        rc = codex_transcript_cwd(path)
        if rc is not None:
            try:
                if str(Path(rc).expanduser().resolve()) != want_cwd:
                    continue  # different repo — not this run
            except (OSError, ValueError):
                if rc != want_cwd:
                    continue
        first, last = transcript_time_span(path)  # top-level "timestamp" per line
        ok, reason = _tm.is_member(
            first, last, run_start, run_end,
            record_host="codex", run_host=run_host, **kwargs,
        )
        if ok:
            return path, None
        last_reason = reason
    if last_reason:
        marker += f" — nearest candidate {last_reason}"
    return None, marker


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
    run. Never raises.

    Two sources, in order:
      1. Claude Code transcripts (``~/.claude/projects/<slug>/``, host ``claude_code``).
      2. Codex rollouts (``~/.codex/sessions/``, host ``codex``) — added so a codex-hosted
         run gets a REAL transcript source instead of only an absence marker (retro §10/§11
         came back empty on codex runs). Skipped only when the run is KNOWN to be
         ``claude_code``-hosted.

    This extends the RCA 2026-07-11 fix (422a5c1): the old locator silently substituted a
    ~3-week-stale Claude transcript for a codex run; now we neither substitute NOR leave
    codex runs sourceless — we find the codex rollout when one provably belongs.
    """
    kwargs = {} if bound_hours is None else {"bound_hours": bound_hours}
    marker = _tm.absence_marker(run_host, run_start, run_end, kind="transcript")
    last_reason = None
    try:
        slug = cwd_to_slug(cwd)
        root = sessions_root() / slug
        if root.is_dir():
            jsonls = sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in jsonls:
                first, last = transcript_time_span(path)
                ok, reason = _tm.is_member(
                    first, last, run_start, run_end,
                    record_host="claude_code", run_host=run_host, **kwargs,
                )
                if ok:
                    return path, None
                last_reason = reason
    except (OSError, ValueError):
        pass
    # Source 2: codex rollouts, unless the run is explicitly claude_code-hosted.
    if str(run_host or "").lower() != "claude_code":
        codex_path, _codex_reason = find_codex_transcript_for_run(
            cwd, run_start=run_start, run_end=run_end, run_host=run_host,
            bound_hours=bound_hours,
        )
        if codex_path is not None:
            return codex_path, None
    if last_reason:
        marker += f" — nearest candidate {last_reason}"
    return None, marker
