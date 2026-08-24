"""Regression tests for terminal execution tombstones on later Stop hooks."""
from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import resume_resolver  # noqa: E402
import stop_closeout  # noqa: E402
from state_finalize import annotate_if_incomplete  # noqa: E402


SESSION = "sess-tombstone"


def _write_state(workdir: Path, state: dict) -> Path:
    state_path = workdir / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state))
    return state_path


def _terminal_recorded_state() -> dict:
    return {
        "phase": "execute",
        "triggers": {"riskSurfaceChange": True},
        "execution": {
            "build_loop_id": "bl-tombstone-001",
            "current_session_id": SESSION,
            "started_by_session_id": SESSION,
            "last_heartbeat_at": "2026-06-13T00:00:00Z",
            "run_label": "tombstone#001",
        },
        "runs": [{
            "run_id": "bl-tombstone-001",
            "outcome": "pass",
            "date": "2026-06-13T00:00:00Z",
            "goal": "preserve terminal tombstone",
            "phases": {},
        }],
    }


def test_empty_execution_tombstone_is_a_byte_noop(tmp_path):
    state_path = _write_state(tmp_path, {"phase": "execute", "execution": {}})
    before = state_path.read_bytes()

    assert annotate_if_incomplete(tmp_path) is False
    assert state_path.read_bytes() == before


def test_later_stop_preserves_closeout_tombstone_and_resolves_fresh(tmp_path):
    state_path = _write_state(tmp_path, _terminal_recorded_state())

    assert stop_closeout.run_stop(tmp_path, SESSION) == {}
    assert json.loads(state_path.read_text())["execution"] == {}
    before = state_path.read_bytes()

    assert annotate_if_incomplete(tmp_path) is False
    assert state_path.read_bytes() == before
    resolved = resume_resolver.resolve(tmp_path, "")
    assert resolved["decision"] == "fresh"
    assert resolved["reason"] == "no incomplete run"
