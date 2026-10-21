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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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


class Allows(unittest.TestCase):
    def test_finite_wait_has_no_universal_duration_cutoff(self) -> None:
        for seconds in (119, 120, 121, 300):
            self.assertIsNone(gate.evaluate(f"sleep {seconds}; verify_late_delivery"))

    def test_loop_examples_are_data_in_a_source_ingest_write(self) -> None:
        for command in (
            "curl --data-urlencode 'text=while true; do sleep 30; done' https://example.invalid",
            'printf "%s" "until [ -f done ]; do sleep 5; done" > guide.md',
            r'curl --data-urlencode "text=example \"while true; do sleep 30; done\" end" https://example.invalid',
            "python3 - <<'PY'\ntext = 'while true; do sleep 30; done'\nPY",
        ):
            with self.subTest(command=command):
                self.assertIsNone(gate.evaluate(command))

    def test_shell_strings_and_command_substitutions_remain_executable(self) -> None:
        for command in ("bash -c 'while true; do sleep 30; done'",
                        "bash -lc 'while true; do sleep 30; done'",
                        "eval 'while true; do sleep 30; done'",
                        "bash <<'SH'\nwhile true; do sleep 30; done\nSH",
                        "echo foo#bar; while true; do sleep 30; done",
                        "echo ${name#prefix}; while true; do sleep 30; done",
                        "echo ok # comment with break\nwhile true; do sleep 30; done",
                        r'echo "\""; while true; do sleep 30; done',
                        r'while true; do printf "\""; sleep 30; done',
                        'echo "$(while true; do sleep 30; done)"'):
            with self.subTest(command=command):
                self.assertIsNotNone(gate.evaluate(command))

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


class ConditionPollWithNoIterationBound(unittest.TestCase):
    """The shape that actually cost two days, and the shapes that must stay allowed.

    `_INFINITE_HEADER` only matches a LITERAL infinite header (`while true`,
    `until false`). A condition-poll looks bounded — there is a condition right
    there — while nothing guarantees the condition can flip. That is the harder
    case to spot by eye, so the gate has to spot it.
    """

    def test_rejects_the_watcher_that_ran_for_two_days(self) -> None:
        # Measured 2026-09-04. `pgrep -f` matched the watcher's OWN command line,
        # because the pattern appears in its argv, so the negation was never true.
        cmd = ('until [ -s /tmp/fails.txt ] || ! pgrep -f "vitest run --reporter=dot" '
               '>/dev/null; do sleep 10; done; cat /tmp/fails.txt')
        self.assertIsNotNone(
            gate.evaluate(cmd),
            "the exact loop that ran for two days must be rejected",
        )

    def test_rejects_a_plain_until_on_a_file(self) -> None:
        self.assertIsNotNone(gate.evaluate("until [ -f /tmp/done ]; do sleep 5; done"))

    def test_rejects_polling_a_port_forever(self) -> None:
        self.assertIsNotNone(gate.evaluate("while ! curl -sf localhost:4200; do sleep 3; done"))

    def test_allows_the_bounded_idiom_the_gate_itself_recommends(self) -> None:
        # If this ever rejects, the gate is telling callers to write something it
        # then refuses — the fastest way to make a guard get bypassed.
        cmd = ('for i in $(seq 1 30); do curl -sf localhost:4200 && break; sleep 1; done')
        self.assertIsNone(gate.evaluate(cmd))

    def test_allows_an_iteration_bounded_poll_without_break(self) -> None:
        cmd = ('for i in $(seq 1 40); do st=$(gh run list --json status); '
               '[ "$st" = completed ] && break; sleep 12; done')
        self.assertIsNone(gate.evaluate(cmd))

    def test_allows_a_read_loop_which_is_bounded_by_its_input(self) -> None:
        self.assertIsNone(
            gate.evaluate('while read -r line; do echo "$line"; sleep 1; done < f.txt'))

    def test_comment_or_literal_break_does_not_supply_an_exit(self) -> None:
        for cmd in ("until [ -s out.txt ]; do sleep 5; done  # no bound, no break",
                    'while true; do echo "break"; sleep 5; done'):
            self.assertIsNotNone(gate.evaluate(cmd))
