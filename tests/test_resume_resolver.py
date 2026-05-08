"""Tests for scripts/resume_resolver.py (M3).

Covers: schema-version mismatch, run_id mismatch, phase=report refusal,
latest resolution, concurrent-modification demotion, in-flight-no-return
demotion, no-state.json handling, heartbeat-staleness no-resume path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from resume_resolver import resolve  # noqa: E402
from write_run_entry import update_execution_state  # noqa: E402
from write_subagent_result import write_subagent_result  # noqa: E402


def _setup_started_run(tmp_path: Path, *, run_id="run_test_001", queued=("c1", "c2", "c3", "c4")) -> Path:
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    update_execution_state(
        state_path, "start",
        run_id=run_id,
        queued_chunks=list(queued),
        file_ownership={c: [f"{c}.py"] for c in queued},
        now=datetime(2026, 5, 6, 10, 0, 0, tzinfo=timezone.utc),
    )
    return state_path


def test_no_state_json_no_resume_returns_fresh(tmp_path):
    env = resolve(tmp_path, "")
    assert env["decision"] == "fresh"
    assert env["run_id"] is None


def test_no_state_json_with_resume_aborts(tmp_path):
    env = resolve(tmp_path, "run_doesnotexist")
    assert env["decision"] == "abort"


def test_no_resume_no_stale_heartbeat_returns_fresh(tmp_path):
    _setup_started_run(tmp_path)
    # Heartbeat is fresh-ish — call resolve with a "now" only 30s after start
    now = datetime(2026, 5, 6, 10, 0, 30, tzinfo=timezone.utc)
    env = resolve(tmp_path, "", now=now)
    assert env["decision"] == "fresh"


def test_no_resume_stale_heartbeat_prompts_user(tmp_path):
    _setup_started_run(tmp_path)
    # Now is 10 minutes after start — heartbeat is stale
    now = datetime(2026, 5, 6, 10, 10, 0, tzinfo=timezone.utc)
    env = resolve(tmp_path, "", now=now)
    assert env["decision"] == "prompt_user"
    assert env["run_id"] == "run_test_001"
    assert "incomplete build detected" in env["reason"]


def test_no_resume_phase_report_returns_fresh(tmp_path):
    _setup_started_run(tmp_path)
    update_execution_state(tmp_path / ".build-loop" / "state.json", "complete")
    # Even after "10 minutes" the phase=report sentinel says clean exit
    now = datetime(2026, 5, 6, 10, 10, 0, tzinfo=timezone.utc)
    env = resolve(tmp_path, "", now=now)
    assert env["decision"] == "fresh"


def test_resume_literal_match_succeeds(tmp_path):
    _setup_started_run(tmp_path, run_id="run_xyz")
    env = resolve(tmp_path, "run_xyz")
    assert env["decision"] == "resume"
    assert env["run_id"] == "run_xyz"
    # All 4 chunks queued and not yet dispatched → all remaining
    assert len(env["remaining_chunks"]) == 4
    assert {r["chunk_id"] for r in env["remaining_chunks"]} == {"c1", "c2", "c3", "c4"}


def test_resume_run_id_mismatch_aborts(tmp_path):
    _setup_started_run(tmp_path, run_id="run_abc")
    env = resolve(tmp_path, "run_def")
    assert env["decision"] == "abort"
    assert "does not match" in env["reason"]


def test_resume_phase_report_aborts(tmp_path):
    _setup_started_run(tmp_path, run_id="run_done")
    update_execution_state(tmp_path / ".build-loop" / "state.json", "complete")
    env = resolve(tmp_path, "run_done")
    assert env["decision"] == "abort"
    assert "already complete" in env["reason"]


def test_resume_schema_mismatch_aborts(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "execution": {
            "schema_version": 999,
            "run_id": "run_old",
            "phase": "execute",
            "iterate_attempt": 0,
            "in_flight_chunks": [],
            "completed_chunks": [],
            "queued_chunks": ["c1"],
            "file_ownership": {"c1": ["a.py"]},
            "started_at": "2026-05-06T10:00:00Z",
            "last_heartbeat_at": "2026-05-06T10:00:00Z",
            "crashed_at": None,
        }
    }))
    env = resolve(tmp_path, "run_old")
    assert env["decision"] == "abort"
    assert "incompatible schema_version" in env["reason"]


def test_resume_with_some_returned_chunks_excludes_them(tmp_path):
    state_path = _setup_started_run(tmp_path, run_id="run_partial")
    # Dispatch + return c1 with status=fixed
    update_execution_state(state_path, "dispatch_chunk", chunk_id="c1")
    update_execution_state(state_path, "return_chunk", chunk_id="c1", status="fixed")
    # Dispatch c2 but don't return (mid-execute crash)
    update_execution_state(state_path, "dispatch_chunk", chunk_id="c2")
    env = resolve(tmp_path, "run_partial")
    assert env["decision"] == "resume"
    remaining = {r["chunk_id"] for r in env["remaining_chunks"]}
    # c1 is done (status=fixed → excluded); c2 is in_flight (no envelope → demoted); c3, c4 still queued
    assert remaining == {"c2", "c3", "c4"}


def test_in_flight_with_failed_envelope_demotes(tmp_path):
    state_path = _setup_started_run(tmp_path, run_id="run_failed")
    update_execution_state(state_path, "dispatch_chunk", chunk_id="c1")
    # Subagent returned but with status=failed (M1 envelope present)
    write_subagent_result(tmp_path, "run_failed", {
        "chunk_id": "c1",
        "status": "failed",
        "files_changed": [],
        "verifications": [],
        "attempt": 1,
    })
    # State.json still shows c1 in in_flight (orchestrator crashed before update_execution_state for return_chunk)
    env = resolve(tmp_path, "run_failed")
    remaining = [r for r in env["remaining_chunks"] if r["chunk_id"] == "c1"]
    assert len(remaining) == 1
    assert remaining[0]["prior_status"] == "failed"


def test_resume_latest_resolves_to_actual_run_id(tmp_path):
    _setup_started_run(tmp_path, run_id="run_latest_test")
    now = datetime(2026, 5, 6, 10, 30, 0, tzinfo=timezone.utc)  # 30 min after start
    env = resolve(tmp_path, "latest", now=now)
    assert env["decision"] == "resume"
    assert env["run_id"] == "run_latest_test"


def test_resume_latest_when_no_stale_run_aborts(tmp_path):
    _setup_started_run(tmp_path)
    now = datetime(2026, 5, 6, 10, 0, 30, tzinfo=timezone.utc)  # 30s after start, fresh heartbeat
    env = resolve(tmp_path, "latest", now=now)
    assert env["decision"] == "abort"


def _make_git_repo(tmp_path: Path) -> None:
    """Initialize a git repo so concurrent-modification check has something to query."""
    subprocess.check_call(["git", "init", "-q"], cwd=tmp_path)
    subprocess.check_call(["git", "config", "user.email", "test@test"], cwd=tmp_path)
    subprocess.check_call(["git", "config", "user.name", "test"], cwd=tmp_path)


def test_concurrent_modification_demotes_completed_chunk(tmp_path):
    _make_git_repo(tmp_path)
    state_path = _setup_started_run(tmp_path, run_id="run_cm")
    # Create the file owned by c1, commit it, then complete c1
    (tmp_path / "c1.py").write_text("# v1\n")
    subprocess.check_call(["git", "add", "c1.py"], cwd=tmp_path)
    subprocess.check_call(["git", "commit", "-qm", "v1"], cwd=tmp_path)
    update_execution_state(state_path, "dispatch_chunk", chunk_id="c1")
    update_execution_state(state_path, "return_chunk", chunk_id="c1", status="fixed")

    # Hand-modify c1.py AFTER the chunk completed
    time.sleep(0.05)
    (tmp_path / "c1.py").write_text("# v2 hand-edited\n")

    env = resolve(tmp_path, "run_cm")
    assert env["decision"] == "resume"
    flagged = [m for m in env["concurrent_modifications"] if m["chunk_id"] == "c1"]
    assert len(flagged) == 1
    assert "c1.py" in flagged[0]["files"]
    # Demoted into remaining_chunks with concurrent_modification_detected status
    remaining_c1 = [r for r in env["remaining_chunks"] if r["chunk_id"] == "c1"]
    assert remaining_c1
    assert remaining_c1[0]["prior_status"] == "concurrent_modification_detected"


def test_concurrent_modification_skipped_when_file_clean(tmp_path):
    _make_git_repo(tmp_path)
    state_path = _setup_started_run(tmp_path, run_id="run_clean")
    (tmp_path / "c1.py").write_text("# v1\n")
    subprocess.check_call(["git", "add", "c1.py"], cwd=tmp_path)
    subprocess.check_call(["git", "commit", "-qm", "v1"], cwd=tmp_path)
    update_execution_state(state_path, "dispatch_chunk", chunk_id="c1")
    update_execution_state(state_path, "return_chunk", chunk_id="c1", status="fixed")
    # Don't modify after — file is clean
    env = resolve(tmp_path, "run_clean")
    assert env["concurrent_modifications"] == []


def test_iterate_attempt_preserved_across_resume(tmp_path):
    state_path = _setup_started_run(tmp_path, run_id="run_iter")
    update_execution_state(state_path, "iterate_attempt")
    update_execution_state(state_path, "iterate_attempt")
    env = resolve(tmp_path, "run_iter")
    assert env["iterate_attempt"] == 2
