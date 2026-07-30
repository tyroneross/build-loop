#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Task heartbeat records for long-running Rally work.

Presence answers "can this session still write?". Task heartbeat answers
"is this session still working on the claimed task, and does it need attention?".
The stream is append-only and low-noise; status/watch read only the latest
compact projection.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

_HEARTBEAT_DIR = "task-heartbeats"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
DEFAULT_INTERVAL_SECONDS = 600
DEFAULT_GRACE_SECONDS = 60
STATUSES = {
    "running",
    "blocked",
    "waiting",
    "reviewing",
    "needs_attention",
    "done_pending_release",
}


def _safe_name(value: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", (value or "unknown").strip())
    cleaned = cleaned.strip("._")
    return cleaned or "unknown"


def heartbeat_path(channel_dir: Path, tool: str) -> Path:
    return Path(channel_dir) / _HEARTBEAT_DIR / f"{_safe_name(tool)}.jsonl"


def _line_append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":")) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


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
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if session_id and rec.get("session_id") != session_id:
            continue
        if task_ref and rec.get("task_ref") != task_ref:
            continue
        records.append(rec)
    records.sort(key=lambda item: float(item.get("ts") or 0.0))
    if limit is not None and limit >= 0:
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
    current_time = time.time() if now is None else float(now)
    grace = max(0, int(grace_seconds))
    records = read_heartbeats(channel_dir, tool=tool, session_id=session_id)
    latest_any = records[-1] if records else None
    expected_records = [
        rec for rec in records if expected_ref and rec.get("task_ref") == expected_ref
    ]
    latest_expected = expected_records[-1] if expected_records else None
    selected = latest_expected if expected_ref else latest_any

    if expected_ref and latest_any and latest_any.get("task_ref") != expected_ref:
        return {
            "expected_ref": expected_ref,
            "health": "wrong_task",
            "missed_count": 1,
            "latest": _compact_record(latest_any),
            "latest_for_expected": _compact_record(latest_expected),
        }
    if expected_ref and not selected:
        return {
            "expected_ref": expected_ref,
            "health": "missing",
            "missed_count": 1,
            "latest": _compact_record(latest_any),
            "latest_for_expected": None,
        }
    if not selected:
        return {
            "expected_ref": expected_ref,
            "health": "none",
            "missed_count": 0,
            "latest": None,
        }

    status = str(selected.get("status") or "running")
    still_on_task = bool(selected.get("still_on_task"))
    interval = max(1, int(selected.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS))
    try:
        next_at = float(selected.get("next_check_in_at"))
    except (TypeError, ValueError):
        next_at = float(selected.get("ts") or current_time) + interval

    missed_count = 0
    health = "current"
    if status in {"blocked", "needs_attention"}:
        health = status
    elif not still_on_task:
        health = "drift_risk"
    elif current_time > next_at + grace:
        health = "stale_check_in"
        missed_count = max(1, int(math.ceil((current_time - next_at) / interval)))

    return {
        "expected_ref": expected_ref,
        "health": health,
        "missed_count": missed_count,
        "latest": _compact_record(selected),
        "latest_for_expected": _compact_record(latest_expected) if expected_ref else None,
    }
