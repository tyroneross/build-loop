#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Task heartbeat records for long-running Rally work.

Presence answers "can this session still write?". Task heartbeat answers
"is this session still working on the claimed task, and does it need attention?".
The local fallback is a bounded latest-per-session/task snapshot. Status/watch
read only that compact projection; native Rally continues to carry heartbeats
as ordinary facts.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import stat
import time
from pathlib import Path
from typing import Any

_HEARTBEAT_DIR = "task-heartbeats"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
DEFAULT_INTERVAL_SECONDS = 600
DEFAULT_GRACE_SECONDS = 60
MAX_SNAPSHOT_RECORDS = 256
MAX_SNAPSHOT_BYTES = 512 * 1024
MAX_RECORD_BYTES = 16 * 1024
_LOCK_TIMEOUT_SECONDS = 1.0
_RETENTION_KIND = "task-heartbeat-retention"
STATUSES = {
    "running",
    "blocked",
    "waiting",
    "reviewing",
    "needs_attention",
    "done_pending_release",
}


def _safe_name(value: str) -> str:
    raw = (value or "unknown").strip()
    cleaned = _SAFE_NAME_RE.sub("_", raw)
    cleaned = cleaned.strip("._")
    cleaned = cleaned or "unknown"
    if len(cleaned) <= 96:
        return cleaned
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{cleaned[:83].rstrip('._-')}-{digest}"


def heartbeat_path(channel_dir: Path, tool: str) -> Path:
    return Path(channel_dir) / _HEARTBEAT_DIR / f"{_safe_name(tool)}.jsonl"


def _lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def _acquire_write_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        str(_lock_path(path)),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                os.close(fd)
                raise
            if time.monotonic() >= deadline:
                os.close(fd)
                raise TimeoutError("task-heartbeat snapshot lock timed out")
            time.sleep(0.01)


def _release_write_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(fd)


def _truncate_utf8(value: Any, max_bytes: int) -> str:
    raw = str(value or "")
    encoded = raw.encode("utf-8")
    if len(encoded) <= max_bytes:
        return raw
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _bounded_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project one heartbeat to a known, individually bounded wire shape."""
    string_limits = {
        "schema_version": 32,
        "kind": 64,
        "id": 512,
        "session_id": 512,
        "tool": 512,
        "model": 512,
        "run_id": 512,
        "app_slug": 512,
        "task_ref": 2048,
        "status": 64,
        "progress_since_last": 2048,
        "attention_reason": 2048,
    }
    out: dict[str, Any] = {}
    for key, limit in string_limits.items():
        if key in record:
            out[key] = _truncate_utf8(record.get(key), limit)
    out["still_on_task"] = bool(record.get("still_on_task"))
    for key in ("interval_seconds", "next_check_in_at", "ts"):
        if key in record:
            out[key] = record.get(key)
    evidence = record.get("evidence_refs")
    out["evidence_refs"] = [
        _truncate_utf8(item, 256)
        for item in (evidence if isinstance(evidence, list) else [])[:16]
    ]
    encoded = json.dumps(out, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RECORD_BYTES:
        out["progress_since_last"] = _truncate_utf8(
            out.get("progress_since_last"), 512
        )
        out["attention_reason"] = _truncate_utf8(
            out.get("attention_reason"), 512
        )
        out["evidence_refs"] = []
        encoded = json.dumps(out, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RECORD_BYTES:
        raise ValueError("task-heartbeat record exceeds byte ceiling")
    return out


def _record_timestamp(record: dict[str, Any]) -> float:
    try:
        value = float(record.get("ts") or 0.0)
        return value if math.isfinite(value) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _record_key(record: dict[str, Any]) -> tuple[str, str]:
    return (str(record.get("session_id") or "unknown"), str(record.get("task_ref") or ""))


def _is_active_record(record: dict[str, Any]) -> bool:
    return str(record.get("status") or "running") != "done_pending_release"


def _read_snapshot(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """Read a bounded tail and report whether older key coverage was lost."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return ([], True) if os.path.lexists(path) else ([], False)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            return [], True
        start = max(0, metadata.st_size - MAX_SNAPSHOT_BYTES)
        data = os.pread(fd, metadata.st_size - start, start)
    except OSError:
        return [], True
    finally:
        os.close(fd)

    coverage_incomplete = start > 0
    if start:
        newline = data.find(b"\n")
        if newline < 0:
            return [], True
        data = data[newline + 1 :]
    records_reversed: list[dict[str, Any]] = []
    for raw_line in reversed(data.splitlines()):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except (UnicodeError, ValueError, TypeError):
            coverage_incomplete = True
            continue
        if not isinstance(record, dict):
            coverage_incomplete = True
            continue
        if record.get("kind") == _RETENTION_KIND:
            coverage_incomplete = coverage_incomplete or bool(record.get("truncated"))
            continue
        if record.get("kind") != "task-heartbeat":
            coverage_incomplete = True
            continue
        if len(records_reversed) >= MAX_SNAPSHOT_RECORDS:
            coverage_incomplete = True
            break
        records_reversed.append(record)
    records_reversed.reverse()
    return records_reversed, coverage_incomplete


