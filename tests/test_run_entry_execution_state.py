"""Tests for write_run_entry.update_execution_state (M2).

Exercises every action, the schema-version stamp, atomic-write semantics,
and concurrent-call safety via the existing LockedFile primitive.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from write_run_entry import (  # noqa: E402
    EXECUTION_SCHEMA_VERSION,
    EXECUTION_VALID_ACTIONS,
    update_execution_state,
)


def _state(tmp_path):
    p = tmp_path / "state.json"
    return p


def _start(tmp_path, **overrides):
    args = dict(
        run_id="run_20260506T220000Z_a1b2c3d4",
        queued_chunks=["c1", "c2", "c3"],
        file_ownership={"c1": ["a.py"], "c2": ["b.py"], "c3": ["c.py"]},
    )
    args.update(overrides)
    return update_execution_state(_state(tmp_path), "start", **args)


def test_start_creates_execution_block(tmp_path):
    block = _start(tmp_path)
    assert block["schema_version"] == EXECUTION_SCHEMA_VERSION
    assert block["run_id"] == "run_20260506T220000Z_a1b2c3d4"
    assert block["phase"] == "execute"
    assert block["iterate_attempt"] == 0
    assert block["queued_chunks"] == ["c1", "c2", "c3"]
    assert block["in_flight_chunks"] == []
    assert block["completed_chunks"] == []
    assert block["file_ownership"] == {"c1": ["a.py"], "c2": ["b.py"], "c3": ["c.py"]}
    assert block["started_at"] is not None
    assert block["last_heartbeat_at"] is not None
    assert block["crashed_at"] is None


def test_dispatch_moves_queue_to_in_flight(tmp_path):
    _start(tmp_path)
    block = update_execution_state(_state(tmp_path), "dispatch_chunk", chunk_id="c1")
    assert block["queued_chunks"] == ["c2", "c3"]
    assert block["in_flight_chunks"] == ["c1"]


def test_return_moves_in_flight_to_completed(tmp_path):
    _start(tmp_path)
    update_execution_state(_state(tmp_path), "dispatch_chunk", chunk_id="c1")
    block = update_execution_state(_state(tmp_path), "return_chunk", chunk_id="c1", status="fixed")
    assert block["in_flight_chunks"] == []
    assert len(block["completed_chunks"]) == 1
    entry = block["completed_chunks"][0]
    assert entry["chunk_id"] == "c1"
    assert entry["status"] == "fixed"
    assert "completed_at" in entry


def test_return_with_invalid_status_raises(tmp_path):
    _start(tmp_path)
    update_execution_state(_state(tmp_path), "dispatch_chunk", chunk_id="c1")
    with pytest.raises(ValueError, match="status"):
        update_execution_state(_state(tmp_path), "return_chunk", chunk_id="c1", status="bogus")


def test_phase_transition(tmp_path):
    _start(tmp_path)
    block = update_execution_state(_state(tmp_path), "phase_transition", phase="review")
    assert block["phase"] == "review"
    block = update_execution_state(_state(tmp_path), "phase_transition", phase="iterate")
    assert block["phase"] == "iterate"


def test_phase_transition_invalid_phase_raises(tmp_path):
    _start(tmp_path)
    with pytest.raises(ValueError, match="phase"):
        update_execution_state(_state(tmp_path), "phase_transition", phase="bogus")


def test_iterate_attempt_increments(tmp_path):
    _start(tmp_path)
    block = update_execution_state(_state(tmp_path), "iterate_attempt")
    assert block["iterate_attempt"] == 1
    block = update_execution_state(_state(tmp_path), "iterate_attempt")
    assert block["iterate_attempt"] == 2


def test_item_iteration_records_attempts_and_stop_reason(tmp_path):
    _start(tmp_path)
    block = update_execution_state(
        _state(tmp_path),
        "item_iteration",
        item_id="issue-1",
        status="failed",
        phase="iterate",
        criterion="tests",
        stop_reason="validator-failed",
        validator="pytest",
        model="code-tier",
        now=datetime(2026, 5, 6, 10, 0, 5, tzinfo=timezone.utc),
    )
    block = update_execution_state(
        _state(tmp_path),
        "item_iteration",
        item_id="issue-1",
        status="passed",
        phase="review",
        criterion="tests",
        validator="pytest",
        now=datetime(2026, 5, 6, 10, 1, 5, tzinfo=timezone.utc),
    )

    attempts = block["item_iterations"]["issue-1"]
    assert block["current_item_id"] == "issue-1"
    assert attempts[0] == {
        "attempt": 1,
        "status": "failed",
        "phase": "iterate",
        "recorded_at": "2026-05-06T10:00:05Z",
        "criterion": "tests",
        "stop_reason": "validator-failed",
        "validator": "pytest",
        "model": "code-tier",
    }
    assert attempts[1]["attempt"] == 2
    assert attempts[1]["status"] == "passed"
    assert attempts[1]["phase"] == "review"
    assert attempts[1]["criterion"] == "tests"
    assert "stop_reason" not in attempts[1]


def test_item_iteration_validates_status(tmp_path):
    _start(tmp_path)
    with pytest.raises(ValueError, match="status"):
        update_execution_state(_state(tmp_path), "item_iteration", item_id="issue-1", status="bogus")


def test_complete_sets_phase_report(tmp_path):
    _start(tmp_path)
    block = update_execution_state(_state(tmp_path), "complete")
    assert block["phase"] == "report"


def test_start_requires_run_id(tmp_path):
    with pytest.raises(ValueError, match="run_id"):
        update_execution_state(_state(tmp_path), "start", queued_chunks=[], file_ownership={})


def test_start_requires_queued_chunks(tmp_path):
    with pytest.raises(ValueError, match="queued_chunks"):
        update_execution_state(_state(tmp_path), "start", run_id="r1", file_ownership={})


def test_dispatch_without_start_raises(tmp_path):
    with pytest.raises(ValueError, match="existing execution block"):
        update_execution_state(_state(tmp_path), "dispatch_chunk", chunk_id="c1")


def test_invalid_action_raises(tmp_path):
    with pytest.raises(ValueError, match="action"):
        update_execution_state(_state(tmp_path), "bogus_action")


def test_review_e_pass_appends_rows_without_execution_block(tmp_path):
    """review_e_pass writes state['reviewE'] and does NOT require 'start' first."""
    sp = _state(tmp_path)
    sp.write_text("{}")
    update_execution_state(sp, "review_e_pass", files_scanned=["src/a.ts", "src/b.ts"], is_final=False)
    update_execution_state(sp, "review_e_pass", files_scanned=["src/a.ts"], is_final=True)
    st = json.loads(sp.read_text())
    assert st["reviewE"] == [
        {"pass_idx": 0, "files_scanned": ["src/a.ts", "src/b.ts"], "is_final": False},
        {"pass_idx": 1, "files_scanned": ["src/a.ts"], "is_final": True},
    ]
    assert "execution" not in st  # telemetry is independent of the heartbeat block


def test_review_e_pass_validates_args(tmp_path):
    sp = _state(tmp_path)
    sp.write_text("{}")
    with pytest.raises(ValueError, match="files_scanned"):
        update_execution_state(sp, "review_e_pass", is_final=False)
    with pytest.raises(ValueError, match="is_final"):
        update_execution_state(sp, "review_e_pass", files_scanned=["x"])


def test_action_set_complete():
    """Sanity: every action documented in the plan is in the enum."""
    expected = {"start", "dispatch_chunk", "return_chunk", "phase_transition",
                "iterate_attempt", "item_iteration", "review_e_pass", "complete", "heartbeat"}
    assert EXECUTION_VALID_ACTIONS == expected


def test_atomic_write_preserves_existing_top_level_keys(tmp_path):
    state_path = _state(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"runs": [{"run_id": "old"}], "preBuildSha": "abc"}))
    _start(tmp_path)
    persisted = json.loads(state_path.read_text())
    assert persisted["runs"] == [{"run_id": "old"}]
    assert persisted["preBuildSha"] == "abc"
    assert "execution" in persisted


def test_concurrent_calls_serialize_via_lock(tmp_path):
    """Four threads each dispatching a different chunk should all land without clobbering."""
    _start(tmp_path, queued_chunks=["c1", "c2", "c3", "c4"], file_ownership={f"c{i}": [f"f{i}.py"] for i in range(1, 5)})
    state_path = _state(tmp_path)
    errors = []

    def worker(cid):
        try:
            update_execution_state(state_path, "dispatch_chunk", chunk_id=cid)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(f"c{i}",)) for i in range(1, 5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    block = json.loads(state_path.read_text())["execution"]
    assert sorted(block["in_flight_chunks"]) == ["c1", "c2", "c3", "c4"]
    assert block["queued_chunks"] == []


def test_heartbeat_refreshed_on_every_call(tmp_path):
    _start(tmp_path, now=datetime(2026, 5, 6, 10, 0, 0, tzinfo=timezone.utc))
    block_after = update_execution_state(
        _state(tmp_path),
        "dispatch_chunk",
        chunk_id="c1",
        now=datetime(2026, 5, 6, 10, 0, 5, tzinfo=timezone.utc),
    )
    assert block_after["last_heartbeat_at"] == "2026-05-06T10:00:05Z"
    # started_at should NOT change
    assert block_after["started_at"] == "2026-05-06T10:00:00Z"
