#!/usr/bin/env python3
"""Tests for unbounded_wait_gate.

The gate BLOCKS, so its false-positive behavior matters more than its
true-positive behavior: a wrongly-rejected command wedges a session, which is a
worse and far more common failure than one missed sleep loop. Most of these
cases are therefore negative controls.
"""

from __future__ import annotations

import importlib.util
import io
import json
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "unbounded_wait_gate", Path(__file__).with_name("unbounded_wait_gate.py")
)
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


class Blocks(unittest.TestCase):
    def test_the_observed_failure(self) -> None:
        # Verbatim shape from the 2026-07-27 atomize-ai run.
        self.assertIsNotNone(gate.evaluate("while true; do sleep 30; done"))

    def test_infinite_variants(self) -> None:
        for cmd in (
            "while :; do sleep 5; done",
            "until false; do sleep 10; done",
            "for ((;;)); do sleep 60; done",
            "WHILE TRUE; DO SLEEP 30; DONE",
            "echo start && while true; do sleep 15; done",
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(gate.evaluate(cmd))

    def test_long_bare_sleep(self) -> None:
        self.assertIsNotNone(gate.evaluate("sleep 300"))
        self.assertIsNotNone(gate.evaluate("sleep 120"))


class Allows(unittest.TestCase):
    def test_the_idiom_build_loop_already_ships(self) -> None:
        # agents/architecture-scout.md:180 — bounded, with an escape.
        cmd = (
            'for i in $(seq 1 30); do pgrep -f "architecture scan" >/dev/null '
            "|| break; sleep 1; done"
        )
        self.assertIsNone(gate.evaluate(cmd))

    def test_infinite_loop_with_an_escape(self) -> None:
        for cmd in (
            "while true; do check && break; sleep 5; done",
            "while true; do sleep 5; [ -f done.flag ] && exit 0; done",
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNone(gate.evaluate(cmd))

    def test_short_settle_sleeps_are_untouched(self) -> None:
        for cmd in ("npm run dev & sleep 3", "sleep 2", "sleep 119"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(gate.evaluate(cmd))

    def test_no_sleep_no_opinion(self) -> None:
        for cmd in ("while true; do read -r l; done", "git commit -m x", ""):
            with self.subTest(cmd=cmd):
                self.assertIsNone(gate.evaluate(cmd))

    def test_the_word_sleep_in_prose_is_not_a_wait(self) -> None:
        self.assertIsNone(gate.evaluate('git commit -m "fix sleep tracking bug"'))


class Plumbing(unittest.TestCase):
    def _run(self, payload: object) -> int:
        sys_stdin, sys_stderr = gate.sys.stdin, gate.sys.stderr
        gate.sys.stdin = io.StringIO(json.dumps(payload))
        gate.sys.stderr = io.StringIO()
        try:
            return gate.main()
        finally:
            gate.sys.stdin, gate.sys.stderr = sys_stdin, sys_stderr

    def test_blocks_with_exit_2(self) -> None:
        rc = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "while true; do sleep 30; done"}}
        )
        self.assertEqual(rc, 2)

    def test_clean_command_exits_0(self) -> None:
        rc = self._run({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(rc, 0)

    def test_non_bash_tools_are_ignored(self) -> None:
        rc = self._run(
            {"tool_name": "Write", "tool_input": {"command": "while true; do sleep 30; done"}}
        )
        self.assertEqual(rc, 0)

    def test_fails_open_on_junk(self) -> None:
        for payload in ({}, {"tool_name": "Bash"}, {"tool_name": "Bash", "tool_input": "nope"}):
            with self.subTest(payload=payload):
                self.assertEqual(self._run(payload), 0)

    def test_fails_open_on_unparseable_stdin(self) -> None:
        sys_stdin = gate.sys.stdin
        gate.sys.stdin = io.StringIO("not json")
        try:
            self.assertEqual(gate.main(), 0)
        finally:
            gate.sys.stdin = sys_stdin


class HostResumeHint(unittest.TestCase):
    """The rejection must name the primitive THIS host has.

    Before 2026-08-22 the gate gave one host-agnostic snippet, so build-loop's
    only self-resume advice silently assumed a Claude-only harness feature.
    """

    INFINITE = "while true; do " + "sle" + "ep 30; done"

    def test_claude_code_is_told_to_use_schedule_wakeup(self) -> None:
        msg = gate.evaluate(self.INFINITE, env={"CLAUDE_PLUGIN_ROOT": "/x"})
        self.assertIn("ScheduleWakeup", msg)
        self.assertIn("claude_code", msg)

    def test_codex_is_never_told_to_use_schedule_wakeup(self) -> None:
        msg = gate.evaluate(self.INFINITE, env={"CODEX_HOME": "/x"})
        self.assertNotIn("ScheduleWakeup", msg)
        self.assertIn("coordination_watch.py", msg)
        self.assertIn("codex", msg)

    def test_unknown_host_gets_the_universal_fallback(self) -> None:
        msg = gate.evaluate(self.INFINITE, env={})
        self.assertNotIn("ScheduleWakeup", msg)
        self.assertIn("coordination_watch.py", msg)

    def test_long_bare_sleep_also_carries_the_hint(self) -> None:
        msg = gate.evaluate("sl" + "eep 300", env={"CLAUDE_PLUGIN_ROOT": "/x"})
        self.assertIn("ScheduleWakeup", msg)

    def test_allowed_commands_get_no_hint(self) -> None:
        # The hint rides on a rejection; it must never appear on a clean command.
        self.assertIsNone(gate.evaluate("sl" + "eep 2", env={"CLAUDE_PLUGIN_ROOT": "/x"}))

    def test_fails_open_when_wake_scheduler_is_unreachable(self) -> None:
        # A gate that cannot resolve the host still has a correct host-agnostic
        # answer, and must never hard-fail or drop the rejection.
        real = gate.Path

        class _Missing:
            def __init__(self, *a, **k) -> None: ...
            def resolve(self):
                raise OSError("no filesystem")

        gate.Path = _Missing
        try:
            self.assertIsNone(gate._host_resume_hint({"CLAUDE_PLUGIN_ROOT": "/x"}))
            msg = gate.evaluate(self.INFINITE, env={"CLAUDE_PLUGIN_ROOT": "/x"})
            self.assertIsNotNone(msg)
            self.assertIn("bound it", msg)
            self.assertNotIn("resume primitive", msg)
        finally:
            gate.Path = real

    def test_host_detection_does_not_leak_into_the_real_environment(self) -> None:
        import os

        before = dict(os.environ)
        gate.evaluate(self.INFINITE, env={"CODEX_HOME": "/x"})
        self.assertEqual(dict(os.environ), before)


if __name__ == "__main__":
    unittest.main()
