#!/usr/bin/env python3
"""App Pulse presence — live session liveness, reaper, and read cursor.

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
import time
from pathlib import Path

_SESSIONS_DIR = "sessions"
_CONFIG_NAME = "config.json"
_DEFAULT_HEARTBEAT_MIN = 15
_ZERO_CURSOR = {"revision": 0, "changes_offset": 0}


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
) -> None:
    """Write/refresh presence (overwrite-in-place). Preserves the cursor.

    Fire-and-forget: never raises, never blocks the host action.
    """
    try:
        p = _presence_path(channel_dir, session_id)
        cursor = dict(_ZERO_CURSOR)
        try:
            cursor = json.loads(p.read_text()).get("cursor", cursor)
        except (FileNotFoundError, OSError, ValueError):
            pass
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
    """Remove presence older than the heartbeat window. Returns reaped IDs."""
    cutoff = time.time() - heartbeat_minutes(channel_dir) * 60
    reaped: list = []
    for f, rec in _iter_presence(channel_dir):
        if float(rec.get("heartbeat_ts", 0)) < cutoff:
            try:
                f.unlink()
                reaped.append(rec.get("session_id", f.stem))
            except OSError:
                continue
    return reaped


def read_active_presence(channel_dir: Path, *, exclude_session: str) -> list:
    """Live peers (post-reap), excluding ``exclude_session``. Never locks."""
    reap_stale(channel_dir)
    out = []
    for _f, rec in _iter_presence(channel_dir):
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
    """Advance this session's own cursor in-place (preserves other fields)."""
    try:
        p = _presence_path(channel_dir, session_id)
        rec = json.loads(p.read_text())
        rec["cursor"] = {
            "revision": int(revision),
            "changes_offset": int(changes_offset),
        }
        _atomic_write(p, rec)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return
