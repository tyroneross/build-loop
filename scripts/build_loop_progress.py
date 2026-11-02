"""Pytest lifecycle adapter; Rust owns process status and raw output logs."""
import json
import os
from pathlib import Path
import time
import pytest

_directory = os.environ.get("BUILD_LOOP_PROGRESS_DIR")
_completed = 0
_state = {"schema": "build-loop.pytest.v1", "active_test": None, "phase": "starting", "collected": 0, "completed": 0}


def _record(event, **values):
    if not _directory:
        return
    _state.update(values, updated_at_ms=time.time_ns() // 1_000_000)
    root = Path(_directory)
    payload = json.dumps(_state, ensure_ascii=True)
    temp = root / ("pytest-status-" + str(os.getpid()) + ".tmp")
    temp.write_text(payload, encoding="utf-8")
    temp.replace(root / "pytest-status.json")
    with (root / "pytest-events.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps({"event": event, **_state}) + "\n")
        output.flush()


def pytest_sessionstart():
    global _completed
    _completed = 0
    _record("session_start", phase="collecting", active_test=None, collected=0, completed=0, outcome=None, exit_code=None)


def pytest_collection_finish(session):
    _record("collection_finish", phase="collected", collected=len(session.items))


def pytest_runtest_logstart(nodeid):
    # Fires before fixture setup, so a stalled setup has an attributable test.
    _record("test_start", active_test=nodeid, phase="setup", outcome=None)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    _record("phase_start", active_test=item.nodeid, phase="setup", outcome=None)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    _record("phase_start", active_test=item.nodeid, phase="call", outcome=None)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item):
    _record("phase_start", active_test=item.nodeid, phase="teardown", outcome=None)


def pytest_runtest_logreport(report):
    _record("test_report", active_test=report.nodeid, phase=report.when, outcome=report.outcome)


def pytest_runtest_logfinish(nodeid):
    global _completed
    _completed += 1
    _record("test_finish", active_test=None, phase="between_tests", completed=_completed, last_finished_test=nodeid)


def pytest_sessionfinish(exitstatus):
    _record("session_finish", active_test=None, phase="finished", exit_code=int(exitstatus))
