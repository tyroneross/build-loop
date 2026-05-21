#!/usr/bin/env python3
"""Rally Point presence — live session liveness, reaper, and read cursor.

One ``sessions/<session-id>.json`` per live session, overwrite-in-place
via tmp+rename (atomic, no partial reads). Each carries the per-session
read cursor (``revision`` + ``changes.jsonl`` byte offset) so checkpoint
reads are delta-only.

Reaper: a presence file whose ``heartbeat_ts`` is older than
``heartbeat_minutes`` (default 15, overridable via the channel's
``config.json`` — OQ2) is stale and removed. No daemon: ``reap_stale``
runs opportunistically at each checkpoint read.

All reads no-op gracefully when the channel/sessions dir is absent
(returns empty / zero-cursor; lazy-create on write only).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

_SESSIONS_DIR = "sessions"
_CONFIG_NAME = "config.json"
_DEFAULT_HEARTBEAT_MIN = 15
_ZERO_CURSOR = {"revision": 0, "changes_offset": 0}
_GIT_TIMEOUT_S = 0.5  # cap any single git call; fail-open on timeout
_UNKNOWN_BRANCH = {
    "branch_name": "unknown",
    "branch_head_sha": "unknown",
    "branch_merge_status": "unknown",
}


def _compute_branch_status(cwd: Path) -> dict:
    """Return branch_name, branch_head_sha, branch_merge_status for cwd.

    Fail-open: any git error, timeout, detached HEAD, or non-git dir
    returns the all-``unknown`` record. Never raises. ~5 ms per call on
    a healthy repo.

    Merge-status check: ``git merge-base --is-ancestor HEAD <upstream>``
    where upstream is ``origin/main`` with fallback to ``main``. Exit 0
    means HEAD is an ancestor of (i.e. merged into) the upstream tip.
    Squash-merged branches return ``unmerged`` here — file-level fallback
    lives in checkpoint._peer_files_already_landed.
    """
    rec = dict(_UNKNOWN_BRANCH)
    try:
        cwd_str = str(cwd)
        # Branch name (detached HEAD -> "HEAD"; we still return that as-is).
        r = subprocess.run(
            ["git", "-C", cwd_str, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
        if r.returncode == 0 and r.stdout.strip():
            rec["branch_name"] = r.stdout.strip()
        else:
            return rec  # not a git repo (or worse) — bail
        # HEAD SHA.
        r = subprocess.run(
            ["git", "-C", cwd_str, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
        if r.returncode == 0 and r.stdout.strip():
            rec["branch_head_sha"] = r.stdout.strip()
        else:
            return rec
        # Merge-status: try origin/main first, fall back to main.
        for upstream in ("origin/main", "main"):
            # Verify upstream exists before --is-ancestor (cheaper failure).
            v = subprocess.run(
                ["git", "-C", cwd_str, "rev-parse", "--verify", "--quiet",
                 upstream],
                capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
            )
            if v.returncode != 0:
                continue
            a = subprocess.run(
                ["git", "-C", cwd_str, "merge-base", "--is-ancestor",
                 rec["branch_head_sha"], upstream],
                capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
            )
            if a.returncode == 0:
                rec["branch_merge_status"] = "merged"
            elif a.returncode == 1:
                rec["branch_merge_status"] = "unmerged"
            # other exit codes (128 etc.) fall through to "unknown"
            return rec
        return rec  # neither upstream resolved
    except (subprocess.SubprocessError, OSError, ValueError):
        return dict(_UNKNOWN_BRANCH)


def _sessions_dir(channel_dir: Path) -> Path:
    return Path(channel_dir) / _SESSIONS_DIR


def _presence_path(channel_dir: Path, session_id: str) -> Path:
    return _sessions_dir(channel_dir) / f"{session_id}.json"


def heartbeat_minutes(channel_dir: Path) -> int:
    """Stale window in minutes (config.json override, default 15)."""
    try:
        cfg = json.loads((Path(channel_dir) / _CONFIG_NAME).read_text())
        v = int(cfg.get("heartbeat_minutes", _DEFAULT_HEARTBEAT_MIN))
        return v if v > 0 else _DEFAULT_HEARTBEAT_MIN
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return _DEFAULT_HEARTBEAT_MIN


def _atomic_write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    os.replace(str(tmp), str(path))


def write_presence(
    channel_dir: Path,
    *,
    session_id: str,
    tool: str,
    model: str,
    run_id: str,
    app_slug: str,
    phase: str,
    files_in_flight: list | None = None,
    cwd: Path | None = None,
) -> None:
    """Write/refresh presence (overwrite-in-place). Preserves the cursor.

    ``cwd`` (optional) — the working directory whose branch state should
    be recorded. When omitted, ``Path.cwd()`` is used. The branch fields
    (``branch_name``, ``branch_head_sha``, ``branch_merge_status``,
    ``branch_merge_status_checked_ts``) are computed via
    ``_compute_branch_status``; any git failure yields ``"unknown"``.

    Fire-and-forget: never raises, never blocks the host action.
    """
    try:
        p = _presence_path(channel_dir, session_id)
        cursor = dict(_ZERO_CURSOR)
        try:
            cursor = json.loads(p.read_text()).get("cursor", cursor)
        except (FileNotFoundError, OSError, ValueError):
            pass
        branch = _compute_branch_status(cwd if cwd is not None else Path.cwd())
        rec = {
            "session_id": session_id,
            "tool": tool or "unknown",
            "model": model or "unknown",
            "run_id": run_id or "unknown",
            "app_slug": app_slug,
            "phase": phase,
            "files_in_flight": list(files_in_flight or []),
            "heartbeat_ts": time.time(),
            "cursor": cursor,
            "branch_name": branch["branch_name"],
            "branch_head_sha": branch["branch_head_sha"],
            "branch_merge_status": branch["branch_merge_status"],
            "branch_merge_status_checked_ts": time.time(),
            "cwd": str(cwd) if cwd is not None else str(Path.cwd()),
        }
        _atomic_write(p, rec)
    except Exception:  # noqa: BLE001 — fire-and-forget
        return


def _iter_presence(channel_dir: Path):
    sd = _sessions_dir(channel_dir)
    try:
        names = list(sd.glob("*.json"))
    except OSError:
        return
    for f in names:
        try:
            yield f, json.loads(f.read_text())
        except (OSError, ValueError):
            continue


def reap_stale(channel_dir: Path) -> list:
    """Remove stale *live* presence. Returns reaped session IDs.

    Pure-reader cursor stubs (``tool == "reader"``, ``heartbeat_ts``
    intentionally 0) are NOT reaped — they are not live peers and their
    sole purpose is to persist a delta cursor between polls. Only real
    live presence (a positive heartbeat older than the window) is
    removed.
    """
    cutoff = time.time() - heartbeat_minutes(channel_dir) * 60
    reaped: list = []
    for f, rec in _iter_presence(channel_dir):
        if rec.get("tool") == "reader":
            continue  # cursor stub — keep, never a peer
        if float(rec.get("heartbeat_ts", 0)) < cutoff:
            try:
                f.unlink()
                reaped.append(rec.get("session_id", f.stem))
            except OSError:
                continue
    return reaped


def read_active_presence(channel_dir: Path, *, exclude_session: str) -> list:
    """Live peers (post-reap), excluding ``exclude_session`` and reader
    cursor stubs. Never locks."""
    reap_stale(channel_dir)
    out = []
    for _f, rec in _iter_presence(channel_dir):
        if rec.get("tool") == "reader":
            continue  # cursor stub is not a peer
        if rec.get("session_id") != exclude_session:
            out.append(rec)
    return out


def get_cursor(channel_dir: Path, session_id: str) -> dict:
    """Return this session's read cursor (zero-cursor if absent)."""
    try:
        rec = json.loads(
            _presence_path(channel_dir, session_id).read_text()
        )
        c = rec.get("cursor", {})
        return {
            "revision": int(c.get("revision", 0)),
            "changes_offset": int(c.get("changes_offset", 0)),
        }
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return dict(_ZERO_CURSOR)


def set_cursor(
    channel_dir: Path, session_id: str, *, revision: int, changes_offset: int
) -> None:
    """Advance this session's own cursor (preserves other fields).

    Pure readers (the SessionStart / pre-edit hooks) have no presence
    file of their own, yet their cursor MUST persist or every poll
    re-surfaces the same delta. So when no presence file exists we write
    a minimal cursor-only stub with ``heartbeat_ts: 0`` — the reaper
    treats it as long-stale (never a "live peer") and eventually cleans
    it, but the cursor survives between polls. Delta-only reads for
    readers are thus first-class, not a special case.
    """
    try:
        p = _presence_path(channel_dir, session_id)
        try:
            rec = json.loads(p.read_text())
        except (FileNotFoundError, OSError, ValueError):
            rec = {
                "session_id": session_id,
                "tool": "reader",
                "model": "n/a",
                "run_id": "n/a",
                "app_slug": "",
                "phase": "reader",
                "files_in_flight": [],
                "heartbeat_ts": 0,  # never counts as a live peer
            }
        rec["cursor"] = {
            "revision": int(revision),
            "changes_offset": int(changes_offset),
        }
        _atomic_write(p, rec)
    except (OSError, ValueError, TypeError):
        return
