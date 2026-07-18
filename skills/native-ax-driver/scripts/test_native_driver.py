#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
Tests for the pure launch / pid-capture / scope-selection helpers in
native_driver.py.

Honest scope: the live path — `launch` actually invoking macOS `open`,
snapshotting real GUI process pids via osascript, and driving the Swift AX
binary against the captured pid — requires a running macOS GUI session and
Accessibility permission. That path cannot be exercised headlessly and is
NOT unit-tested here; it is covered by the SKILL.md manual self-test
("Single-instance PID-scoped verification mode").

What IS tested here is the pure, deterministic logic that decides which pid
belongs to the freshly-launched instance and how the `open` invocation is
built — the part that must never guess. These functions take no OS action
and have no side effects, so they run anywhere pytest runs.
"""

from __future__ import annotations

import native_driver


# ─── select_new_pid ────────────────────────────────────────────────────────


def test_select_new_pid_single_new_pid():
    assert native_driver.select_new_pid({1, 2}, {1, 2, 3}) == 3


def test_select_new_pid_identical_sets_returns_none():
    assert native_driver.select_new_pid({1, 2}, {1, 2}) is None


def test_select_new_pid_two_new_pids_is_ambiguous():
    assert native_driver.select_new_pid({1, 2}, {1, 2, 3, 4}) is None


def test_select_new_pid_empty_before_one_after():
    assert native_driver.select_new_pid(set(), {42}) == 42


def test_select_new_pid_empty_before_empty_after():
    assert native_driver.select_new_pid(set(), set()) is None


def test_select_new_pid_pid_disappeared_is_not_new():
    # before has a pid that's gone in after; no new pid appeared.
    assert native_driver.select_new_pid({1, 2, 3}, {1, 2}) is None


# ─── build_launch_env ──────────────────────────────────────────────────────


def test_build_launch_env_sets_key_when_both_given():
    base = {"PATH": "/usr/bin"}
    env = native_driver.build_launch_env(base, "ET_STATE_DIR", "/tmp/state")
    assert env["ET_STATE_DIR"] == "/tmp/state"
    assert env["PATH"] == "/usr/bin"


def test_build_launch_env_does_not_mutate_input():
    base = {"PATH": "/usr/bin"}
    native_driver.build_launch_env(base, "ET_STATE_DIR", "/tmp/state")
    assert base == {"PATH": "/usr/bin"}
    assert "ET_STATE_DIR" not in base


def test_build_launch_env_no_state_env_var_returns_unchanged_copy():
    base = {"PATH": "/usr/bin"}
    env = native_driver.build_launch_env(base, None, "/tmp/state")
    assert env == base
    assert env is not base


def test_build_launch_env_no_state_dir_returns_unchanged_copy():
    base = {"PATH": "/usr/bin"}
    env = native_driver.build_launch_env(base, "ET_STATE_DIR", None)
    assert env == base
    assert env is not base


def test_build_launch_env_both_none_returns_unchanged_copy():
    base = {"PATH": "/usr/bin"}
    env = native_driver.build_launch_env(base, None, None)
    assert env == base
    assert env is not base


# ─── build_open_command ────────────────────────────────────────────────────


def test_build_open_command_always_includes_force_new_instance():
    cmd = native_driver.build_open_command(
        "/Applications/MyApp.app", by_bundle_id=False, fresh=False, args=[]
    )
    assert "-n" in cmd


def test_build_open_command_fresh_true_includes_dash_F():
    cmd = native_driver.build_open_command(
        "/Applications/MyApp.app", by_bundle_id=False, fresh=True, args=[]
    )
    assert "-F" in cmd


def test_build_open_command_fresh_false_excludes_dash_F():
    cmd = native_driver.build_open_command(
        "/Applications/MyApp.app", by_bundle_id=False, fresh=False, args=[]
    )
    assert "-F" not in cmd


def test_build_open_command_by_bundle_id_uses_dash_b():
    cmd = native_driver.build_open_command(
        "com.example.myapp", by_bundle_id=True, fresh=False, args=[]
    )
    assert "-b" in cmd
    assert cmd[cmd.index("-b") + 1] == "com.example.myapp"
    assert "com.example.myapp" not in cmd[: cmd.index("-b")]


def test_build_open_command_by_app_path_uses_raw_path():
    cmd = native_driver.build_open_command(
        "/Applications/MyApp.app", by_bundle_id=False, fresh=False, args=[]
    )
    assert "-b" not in cmd
    assert "/Applications/MyApp.app" in cmd


def test_build_open_command_appends_args_when_nonempty():
    cmd = native_driver.build_open_command(
        "/Applications/MyApp.app",
        by_bundle_id=False,
        fresh=False,
        args=["--flag", "value"],
    )
    assert "--args" in cmd
    idx = cmd.index("--args")
    assert cmd[idx + 1 :] == ["--flag", "value"]


def test_build_open_command_no_args_omits_dash_dash_args():
    cmd = native_driver.build_open_command(
        "/Applications/MyApp.app", by_bundle_id=False, fresh=False, args=[]
    )
    assert "--args" not in cmd


def test_build_open_command_full_shape_fresh_bundle_with_args():
    cmd = native_driver.build_open_command(
        "com.example.myapp",
        by_bundle_id=True,
        fresh=True,
        args=["--seed", "123"],
    )
    assert cmd == ["open", "-n", "-F", "-b", "com.example.myapp", "--args", "--seed", "123"]
