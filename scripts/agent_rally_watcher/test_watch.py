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

import os

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


# ---- C1 additions: --parent-pid + --max-lifetime-seconds ------------------
#
# Root cause covered (in addition to the existing _is_orphaned guard): the
# existing guard's `if initial_ppid <= 1: return False` carve-out misfires when
# the hook process exits before the child Python's main runs `os.getppid()`,
# leaving the watcher with `initial_ppid == 1` and immortal. The launcher now
# captures its own pid BEFORE Popen and threads it via --parent-pid; a
# --max-lifetime-seconds backstop guards even a hypothetical future failure of
# pid-based detection. Both must be exercised under env -u of agent-id vars so
# the test reflects real behavior, not rigged env (build-loop-memory feedback
# feedback_smoke_test_environment_rigging.md).


# ---- _is_parent_alive (pure helper) ---------------------------------------

def test_is_parent_alive_self_pid_is_alive():
    # The current process is obviously alive.
    assert watch._is_parent_alive(os.getpid()) is True


def test_is_parent_alive_dead_pid_returns_false(monkeypatch):
    # A definitely-dead pid (very large, never assigned) — os.kill raises
    # ProcessLookupError; helper must return False.
    def fake_kill(_pid, _sig):
        raise ProcessLookupError("no such process")
    monkeypatch.setattr(watch.os, "kill", fake_kill)
    assert watch._is_parent_alive(987654321) is False


def test_is_parent_alive_treats_eperm_as_alive(monkeypatch):
    # A cross-uid parent surfaces EPERM; the parent exists so treat as alive.
    def fake_kill(_pid, _sig):
        raise PermissionError("EPERM")
    monkeypatch.setattr(watch.os, "kill", fake_kill)
    assert watch._is_parent_alive(4242) is True


def test_is_parent_alive_low_pid_never_trips():
    # Match _is_orphaned semantics: pid<=1 means detached/launchd; do not
    # claim parent died.
    assert watch._is_parent_alive(0) is True
    assert watch._is_parent_alive(1) is True


# ---- _env_max_lifetime -----------------------------------------------------

def test_env_max_lifetime_default(monkeypatch):
    monkeypatch.delenv(watch._ENV_MAX_LIFETIME, raising=False)
    assert watch._env_max_lifetime() == watch._DEFAULT_MAX_LIFETIME_SECONDS


def test_env_max_lifetime_parses_env(monkeypatch):
    monkeypatch.setenv(watch._ENV_MAX_LIFETIME, "60.5")
    assert watch._env_max_lifetime() == 60.5


def test_env_max_lifetime_falls_back_on_bad_value(monkeypatch):
    monkeypatch.setenv(watch._ENV_MAX_LIFETIME, "not-a-float")
    assert watch._env_max_lifetime() == watch._DEFAULT_MAX_LIFETIME_SECONDS


# ---- main() integration with --parent-pid ---------------------------------

def test_explicit_parent_pid_dead_exits_first_loop(monkeypatch):
    """When --parent-pid is dead, watcher must exit on the FIRST loop
    iteration, BEFORE polling status. Proves the new mechanism succeeds
    where the legacy initial_ppid<=1 carve-out fails."""
    # Simulate the live bug exactly: initial_ppid will be captured as 1
    # (launcher already exited) so the legacy guard short-circuits, but the
    # explicit parent-pid liveness check still fires.
    monkeypatch.setattr(watch.os, "getppid", lambda: 1)  # legacy guard inert

    def dead_parent_kill(_pid, _sig):
        raise ProcessLookupError("parent gone")
    monkeypatch.setattr(watch.os, "kill", dead_parent_kill)

    def fail_if_called(_args):
        raise AssertionError("watcher must exit before polling status")
    monkeypatch.setattr(watch.coordination_status, "build_status", fail_if_called)

    rc = watch.main(
        ["--session-id", "test-explicit-parent",
         "--workdir", ".",
         "--parent-pid", "987654321",
         "--iterations", "5"]
    )
    assert rc == 0


