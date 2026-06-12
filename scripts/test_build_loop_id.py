# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the build_loop_id run-identity layer.

Acceptance criteria covered:
  AC-A1: Phase 1 Assess generates build_loop_id; persists to state.execution.
  AC-A2: Resume preserves build_loop_id, updates only current_session_id.
  AC-A3: Every rally write carries top-level build_loop_id fields
         (post + inbox + presence) — NOT nested in producer_metadata.
  AC-A4: Producer metadata fields remain untouched.
  AC-A5: Collision guard regenerates when the runs dir already exists.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from rally_point import build_loop_id as bli  # noqa: E402
from rally_point import changes as ch  # noqa: E402
from rally_point import inbox  # noqa: E402
from rally_point import post as post_mod  # noqa: E402
from rally_point import presence  # noqa: E402


def _state_path(workdir: Path) -> Path:
    return workdir / ".build-loop" / "state.json"


# ---------------------------------------------------------------------------
# AC-A1
# ---------------------------------------------------------------------------

def test_generate_at_phase_1_when_absent(tmp_path: Path):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    exec_block = bli.generate_or_resume(
        workdir, tool="claude_code", session_id="claude-abc"
    )
    assert exec_block["build_loop_id"].startswith("bl-")
    assert "claude_code" in exec_block["build_loop_id"]
    # Suffix is six digits.
    suffix = exec_block["build_loop_id"].rsplit("-", 1)[-1]
    assert len(suffix) == 6 and suffix.isdigit()
    assert exec_block["started_by_tool"] == "claude_code"
    assert exec_block["started_by_session_id"] == "claude-abc"
    assert exec_block["current_session_id"] == "claude-abc"
    assert exec_block["started_at"].endswith("Z")
    assert "claude_code#" in exec_block["run_label"]

    # State.json persisted.
    state = json.loads(_state_path(workdir).read_text())
    assert state["execution"]["build_loop_id"] == exec_block["build_loop_id"]

    # Runs dir created and named after the id.
    runs = workdir / ".build-loop" / "runs"
    assert (runs / exec_block["build_loop_id"]).is_dir()


def test_fresh_mint_clears_stale_per_run_state(tmp_path: Path):
    # A fresh run must not inherit the previous run's phase/triggers: stale
    # `phase: done` would let the Stop-hook closeout record a crashed new run
    # as pass; stale triggers would attribute the old run's stakes to it.
    workdir = tmp_path / "repo"
    (workdir / ".build-loop").mkdir(parents=True)
    _state_path(workdir).write_text(json.dumps({
        "phase": "done",
        "triggers": {"riskSurfaceChange": True},
        "runs": [{"run_id": "bl-old"}],
        "execution": {},  # no build_loop_id → fresh-mint path
    }))
    bli.generate_or_resume(workdir, tool="claude_code", session_id="s-new")
    state = json.loads(_state_path(workdir).read_text())
    assert "phase" not in state
    assert "triggers" not in state
    assert state["runs"] == [{"run_id": "bl-old"}]  # history untouched


def test_resume_preserves_phase_and_triggers(tmp_path: Path):
    # The clear fires ONLY on fresh mint; resuming an in-flight run must not
    # wipe its own live phase/triggers.
    workdir = tmp_path / "repo"
    (workdir / ".build-loop").mkdir(parents=True)
    _state_path(workdir).write_text(json.dumps({
        "phase": "execute",
        "triggers": {"riskSurfaceChange": True},
        "execution": {"build_loop_id": "bl-live", "current_session_id": "s-old"},
    }))
    bli.generate_or_resume(workdir, tool="claude_code", session_id="s-new")
    state = json.loads(_state_path(workdir).read_text())
    assert state["phase"] == "execute"
    assert state["triggers"] == {"riskSurfaceChange": True}
    assert state["execution"]["current_session_id"] == "s-new"


# ---------------------------------------------------------------------------
# AC-A2
# ---------------------------------------------------------------------------

