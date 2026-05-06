"""Tests for scripts/state_finalize.py (M4 Stop hook annotation)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from state_finalize import annotate_if_incomplete, main  # noqa: E402
from write_run_entry import update_execution_state  # noqa: E402


def test_no_state_json_returns_false(tmp_path):
    assert annotate_if_incomplete(tmp_path) is False


def test_state_without_execution_block_no_annotation(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"runs": []}))
    assert annotate_if_incomplete(tmp_path) is False
    # State file untouched
    assert json.loads(state_path.read_text()) == {"runs": []}


def test_phase_report_no_annotation(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    update_execution_state(state_path, "start",
        run_id="r1", queued_chunks=["c1"], file_ownership={"c1": ["a"]})
    update_execution_state(state_path, "complete")
    assert annotate_if_incomplete(tmp_path) is False
    # crashed_at remains None
    state = json.loads(state_path.read_text())
    assert state["execution"].get("crashed_at") is None


def test_incomplete_phase_writes_annotation(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    update_execution_state(state_path, "start",
        run_id="r1", queued_chunks=["c1"], file_ownership={"c1": ["a"]})
    assert annotate_if_incomplete(tmp_path) is True
    state = json.loads(state_path.read_text())
    assert state["execution"]["crashed_at"] is not None
    assert state["execution"]["crash_signal"] == "stop_hook"


def test_custom_signal_value(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    update_execution_state(state_path, "start",
        run_id="r1", queued_chunks=["c1"], file_ownership={"c1": ["a"]})
    assert annotate_if_incomplete(tmp_path, signal="subagent_stop") is True
    state = json.loads(state_path.read_text())
    assert state["execution"]["crash_signal"] == "subagent_stop"


def test_corrupt_state_json_swallows_error(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{not valid json")
    # Returns False, never raises
    assert annotate_if_incomplete(tmp_path) is False


def test_main_always_exits_zero_on_corrupt_state(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{not valid json")
    rc = main(["--workdir", str(tmp_path), "--mark-incomplete-as-crashed"])
    assert rc == 0


def test_main_always_exits_zero_when_no_state(tmp_path):
    rc = main(["--workdir", str(tmp_path), "--mark-incomplete-as-crashed"])
    assert rc == 0


def test_main_writes_annotation_via_cli(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    update_execution_state(state_path, "start",
        run_id="r1", queued_chunks=["c1"], file_ownership={"c1": ["a"]})
    rc = main(["--workdir", str(tmp_path), "--mark-incomplete-as-crashed"])
    assert rc == 0
    state = json.loads(state_path.read_text())
    assert state["execution"]["crashed_at"] is not None
