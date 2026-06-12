# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for stop_closeout.py — structural Stop-hook closeout for inline runs (f6)."""
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stop_closeout  # noqa: E402

SCRIPT = Path(__file__).parent / "stop_closeout.py"
SESSION = "sess-abc"


def _now():
    return stop_closeout._utc_now()


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_state(tmp: Path, state: dict) -> Path:
    bl = tmp / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    (bl / "state.json").write_text(json.dumps(state))
    return bl / "state.json"


def _base_state(session=SESSION, run_id="bl-test-001", phase="done", stakes_trigger=True, runs=None):
    return {
        "phase": phase,
        "triggers": {"riskSurfaceChange": True} if stakes_trigger else {"riskSurfaceChange": False},
        "execution": {
            "build_loop_id": run_id,
            "current_session_id": session,
            "started_by_session_id": session,
            "last_heartbeat_at": _iso(_now()),
            "run_label": "test#001",
        },
        "runs": runs if runs is not None else [],
    }


def _runs(tmp: Path) -> list:
    return json.loads((tmp / ".build-loop" / "state.json").read_text()).get("runs", [])


# --- record path -----------------------------------------------------------

def test_records_inline_run_and_warns_when_stakes_gated(tmp_path):
    _write_state(tmp_path, _base_state())
    out = stop_closeout.run_stop(tmp_path, SESSION)

    runs = _runs(tmp_path)
    assert len(runs) == 1
    rec = runs[0]
    assert rec["run_id"] == "bl-test-001"
    assert rec["source"] == "append_run"
    assert rec["auditor_status"] == "not-run:parent-must-dispatch"  # honest floor
    assert rec["triggers"]["riskSurfaceChange"] is True  # stakes signal propagated
    # stakes-gated + judgment skipped + no Agent tool → WARN surfaced advisory.
    assert "systemMessage" in out
    assert "WARN" in out["systemMessage"]
    # marker written.
    assert (tmp_path / ".build-loop" / "closeout-pending" / "bl-test-001.md").exists()


def test_records_quietly_when_no_stakes_trigger(tmp_path):
    _write_state(tmp_path, _base_state(stakes_trigger=False))
    out = stop_closeout.run_stop(tmp_path, SESSION)
    assert len(_runs(tmp_path)) == 1            # still recorded (Learn visibility)
    assert out == {}                            # no judgment gap → no advisory


def test_outcome_partial_when_not_done(tmp_path):
    _write_state(tmp_path, _base_state(phase="execute"))
    stop_closeout.run_stop(tmp_path, SESSION)
    assert _runs(tmp_path)[0]["outcome"] == "partial"


# --- idempotency -----------------------------------------------------------

def test_second_stop_same_run_is_noop(tmp_path):
    _write_state(tmp_path, _base_state())
    stop_closeout.run_stop(tmp_path, SESSION)
    first = _runs(tmp_path)
    out2 = stop_closeout.run_stop(tmp_path, SESSION)   # marker now present
    assert out2 == {}
    assert _runs(tmp_path) == first                    # no double-record


def test_does_not_clobber_richer_orchestrator_record(tmp_path):
    rich = {"run_id": "bl-test-001", "date": "2026-06-12T00:00:00Z", "goal": "g",
            "outcome": "pass", "source": "review-g", "phases": {}}
    _write_state(tmp_path, _base_state(runs=[rich]))
    out = stop_closeout.run_stop(tmp_path, SESSION)
    runs = _runs(tmp_path)
    assert len(runs) == 1 and runs[0]["source"] == "review-g"   # untouched
    assert out == {}                                            # idempotent with Review-G


# --- self-gating -----------------------------------------------------------

def test_no_state_json_is_silent(tmp_path):
    (tmp_path / ".build-loop").mkdir()
    assert stop_closeout.run_stop(tmp_path, SESSION) == {}


def test_no_build_loop_id_is_silent(tmp_path):
    st = _base_state()
    st["execution"].pop("build_loop_id")
    _write_state(tmp_path, st)
    assert stop_closeout.run_stop(tmp_path, SESSION) == {}
    assert _runs(tmp_path) == []


def test_session_mismatch_is_silent(tmp_path):
    _write_state(tmp_path, _base_state(session="other-session"))
    assert stop_closeout.run_stop(tmp_path, SESSION) == {}
    assert _runs(tmp_path) == []


def test_no_session_id_fresh_heartbeat_records(tmp_path):
    st = _base_state()
    st["execution"]["current_session_id"] = ""
    st["execution"]["started_by_session_id"] = ""
    _write_state(tmp_path, st)
    stop_closeout.run_stop(tmp_path, "")          # no session id from host
    assert len(_runs(tmp_path)) == 1               # heartbeat fresh → recorded


def test_no_session_id_stale_heartbeat_is_silent(tmp_path):
    st = _base_state()
    st["execution"]["current_session_id"] = ""
    st["execution"]["started_by_session_id"] = ""
    st["execution"]["last_heartbeat_at"] = _iso(_now() - timedelta(minutes=stop_closeout._HEARTBEAT_FRESH_MINUTES + 30))
    _write_state(tmp_path, st)
    assert stop_closeout.run_stop(tmp_path, "") == {}
    assert _runs(tmp_path) == []


# --- session-start surfacing ----------------------------------------------

def test_session_start_surfaces_and_archives_marker(tmp_path):
    _write_state(tmp_path, _base_state())
    stop_closeout.run_stop(tmp_path, SESSION)          # writes the marker
    out = stop_closeout.run_session_start(tmp_path)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "bl-test-001" in out["hookSpecificOutput"]["additionalContext"]
    # surfaced once: moved out of the live dir.
    assert not (tmp_path / ".build-loop" / "closeout-pending" / "bl-test-001.md").exists()
    assert (tmp_path / ".build-loop" / "closeout-pending" / "surfaced" / "bl-test-001.md").exists()
    # second surface → nothing left.
    assert stop_closeout.run_session_start(tmp_path) == {}


def test_session_start_no_markers_is_silent(tmp_path):
    assert stop_closeout.run_session_start(tmp_path) == {}


# --- CLI smoke (exit 0 + valid JSON always) --------------------------------

def test_cli_stop_emits_valid_json_exit0(tmp_path):
    _write_state(tmp_path, _base_state())
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(tmp_path), "--mode", "stop",
         "--session-id", SESSION, "--hook"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0
    json.loads(res.stdout)            # parses


def test_cli_reads_session_id_from_stdin(tmp_path):
    _write_state(tmp_path, _base_state())
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(tmp_path), "--mode", "stop", "--hook"],
        input=json.dumps({"session_id": SESSION, "hook_event_name": "Stop"}),
        capture_output=True, text=True,
    )
    assert res.returncode == 0
    assert len(_runs(tmp_path)) == 1   # session id taken from stdin → recorded
