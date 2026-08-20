#!/usr/bin/env python3
"""Tests for exact-once system-access dispatch. No test invokes macOS tools."""
from __future__ import annotations

import argparse
import importlib.util
import tempfile
import threading
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("system_access_request.py")
SPEC = importlib.util.spec_from_file_location("system_access_request", SCRIPT)
assert SPEC and SPEC.loader
access = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(access)


def args(state_dir: Path, **overrides):
    values = {
        "command": ["fake-system-tool", "dump"], "purpose": "Inspect background items",
        "scope": "Background task registration", "requester": "codex:test", "risk": "read-only",
        "state_dir": str(state_dir), "wait_seconds": 1.0, "undispatched_seconds": 0.01,
        "dedupe_seconds": 300,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class Result:
    def __init__(self, returncode=0): self.returncode = returncode


class SystemAccessRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "state"

    def tearDown(self): self.temp.cleanup()

    def test_completed_request_is_dispatched_once_and_reused(self):
        calls = []
        def runner(command, check=False): calls.append(command); return Result(0)
        self.assertEqual(access.run_request(args(self.state), runner), 0)
        self.assertEqual(access.run_request(args(self.state), runner), 0)
        self.assertEqual(calls, [["fake-system-tool", "dump"]])

    def test_identical_follower_waits_for_leader_without_second_dispatch(self):
        calls, outcome = [], []
        started = threading.Event()
        release = threading.Event()
        def runner(command, check=False):
            calls.append(command); started.set(); release.wait(1); return Result(0)
        leader = threading.Thread(target=lambda: outcome.append(access.run_request(args(self.state), runner)))
        leader.start(); self.assertTrue(started.wait(1))
        follower = threading.Thread(target=lambda: outcome.append(access.run_request(args(self.state, requester="codex:follower"), runner)))
        follower.start(); time.sleep(0.05); release.set()
        leader.join(1); follower.join(1)
        self.assertEqual(sorted(outcome), [0, 0])
        self.assertEqual(len(calls), 1)

    def test_failed_start_is_the_only_automatic_retry_case(self):
        calls = []
        def missing(command, check=False):
            calls.append(command); raise FileNotFoundError("missing")
        self.assertEqual(access.run_request(args(self.state), missing), 127)
        self.assertEqual(access.run_request(args(self.state), lambda *a, **k: Result(0)), 0)
        self.assertEqual(len(calls), 1)

    def test_dispatched_failure_is_recorded_and_not_retried(self):
        calls = []
        def denied(command, check=False): calls.append(command); return Result(1)
        self.assertEqual(access.run_request(args(self.state), denied), 1)
        self.assertEqual(access.run_request(args(self.state), denied), 1)
        self.assertEqual(len(calls), 1)

    def test_terminal_result_is_a_new_request_only_after_the_dedupe_window(self):
        calls = []

        def runner(command, check=False):
            calls.append(command)
            return Result(0)

        self.assertEqual(access.run_request(args(self.state), runner), 0)
        time.sleep(0.02)
        self.assertEqual(
            access.run_request(args(self.state, dedupe_seconds=0.01), runner),
            0,
        )
        self.assertEqual(len(calls), 2)

    def test_stale_undispatched_request_can_be_replaced_without_touching_dispatched(self):
        signature = access._signature(["fake-system-tool", "dump"], "Inspect background items", "Background task registration", "read-only")
        with access._locked_ledger(self.state) as (_, ledger):
            ledger["requests"][signature] = {"status": "requested", "created_at": 0, "id": "old", "waiters": []}
        self.assertEqual(access.run_request(args(self.state), lambda *a, **k: Result(0)), 0)
        with access._locked_ledger(self.state) as (_, ledger):
            self.assertEqual(ledger["requests"][signature]["status"], "completed")

    def test_mutating_request_is_rejected_before_dispatch(self):
        with self.assertRaises(ValueError):
            access.run_request(args(self.state, risk="mutating"), lambda *a, **k: Result(0))

    def test_password_argument_is_never_written_to_the_ledger_or_message(self):
        request = args(self.state, command=["fake-system-tool", "--password", "do-not-store"])
        self.assertEqual(access.run_request(request, lambda *a, **k: Result(0)), 0)
        ledger = (self.state / "ledger.json").read_text(encoding="utf-8")
        self.assertNotIn("do-not-store", ledger)
        self.assertIn("<redacted>", ledger)


if __name__ == "__main__":
    unittest.main()