def test_explicit_parent_pid_alive_keeps_polling(monkeypatch):
    """Live --parent-pid: watcher runs the bounded loop normally."""
    monkeypatch.setattr(watch.os, "getppid", lambda: 4242)
    # os.kill(self_pid, 0) is the default behavior — no monkeypatch needed.

    polled = {"n": 0}
    stub_status = {
        "status": "ok", "required_action": None, "revision": 0,
        "active_peers": [], "overlaps": [], "unresolved": [],
        "dirty_outside_owned": [], "new_changes": [],
    }
    def fake_build_status(_args):
        polled["n"] += 1
        return dict(stub_status)
    monkeypatch.setattr(watch.coordination_status, "build_status", fake_build_status)

    rc = watch.main(
        ["--session-id", "test-alive-parent",
         "--workdir", ".",
         "--parent-pid", str(os.getpid()),
         "--iterations", "3",
         "--interval", "0.01"]
    )
    assert rc == 0
    assert polled["n"] == 3


# ---- main() integration with --max-lifetime-seconds -----------------------

def test_max_lifetime_exceeded_exits_immediately(monkeypatch):
    """--max-lifetime-seconds 0 exits on the FIRST loop pass."""
    monkeypatch.setattr(watch.os, "getppid", lambda: 4242)

    def fail_if_called(_args):
        raise AssertionError("watcher must exit before polling status")
    monkeypatch.setattr(watch.coordination_status, "build_status", fail_if_called)

    rc = watch.main(
        ["--session-id", "test-lifetime",
         "--workdir", ".",
         "--max-lifetime-seconds", "0",
         "--iterations", "5"]
    )
    assert rc == 0


def test_max_lifetime_uses_env_default(monkeypatch):
    """When --max-lifetime-seconds is omitted, env var BUILD_LOOP_WATCHER_*
    sets the default."""
    monkeypatch.setattr(watch.os, "getppid", lambda: 4242)
    monkeypatch.setenv(watch._ENV_MAX_LIFETIME, "0")  # exit immediately

    def fail_if_called(_args):
        raise AssertionError("watcher must exit before polling status")
    monkeypatch.setattr(watch.coordination_status, "build_status", fail_if_called)

    rc = watch.main(
        ["--session-id", "test-env-lifetime",
         "--workdir", ".",
         "--iterations", "5"]
    )
    assert rc == 0


# ---- Regression: hook-exit race that the legacy guard could not catch -----

def test_hook_race_regression_new_guard_catches_what_legacy_missed(monkeypatch):
    """The verified bug: hook exits before child Python startup, so
    `os.getppid()` already returns 1 by the time `main()` captures
    initial_ppid. The legacy `if initial_ppid <= 1: return False` short-
    circuits and the watcher runs forever.

    With --parent-pid pointing at the (already-dead) launcher pid, the new
    helper still detects the dead owner and exits cleanly. This is the
    regression test for build-loop-memory
    lessons/2026-05-31-coordination-process-leak.md fix iteration 2.
    """
    # Reproduce live bug state: getppid() reports 1 from the very first call.
    monkeypatch.setattr(watch.os, "getppid", lambda: 1)

    # Legacy _is_orphaned never fires (carve-out at initial_ppid<=1).
    assert watch._is_orphaned(initial_ppid=1, current_ppid=1) is False

    # But the explicit parent-pid check is independent and still works.
    def dead_parent_kill(_pid, _sig):
        raise ProcessLookupError("launcher gone")
    monkeypatch.setattr(watch.os, "kill", dead_parent_kill)

    def fail_if_called(_args):
        raise AssertionError(
            "watcher must exit before polling status; the parent-pid liveness "
            "check is the second line of defense after the carve-out misfires"
        )
    monkeypatch.setattr(watch.coordination_status, "build_status", fail_if_called)

    rc = watch.main(
        ["--session-id", "test-hook-race",
         "--workdir", ".",
         "--parent-pid", "987654321",
         "--iterations", "5"]
    )
    assert rc == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
