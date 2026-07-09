#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/rally_point/peer_collision.py (EC-03 rca).

Two layers:
  * Pure decision (_has_peer / warn_line_for) — hermetic, no filesystem.
  * Integration through the real channel resolver + live-presence reader, with
    the apps root pointed at a tmp dir via BUILD_LOOP_APPS_ROOT.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import channel_paths  # noqa: E402
import peer_collision as pc  # noqa: E402


# --- pure decision ---------------------------------------------------------
def test_has_peer_with_self_id_any_entry_is_a_peer():
    assert pc._has_peer([{"tool": "codex"}], self_session="me") is True
    assert pc._has_peer([], self_session="me") is False


def test_has_peer_without_self_id_needs_two():
    # No self id → this session is still in the list; one entry could be just me.
    assert pc._has_peer([{"tool": "claude_code"}], self_session="") is False
    assert pc._has_peer([{"tool": "claude_code"}, {"tool": "codex"}], self_session="") is True


def test_warn_line_names_peer_tool_and_prescribes_worktree():
    line = pc.warn_line_for([{"tool": "codex"}], self_session="me")
    assert "peer active on this workdir" in line
    assert "codex" in line
    assert "worktree" in line


def test_warn_line_empty_when_no_peer():
    assert pc.warn_line_for([], self_session="me") == ""


# --- integration through the real resolver ---------------------------------
def _write_presence(channel_dir: Path, session_id: str, tool: str) -> None:
    sessions = channel_dir / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.json").write_text(json.dumps({
        "session_id": session_id,
        "tool": tool,
        "model": "test",
        "run_id": "n/a",
        "app_slug": "",
        "phase": "build",
        "files_in_flight": [],
        "heartbeat_ts": time.time(),  # fresh → live peer
    }))


def test_collision_warn_fires_for_peer_on_same_workdir(tmp_path, monkeypatch):
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", str(tmp_path / "apps"))
    workdir = tmp_path / "repo"
    workdir.mkdir()
    cdir = pc._channel_dir(workdir)
    _write_presence(cdir, "sess-self", "claude_code")
    _write_presence(cdir, "sess-peer", "codex")
    # Excluding self → the codex peer remains → WARN.
    line = pc.collision_warn(workdir, self_session="sess-self")
    assert "peer active on this workdir" in line and "codex" in line


def test_collision_warn_silent_when_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", str(tmp_path / "apps"))
    workdir = tmp_path / "repo"
    workdir.mkdir()
    cdir = pc._channel_dir(workdir)
    _write_presence(cdir, "sess-self", "claude_code")
    assert pc.collision_warn(workdir, self_session="sess-self") == ""


def test_collision_warn_no_room_is_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", str(tmp_path / "apps"))
    workdir = tmp_path / "repo"
    workdir.mkdir()
    # No channel dir created → fail-open empty.
    assert pc.collision_warn(workdir, self_session="sess-self") == ""


def test_main_exit_zero_and_prints_to_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", str(tmp_path / "apps"))
    workdir = tmp_path / "repo"
    workdir.mkdir()
    cdir = pc._channel_dir(workdir)
    _write_presence(cdir, "sess-self", "claude_code")
    _write_presence(cdir, "sess-peer", "codex")
    rc = pc.main(["--workdir", str(workdir), "--session-id", "sess-self"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""            # advisory goes to stderr, not stdout
    assert "peer active on this workdir" in captured.err
