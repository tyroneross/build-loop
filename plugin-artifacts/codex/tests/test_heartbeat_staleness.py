"""Path C acceptance gate — heartbeat-staleness without --resume (M4 primary signal).

Validates that re-dispatching /build-loop:run WITHOUT --resume after a crash
correctly surfaces the resume prompt via the heartbeat-staleness path. This
is the primary M4 signal that fires regardless of whether the Stop hook ran.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from resume_resolver import resolve  # noqa: E402
from state_finalize import annotate_if_incomplete  # noqa: E402
from write_run_entry import update_execution_state  # noqa: E402


def _start_incomplete(tmp_path: Path, *, run_id="run_stale_test", started_at=None):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = started_at or datetime(2026, 5, 6, 22, 0, 0, tzinfo=timezone.utc)
    update_execution_state(
        state_path, "start",
        run_id=run_id,
        queued_chunks=["c1", "c2"],
        file_ownership={"c1": ["c1.py"], "c2": ["c2.py"]},
        now=started_at,
    )
    return state_path, started_at


def test_path_c_fresh_heartbeat_no_prompt(tmp_path):
    """Re-dispatch ~30s after crash: heartbeat is still fresh; no prompt."""
    state_path, started = _start_incomplete(tmp_path)
    now = started + timedelta(seconds=30)
    env = resolve(tmp_path, "", now=now)
    assert env["decision"] == "fresh"


def test_path_c_stale_heartbeat_prompts_user(tmp_path):
    """Re-dispatch 10 minutes after crash: heartbeat is stale; surface prompt."""
    state_path, started = _start_incomplete(tmp_path, run_id="run_path_c")
    now = started + timedelta(minutes=10)
    env = resolve(tmp_path, "", now=now)
    assert env["decision"] == "prompt_user"
    assert env["run_id"] == "run_path_c"
    assert "Resume with `/build-loop:run --resume" not in env["reason"]  # SKILL.md owns the user-facing copy
    assert "incomplete build detected" in env["reason"]
    assert "10.0 min ago" in env["reason"]


def test_path_c_default_threshold_is_5_minutes(tmp_path):
    """Right at 4 minutes: still fresh. Right at 6 minutes: stale."""
    state_path, started = _start_incomplete(tmp_path)
    fresh = resolve(tmp_path, "", now=started + timedelta(minutes=4))
    assert fresh["decision"] == "fresh"
    stale = resolve(tmp_path, "", now=started + timedelta(minutes=6))
    assert stale["decision"] == "prompt_user"


def test_path_c_custom_threshold_honored(tmp_path):
    state_path, started = _start_incomplete(tmp_path)
    # 4 minutes; threshold lowered to 2 minutes → stale
    env = resolve(tmp_path, "", now=started + timedelta(minutes=4), staleness_minutes=2)
    assert env["decision"] == "prompt_user"


def test_path_c_after_phase_report_no_prompt(tmp_path):
    """Stop hook annotation does NOT fire on clean exit; heartbeat path also stays quiet."""
    state_path, started = _start_incomplete(tmp_path, run_id="run_clean_exit")
    update_execution_state(state_path, "complete")
    # Annotation refuses to fire on phase=report
    assert annotate_if_incomplete(tmp_path) is False
    # Even 30 minutes later, no prompt
    env = resolve(tmp_path, "", now=started + timedelta(minutes=30))
    assert env["decision"] == "fresh"


def test_path_c_secondary_signal_complements_primary(tmp_path):
    """When Stop hook DOES fire, both signals point at the same incomplete run."""
    state_path, started = _start_incomplete(tmp_path, run_id="run_dual_signal")
    # Stop hook fires (a clean SIGTERM scenario)
    annotated = annotate_if_incomplete(tmp_path)
    assert annotated is True
    # Heartbeat-staleness path still fires the prompt
    env = resolve(tmp_path, "", now=started + timedelta(minutes=10))
    assert env["decision"] == "prompt_user"
    assert env["execution_block"]["crash_signal"] == "stop_hook"
    assert env["execution_block"]["crashed_at"] is not None


def test_path_c_no_state_json_returns_fresh(tmp_path):
    """Greenfield: no state.json means no incomplete run."""
    env = resolve(tmp_path, "")
    assert env["decision"] == "fresh"