def _snapshot_bytes(
    existing: list[dict[str, Any]],
    incoming: dict[str, Any],
    *,
    coverage_incomplete: bool,
) -> bytes:
    latest: dict[tuple[str, str], tuple[dict[str, Any], int]] = {}
    for ordinal, candidate in enumerate([*existing, incoming]):
        bounded = _bounded_record(candidate)
        key = _record_key(bounded)
        prior = latest.get(key)
        if prior is None or _record_timestamp(bounded) >= _record_timestamp(prior[0]):
            latest[key] = (bounded, ordinal)

    incoming_key = _record_key(_bounded_record(incoming))
    ranked = sorted(
        latest.values(),
        key=lambda item: (
            item[0] is latest.get(incoming_key, (None, 0))[0],
            _is_active_record(item[0]),
            _record_timestamp(item[0]),
            item[1],
        ),
        reverse=True,
    )
    kept = ranked[:MAX_SNAPSHOT_RECORDS]
    truncated = coverage_incomplete or len(ranked) > len(kept)

    def encode(records: list[tuple[dict[str, Any], int]], truncated_flag: bool) -> bytes:
        ordered = sorted(records, key=lambda item: (_record_timestamp(item[0]), item[1]))
        metadata = {
            "schema_version": "1.0",
            "kind": _RETENTION_KIND,
            "truncated": truncated_flag,
            "retained_keys": len(ordered),
            "updated_at": time.time(),
        }
        rows = [json.dumps(item[0], separators=(",", ":")) for item in ordered]
        rows.append(json.dumps(metadata, separators=(",", ":")))
        return ("\n".join(rows) + "\n").encode("utf-8")

    payload = encode(kept, truncated)
    while len(payload) > MAX_SNAPSHOT_BYTES and len(kept) > 1:
        kept.pop()
        truncated = True
        payload = encode(kept, truncated)
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise ValueError("task-heartbeat snapshot exceeds byte ceiling")
    return payload


