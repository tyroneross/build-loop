#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the agent-rally-watcher orphan-exit guard.

Root cause covered: per-session watchers spawned with no reaper leaked across
sessions (build-loop-memory lessons/2026-05-31-coordination-process-leak.md).
The guard makes a watcher self-exit once its owning session is gone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from agent_rally_watcher import watch  # noqa: E402


# ---- _is_orphaned (pure helper) -------------------------------------------

def test_alive_when_parent_unchanged():
    # Owner still alive: current ppid matches the pid captured at startup.
    assert watch._is_orphaned(initial_ppid=4242, current_ppid=4242) is False


def test_orphaned_when_reparented_to_init():
    # Owner died -> reparented to pid 1 (launchd/init) -> orphaned.
    assert watch._is_orphaned(initial_ppid=4242, current_ppid=1) is True


def test_orphaned_when_parent_pid_changes():
    # Any change of parent pid means the original owner is gone.
    assert watch._is_orphaned(initial_ppid=4242, current_ppid=9001) is True


def test_daemon_launch_never_trips():
    # Launched detached (initial ppid already 1): no owning session to outlive.
    assert watch._is_orphaned(initial_ppid=1, current_ppid=1) is False
    assert watch._is_orphaned(initial_ppid=0, current_ppid=1) is False


# ---- main() integration: exits 0 promptly when orphaned --------------------

def test_main_exits_when_orphaned(monkeypatch):
    """main() must return 0 on the first loop pass once orphaned, before doing
    any coordination work — so a dead-owner watcher dies within one interval."""
    # First getppid() call (initial capture) returns a real-looking pid;
    # every subsequent call returns 1 (reparented = orphaned).
    calls = {"n": 0}

    def fake_getppid():
        calls["n"] += 1
        return 4242 if calls["n"] == 1 else 1

    monkeypatch.setattr(watch.os, "getppid", fake_getppid)

    # If the guard fails, build_status would run; make that loud.
    def fail_if_called(_args):
        raise AssertionError("orphaned watcher must exit before polling status")

    monkeypatch.setattr(watch.coordination_status, "build_status", fail_if_called)

    rc = watch.main(
        ["--session-id", "test-orphan", "--workdir", ".", "--iterations", "5"]
    )
    assert rc == 0


def test_main_polls_when_alive(monkeypatch):
    """When not orphaned, main() polls normally and honors --iterations."""
    monkeypatch.setattr(watch.os, "getppid", lambda: 4242)  # stable owner

    polled = {"n": 0}
    stub_status = {
        "status": "ok",
        "required_action": None,
        "revision": 0,
        "active_peers": [],
        "overlaps": [],
        "unresolved": [],
        "dirty_outside_owned": [],
        "new_changes": [],
    }

    def fake_build_status(_args):
        polled["n"] += 1
        return dict(stub_status)

    monkeypatch.setattr(watch.coordination_status, "build_status", fake_build_status)

    rc = watch.main(
        ["--session-id", "test-alive", "--workdir", ".", "--iterations", "2",
         "--interval", "0.01"]
    )
    assert rc == 0
    assert polled["n"] == 2  # ran the full bounded loop, never self-exited


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