def test_resume_preserves_build_loop_id_updates_current_session_id_only(
    tmp_path: Path,
):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    first = bli.generate_or_resume(
        workdir, tool="claude_code", session_id="session-1"
    )
    # Resume from a new session_id.
    second = bli.generate_or_resume(
        workdir, tool="codex", session_id="session-2"
    )
    # ALL immutable fields preserved.
    for k in (
        "build_loop_id",
        "started_at",
        "started_by_tool",
        "started_by_session_id",
        "run_label",
    ):
        assert second[k] == first[k], f"resume mutated immutable field {k!r}"
    # ONLY current_session_id updates.
    assert second["current_session_id"] == "session-2"
    # State.json reflects the resume.
    state = json.loads(_state_path(workdir).read_text())
    assert state["execution"]["current_session_id"] == "session-2"
    assert state["execution"]["build_loop_id"] == first["build_loop_id"]


# ---------------------------------------------------------------------------
# AC-A5 — collision guard
# ---------------------------------------------------------------------------

def test_collision_guard_regenerates_on_existence(tmp_path: Path, monkeypatch):
    """When the candidate runs/<id> dir already exists, regenerate."""
    workdir = tmp_path / "repo"
    workdir.mkdir()
    runs = workdir / ".build-loop" / "runs"
    runs.mkdir(parents=True)

    # Force the first candidate to collide, second to succeed.
    candidates = iter(["bl-X-tool-000001", "bl-X-tool-000001", "bl-X-tool-000002"])
    monkeypatch.setattr(bli, "_candidate_id", lambda tool, now: next(candidates))
    # Pre-create the colliding directory.
    (runs / "bl-X-tool-000001").mkdir()

    exec_block = bli.generate_or_resume(
        workdir, tool="tool", session_id="s",
        now=_dt.datetime(2026, 5, 25, tzinfo=_dt.timezone.utc),
    )
    assert exec_block["build_loop_id"] == "bl-X-tool-000002"


# ---------------------------------------------------------------------------
# AC-A3 — every rally write carries top-level fields
# ---------------------------------------------------------------------------

def _seed_run(workdir: Path) -> dict:
    return bli.generate_or_resume(
        workdir, tool="claude_code", session_id="session-1"
    )


def test_post_record_carries_top_level_build_loop_id_fields(
    tmp_path: Path,
):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    exec_block = _seed_run(workdir)
    channel = tmp_path / "channel"

    new_rev = post_mod.post(
        channel_dir=channel,
        kind="feedback",
        tool="claude_code",
        model="claude-opus-4-7",
        run_id="run-x",
        app_slug="build-loop",
        payload={"step": 1, "verdict": "PASS"},
        # Handoff-shape payload not used → no MECE gate.
        workdir=workdir,
    )
    assert new_rev is not None

    records, _off = ch.read_changes_since(channel, 0)
    assert len(records) == 1
    rec = records[0]
    # AC-A3: top-level keys.
    assert rec["build_loop_id"] == exec_block["build_loop_id"]
    assert rec["build_loop_started_at"] == exec_block["started_at"]
    assert rec["build_loop_run_label"] == exec_block["run_label"]


