#!/usr/bin/env python3
"""App Pulse rally pointer helpers.

``changes.jsonl`` is the immutable audit trail. ``rally/current.json`` is the
small mutable index that lets a fresh session find the latest active rally
without scanning the whole log.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any

try:  # package import
    from . import changes
except ImportError:  # script import
    import changes  # type: ignore

_RALLY_DIR = "rally"
_CURRENT_NAME = "current.json"
_LOCK_NAME = "current.lock"


def _rally_dir(channel_dir: Path) -> Path:
    return Path(channel_dir) / _RALLY_DIR


def current_path(channel_dir: Path) -> Path:
    return _rally_dir(channel_dir) / _CURRENT_NAME


def _lock_path(channel_dir: Path) -> Path:
    return _rally_dir(channel_dir) / _LOCK_NAME


def _atomic_write(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(obj, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _record_revision(record: dict[str, Any]) -> int:
    try:
        return int(record.get("revision", 0))
    except (TypeError, ValueError):
        return 0


def _envelope_from_record(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    phase = str(payload.get("phase") or "")
    status = "closed" if phase in {"run-closeout", "closeout"} else "active"
    return {
        "schema_version": "1.0",
        "app_slug": record.get("app_slug"),
        "run_id": record.get("run_id"),
        "latest_session_id": payload.get("session_id"),
        "latest_revision": _record_revision(record),
        "latest_phase": phase,
        "status": status,
        "coord_file": payload.get("coord_file"),
        "tool": record.get("tool"),
        "model": record.get("model"),
        "updated_at": record.get("ts"),
        "source_kind": record.get("kind"),
    }


def read_current(channel_dir: Path) -> dict[str, Any] | None:
    """Read ``rally/current.json``. Returns None when absent or invalid."""
    try:
        data = json.loads(current_path(Path(channel_dir)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_current(channel_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Write ``rally/current.json`` from a change-log record.

    The pointer is monotonic by ``revision``: if a lower-revision writer races a
    higher-revision writer, the lower revision is ignored under the same lock.
    """
    d = _rally_dir(Path(channel_dir))
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_lock_path(channel_dir)), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        existing = read_current(channel_dir)
        next_env = _envelope_from_record(record)
        if existing:
            try:
                existing_rev = int(existing.get("latest_revision", 0))
            except (TypeError, ValueError):
                existing_rev = 0
            if existing_rev > next_env["latest_revision"]:
                return existing
        _atomic_write(current_path(channel_dir), next_env)
        return next_env
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def rebuild_current(channel_dir: Path) -> dict[str, Any] | None:
    """Rebuild current pointer from the latest rally-start phase event."""
    records, _offset = changes.read_changes_since(Path(channel_dir), 0)
    for record in reversed(records):
        payload = record.get("payload") or {}
        if (
            record.get("kind") == "phase"
            and isinstance(payload, dict)
            and payload.get("phase") == "rally-start"
        ):
            return write_current(channel_dir, record)
    return None