def _atomic_write(path: Path, payload: bytes) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            offset = 0
            while offset < len(payload):
                written = os.write(fd, payload[offset:])
                if written <= 0:
                    raise OSError("zero-byte task-heartbeat snapshot write")
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _line_append(path: Path, record: dict[str, Any]) -> None:
    """Merge a heartbeat into the bounded latest-key snapshot under one lock."""
    path = Path(path)
    if ".rally" in path.expanduser().resolve(strict=False).parts:
        raise ValueError("Build Loop task-heartbeat sidecars are forbidden inside .rally")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = _acquire_write_lock(path)
    try:
        existing, coverage_incomplete = _read_snapshot(path)
        _atomic_write(
            path,
            _snapshot_bytes(
                existing,
                record,
                coverage_incomplete=coverage_incomplete,
            ),
        )
    finally:
        _release_write_lock(lock_fd)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def make_record(
    *,
    session_id: str,
    tool: str,
    model: str = "unknown",
    run_id: str = "unknown",
    app_slug: str = "unknown",
    task_ref: str,
    status: str = "running",
    still_on_task: bool = True,
    progress_since_last: str = "",
    evidence_refs: list[str] | None = None,
    attention_reason: str = "",
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    next_check_in_at: float | None = None,
    ts: float | None = None,
) -> dict[str, Any]:
    now = time.time() if ts is None else float(ts)
    interval = max(1, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
    normalized_status = status if status in STATUSES else "running"
    next_at = (
        float(next_check_in_at)
        if next_check_in_at is not None
        else now + interval
    )
    return {
        "schema_version": "1.0",
        "kind": "task-heartbeat",
        "id": f"{_safe_name(session_id)}-{int(now * 1000)}",
        "session_id": session_id or "unknown",
        "tool": tool or "unknown",
        "model": model or "unknown",
        "run_id": run_id or "unknown",
        "app_slug": app_slug or "unknown",
        "task_ref": task_ref,
        "status": normalized_status,
        "still_on_task": bool(still_on_task),
        "progress_since_last": (progress_since_last or "").strip(),
        "evidence_refs": evidence_refs or [],
        "attention_reason": (attention_reason or "").strip(),
        "interval_seconds": interval,
        "next_check_in_at": next_at,
        "ts": now,
    }


def write_heartbeat(
    channel_dir: Path,
    *,
    session_id: str,
    tool: str,
    model: str = "unknown",
    run_id: str = "unknown",
    app_slug: str = "unknown",
    task_ref: str,
    status: str = "running",
    still_on_task: bool = True,
    progress_since_last: str = "",
    evidence_refs: list[str] | str | None = None,
    attention_reason: str = "",
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    next_check_in_at: float | None = None,
    ts: float | None = None,
) -> dict[str, Any]:
    refs = (
        _split_csv(evidence_refs)
        if isinstance(evidence_refs, str)
        else list(evidence_refs or [])
    )
    record = make_record(
        session_id=session_id,
        tool=tool,
        model=model,
        run_id=run_id,
        app_slug=app_slug,
        task_ref=task_ref,
        status=status,
        still_on_task=still_on_task,
        progress_since_last=progress_since_last,
        evidence_refs=refs,
        attention_reason=attention_reason,
        interval_seconds=interval_seconds,
        next_check_in_at=next_check_in_at,
        ts=ts,
    )
    _line_append(heartbeat_path(Path(channel_dir), tool), record)
    return record


def read_heartbeats(
    channel_dir: Path,
    *,
    tool: str,
    session_id: str | None = None,
    task_ref: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    path = heartbeat_path(Path(channel_dir), tool)
    snapshot, _coverage_incomplete = _read_snapshot(path)
    records: list[dict[str, Any]] = []
    for rec in snapshot:
        if session_id and rec.get("session_id") != session_id:
            continue
        if task_ref and rec.get("task_ref") != task_ref:
            continue
        records.append(rec)
    records.sort(key=_record_timestamp)
    if limit is not None and limit >= 0:
        if limit == 0:
            return []
        return records[-limit:]
    return records


def _compact_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    keys = (
        "id",
        "session_id",
        "tool",
        "task_ref",
        "status",
        "still_on_task",
        "progress_since_last",
        "evidence_refs",
        "attention_reason",
        "interval_seconds",
        "next_check_in_at",
        "ts",
    )
    out = {k: record.get(k) for k in keys if k in record}
    progress = out.get("progress_since_last")
    if isinstance(progress, str) and len(progress) > 240:
        out["progress_since_last"] = progress[:239].rstrip() + "..."
    reason = out.get("attention_reason")
    if isinstance(reason, str) and len(reason) > 240:
        out["attention_reason"] = reason[:239].rstrip() + "..."
    return out


def summarize_task_health(
    channel_dir: Path,
    *,
    tool: str,
    session_id: str,
    expected_ref: str | None = None,
    now: float | None = None,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
) -> dict[str, Any]:
    records, coverage_incomplete = _read_snapshot(
        heartbeat_path(Path(channel_dir), tool)
    )
    return summarize_task_health_records(
        records,
        tool=tool,
        session_id=session_id,
        expected_ref=expected_ref,
        now=now,
        grace_seconds=grace_seconds,
        coverage_incomplete=coverage_incomplete,
    )


def summarize_task_health_records(
    records: list[dict[str, Any]],
    *,
    tool: str,
    session_id: str,
    expected_ref: str | None = None,
    now: float | None = None,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
    coverage_incomplete: bool = False,
) -> dict[str, Any]:
    """Summarize heartbeat health from an already-authoritative record stream.

    Native Rally carries Build Loop heartbeat records inside ordinary facts.
    This pure entrypoint lets native consumers reuse the exact local health
    projection without opening the embedded ``task-heartbeats`` sidecar.
    """
    current_time = time.time() if now is None else float(now)
    grace = max(0, int(grace_seconds))
    def timestamp(item: dict[str, Any]) -> float:
        try:
            return float(item.get("ts") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    records = sorted(
        (
            rec
            for rec in records
            if isinstance(rec, dict)
            and rec.get("tool") == tool
            and rec.get("session_id") == session_id
        ),
        key=timestamp,
    )
    latest_any = records[-1] if records else None
    expected_records = [
        rec for rec in records if expected_ref and rec.get("task_ref") == expected_ref
    ]
    latest_expected = expected_records[-1] if expected_records else None
    selected = latest_expected if expected_ref else latest_any
    coverage = {
        "mode": "bounded_latest_session_task",
        "max_records": MAX_SNAPSHOT_RECORDS,
        "max_bytes": MAX_SNAPSHOT_BYTES,
    }

    coverage["truncated"] = bool(coverage_incomplete)

    def result_with_coverage(
        result: dict[str, Any], *, query_incomplete: bool = False
    ) -> dict[str, Any]:
        return {
            **result,
            "coverage_incomplete": bool(query_incomplete),
            "coverage": coverage,
        }

    if expected_ref and latest_any and latest_any.get("task_ref") != expected_ref:
        return result_with_coverage({
            "expected_ref": expected_ref,
            "health": "wrong_task",
            "missed_count": 1,
            "latest": _compact_record(latest_any),
            "latest_for_expected": _compact_record(latest_expected),
        })
    if expected_ref and not selected:
        return result_with_coverage({
            "expected_ref": expected_ref,
            "health": "unknown" if coverage_incomplete else "missing",
            "missed_count": 0 if coverage_incomplete else 1,
            "reason": (
                "local_heartbeat_retention_truncated"
                if coverage_incomplete
                else "no_heartbeat_for_expected_ref"
            ),
            "latest": _compact_record(latest_any),
            "latest_for_expected": None,
        }, query_incomplete=coverage_incomplete)
    if not selected:
        return result_with_coverage({
            "expected_ref": expected_ref,
            "health": "unknown" if coverage_incomplete else "none",
            "missed_count": 0,
            "reason": (
                "local_heartbeat_retention_truncated"
                if coverage_incomplete
                else "no_heartbeat"
            ),
            "latest": None,
        }, query_incomplete=coverage_incomplete)

    status = str(selected.get("status") or "running")
    still_on_task = bool(selected.get("still_on_task"))
    try:
        interval = max(
            1, int(selected.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS)
        )
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL_SECONDS
    try:
        next_at = float(selected.get("next_check_in_at"))
    except (TypeError, ValueError):
        next_at = _record_timestamp(selected) or current_time
        next_at += interval

    missed_count = 0
    health = "current"
    if status in {"blocked", "needs_attention"}:
        health = status
    elif not still_on_task:
        health = "drift_risk"
    elif current_time > next_at + grace:
        health = "stale_check_in"
        missed_count = max(1, int(math.ceil((current_time - next_at) / interval)))

    return result_with_coverage({
        "expected_ref": expected_ref,
        "health": health,
        "missed_count": missed_count,
        "latest": _compact_record(selected),
        "latest_for_expected": _compact_record(latest_expected) if expected_ref else None,
    })