def test_inbox_write_message_carries_top_level_build_loop_id_fields(
    tmp_path: Path,
):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    exec_block = _seed_run(workdir)
    channel = tmp_path / "channel"
    channel.mkdir()

    inbox.write_message(
        channel,
        sender="claude_code",
        recipient="codex",
        payload={"hello": "world"},
        kind="message",
        workdir=workdir,
    )
    direct = inbox.inbox_path(channel, "codex")
    lines = [ln for ln in direct.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["build_loop_id"] == exec_block["build_loop_id"]
    assert rec["build_loop_started_at"] == exec_block["started_at"]
    assert rec["build_loop_run_label"] == exec_block["run_label"]


def test_inbox_send_to_tool_carries_top_level_build_loop_id_fields_in_inbox_and_channel(
    tmp_path: Path,
):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    exec_block = _seed_run(workdir)
    channel = tmp_path / "channel"
    channel.mkdir()

    inbox.send_to_tool(
        channel,
        sender="claude_code",
        recipient="codex",
        payload={"foo": "bar"},
        kind="message",
        model="claude-opus-4-7",
        run_id="run-x",
        app_slug="build-loop",
        workdir=workdir,
    )
    # Inbox carries the fields.
    direct = inbox.inbox_path(channel, "codex")
    inbox_rec = json.loads(direct.read_text().splitlines()[-1])
    assert inbox_rec["build_loop_id"] == exec_block["build_loop_id"]

    # Channel mirror via post() also carries the fields.
    records, _off = ch.read_changes_since(channel, 0)
    assert len(records) == 1
    assert records[0]["build_loop_id"] == exec_block["build_loop_id"]
    assert records[0]["build_loop_started_at"] == exec_block["started_at"]
    assert records[0]["build_loop_run_label"] == exec_block["run_label"]


def test_presence_write_carries_top_level_build_loop_id_fields(
    tmp_path: Path,
):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    exec_block = _seed_run(workdir)
    channel = tmp_path / "channel"
    channel.mkdir()

    presence.write_presence(
        channel,
        session_id="claude-abc",
        tool="claude_code",
        model="claude-opus-4-7",
        run_id="run-x",
        app_slug="build-loop",
        phase="rally-start",
        cwd=workdir,
    )
    rec = json.loads((channel / "sessions" / "claude-abc.json").read_text())
    assert rec["build_loop_id"] == exec_block["build_loop_id"]
    assert rec["build_loop_started_at"] == exec_block["started_at"]
    assert rec["build_loop_run_label"] == exec_block["run_label"]


# ---------------------------------------------------------------------------
# AC-A4 — orthogonality: NOT inside producer_metadata
# ---------------------------------------------------------------------------

def test_build_loop_id_NOT_in_producer_metadata(tmp_path: Path):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    _seed_run(workdir)
    channel = tmp_path / "channel"

    post_mod.post(
        channel_dir=channel,
        kind="feedback",
        tool="claude_code",
        model="claude-opus-4-7",
        run_id="run-x",
        app_slug="build-loop",
        payload={"step": 2, "verdict": "PASS"},
        workdir=workdir,
    )
    rec = ch.read_changes_since(channel, 0)[0][0]

    # Orthogonality: build_loop_* are top-level keys, producer_* are
    # separate top-level keys. Neither set contains the other.
    assert "build_loop_id" in rec
    assert "producer_name" in rec  # producer_metadata still attached
    assert "producer_version" in rec
    # The fields are siblings, not nested. No producer_* key should
    # carry a build_loop_id payload.
    for k, v in rec.items():
        if k.startswith("producer_") and isinstance(v, dict):
            assert "build_loop_id" not in v


# ---------------------------------------------------------------------------
# Graceful degradation: no state.execution → no fields, write still succeeds.
# ---------------------------------------------------------------------------

def test_post_without_state_execution_omits_build_loop_id_fields(
    tmp_path: Path,
):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    channel = tmp_path / "channel"
    # No state.json — no build_loop_id available.
    new_rev = post_mod.post(
        channel_dir=channel,
        kind="feedback",
        tool="claude_code",
        model="claude-opus-4-7",
        run_id="run-x",
        app_slug="build-loop",
        payload={"step": 3, "verdict": "PASS"},
        workdir=workdir,
    )
    assert new_rev is not None
    rec = ch.read_changes_since(channel, 0)[0][0]
    assert "build_loop_id" not in rec
    assert "build_loop_started_at" not in rec
    assert "build_loop_run_label" not in rec


def test_rally_fields_for_returns_empty_when_workdir_is_none():
    assert bli.rally_fields_for(None) == {}


def test_rally_fields_for_returns_empty_when_state_missing(tmp_path: Path):
    assert bli.rally_fields_for(tmp_path / "nope") == {}
