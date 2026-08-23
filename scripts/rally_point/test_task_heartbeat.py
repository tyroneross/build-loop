# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import task_heartbeat as heartbeat  # noqa: E402


def _write(
    channel: Path,
    *,
    session_id: str,
    task_ref: str,
    ts: float,
    status: str = "running",
    progress: str = "",
) -> dict:
    return heartbeat.write_heartbeat(
        channel,
        session_id=session_id,
        tool="codex",
        task_ref=task_ref,
        status=status,
        progress_since_last=progress,
        interval_seconds=10,
        ts=ts,
        next_check_in_at=ts + 10,
    )


def test_repeated_same_key_is_fixed_size_and_preserves_missed_count(tmp_path):
    channel = tmp_path / "channel"
    for index in range(500):
        _write(
            channel,
            session_id="session-a",
            task_ref="task-a",
            ts=float(index + 1),
            progress=f"step-{index}",
        )

    path = heartbeat.heartbeat_path(channel, "codex")
    records = heartbeat.read_heartbeats(
        channel, tool="codex", session_id="session-a"
    )
    assert path.stat().st_size <= heartbeat.MAX_SNAPSHOT_BYTES
    assert len(records) == 1
    assert records[0]["progress_since_last"] == "step-499"
    health = heartbeat.summarize_task_health(
        channel,
        tool="codex",
        session_id="session-a",
        expected_ref="task-a",
        now=540.0,
        grace_seconds=0,
    )
    assert health["health"] == "stale_check_in"
    assert health["missed_count"] == 3
    assert health["coverage_incomplete"] is False


def test_snapshot_preserves_latest_expected_and_newer_wrong_task(tmp_path):
    channel = tmp_path / "channel"
    _write(channel, session_id="session-a", task_ref="task-a", ts=10.0)
    _write(channel, session_id="session-a", task_ref="task-b", ts=20.0)
    _write(channel, session_id="session-a", task_ref="task-a", ts=15.0)

    health = heartbeat.summarize_task_health(
        channel,
        tool="codex",
        session_id="session-a",
        expected_ref="task-a",
        now=21.0,
    )
    assert health["health"] == "wrong_task"
    assert health["latest"]["task_ref"] == "task-b"
    assert health["latest_for_expected"]["ts"] == 15.0


def test_overflow_prefers_active_keys_and_reports_unknown_for_eviction(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(heartbeat, "MAX_SNAPSHOT_RECORDS", 3)
    channel = tmp_path / "channel"
    _write(channel, session_id="active-old", task_ref="a", ts=1.0)
    _write(
        channel,
        session_id="done-new",
        task_ref="done",
        ts=100.0,
        status="done_pending_release",
    )
    _write(channel, session_id="active-mid", task_ref="b", ts=2.0)
    _write(channel, session_id="active-new", task_ref="c", ts=3.0)

    retained = heartbeat.read_heartbeats(channel, tool="codex")
    assert {record["session_id"] for record in retained} == {
        "active-old",
        "active-mid",
        "active-new",
    }
    health = heartbeat.summarize_task_health(
        channel,
        tool="codex",
        session_id="done-new",
        expected_ref="done",
        now=101.0,
    )
    assert health["health"] == "unknown"
    assert health["missed_count"] == 0
    assert health["coverage_incomplete"] is True
    assert health["reason"] == "local_heartbeat_retention_truncated"


def test_legacy_oversize_reader_only_parses_bounded_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(heartbeat, "MAX_SNAPSHOT_BYTES", 1024)
    monkeypatch.setattr(heartbeat, "MAX_SNAPSHOT_RECORDS", 10)
    channel = tmp_path / "channel"
    path = heartbeat.heartbeat_path(channel, "codex")
    path.parent.mkdir(parents=True)
    rows = [
        heartbeat.make_record(
            session_id=f"session-{index}",
            tool="codex",
            task_ref=f"task-{index}",
            ts=float(index),
        )
        for index in range(500)
    ]
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )

    retained, coverage_incomplete = heartbeat._read_snapshot(path)
    assert len(retained) <= 10
    assert coverage_incomplete is True
    health = heartbeat.summarize_task_health(
        channel,
        tool="codex",
        session_id="not-retained",
        expected_ref="not-retained",
    )
    assert health["health"] == "unknown"


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl is POSIX-only")
def test_concurrent_writers_publish_one_valid_snapshot(tmp_path):
    channel = tmp_path / "channel"
    code = (
        "import pathlib,sys;"
        f"sys.path.insert(0,{str(_HERE)!r});"
        "import task_heartbeat as h;"
        "h.write_heartbeat(pathlib.Path(sys.argv[1]),session_id=sys.argv[2],"
        "tool='codex',task_ref=sys.argv[3],ts=float(sys.argv[4]))"
    )
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(channel),
                f"session-{index}",
                f"task-{index}",
                str(index + 1),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(16)
    ]
    results = [process.communicate(timeout=10) for process in processes]
    assert all(process.returncode == 0 for process in processes), results
    records = heartbeat.read_heartbeats(channel, tool="codex")
    assert len(records) == 16
    assert len({_record["session_id"] for _record in records}) == 16
    path = heartbeat.heartbeat_path(channel, "codex")
    assert path.stat().st_size <= heartbeat.MAX_SNAPSHOT_BYTES
    for line in path.read_text(encoding="utf-8").splitlines():
        assert isinstance(json.loads(line), dict)


def test_large_record_and_tool_path_are_bounded(tmp_path):
    channel = tmp_path / "channel"
    tool = "tool/" + ("x" * 500)
    heartbeat.write_heartbeat(
        channel,
        session_id="s" * 5_000,
        tool=tool,
        task_ref="t" * 20_000,
        progress_since_last="p" * 100_000,
        evidence_refs=["e" * 10_000 for _ in range(100)],
    )
    path = heartbeat.heartbeat_path(channel, tool)
    assert len(path.name) <= 102
    assert path.stat().st_size <= heartbeat.MAX_SNAPSHOT_BYTES
    record = heartbeat.read_heartbeats(channel, tool=tool)[0]
    assert len(json.dumps(record).encode("utf-8")) <= heartbeat.MAX_RECORD_BYTES


def test_nonnumeric_timestamp_is_safe_and_zero_limit_is_empty(tmp_path):
    channel = tmp_path / "channel"
    record = heartbeat.make_record(
        session_id="session-a",
        tool="codex",
        task_ref="task-a",
        ts=1.0,
    )
    record["ts"] = "not-a-number"
    heartbeat._line_append(heartbeat.heartbeat_path(channel, "codex"), record)

    records = heartbeat.read_heartbeats(channel, tool="codex")
    assert len(records) == 1
    assert records[0]["ts"] == "not-a-number"
    assert heartbeat.read_heartbeats(channel, tool="codex", limit=0) == []
