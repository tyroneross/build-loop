#!/usr/bin/env python3
"""Durable scope coordination tests. Fake runners only; never invoke macOS tools."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

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
        "dedupe_seconds": 300, "check_only": False, "record_blocked": False,
        "evidence": None, "resume_request": None, "user_confirmation_ref": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class Result:
    def __init__(self, returncode=0): self.returncode = returncode


class SystemAccessRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.calls = []

    def runner(self, command, check=False):
        self.calls.append(command)
        return Result(0)

    def records(self):
        return list(json.loads((self.state / "ledger.json").read_text())["requests"].values())

    def invoke(self, **overrides):
        return access.run_request(args(self.state, **overrides), self.runner)

    def metadata(self, **overrides):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(self.invoke(command=[], **overrides), 0)
        return json.loads(output.getvalue())

    def test_completed_identical_command_reuses_status_without_execution(self):
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.invoke(purpose="Rephrased", requester="new-task"), 0)
        self.assertEqual(len(self.calls), 1)

    def test_scope_hold_survives_changed_argv_purpose_requester_and_scope_case(self):
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.invoke(command=["swift", "/different/helper.swift"],
                                     purpose="Different reason", requester="new-run",
                                     scope="  BACKGROUND  task registration "), access.HELD)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.records()), 1)

    def test_completed_cancelled_and_failed_holds_never_expire(self):
        for returncode in [0, 1, 130]:
            state = self.state / str(returncode)
            calls = []
            def runner(command, check=False):
                calls.append(command)
                return Result(returncode)
            with patch.object(access, "_now", return_value=10):
                self.assertEqual(access.run_request(args(state), runner), returncode)
            with patch.object(access, "_now", return_value=10**9):
                self.assertEqual(access.run_request(args(state, dedupe_seconds=0), runner),
                                 access.HELD if returncode == 0 else returncode)
            self.assertEqual(len(calls), 1)

    def test_different_scopes_do_not_share_results(self):
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.invoke(scope="Different data"), 0)
        self.assertEqual(len(self.calls), 2)

    def test_pending_different_callers_join_without_dispatch_or_false_success(self):
        started, release = threading.Event(), threading.Event()
        outcomes, calls = [], []
        def runner(command, check=False):
            calls.append(command)
            started.set()
            release.wait(2)
            return Result(0)
        leader = threading.Thread(target=lambda: outcomes.append(access.run_request(args(self.state), runner)))
        follower = threading.Thread(target=lambda: outcomes.append(access.run_request(
            args(self.state, command=["different-probe"], purpose="Reworded", requester="follower"), runner)))
        leader.start()
        try:
            self.assertTrue(started.wait(1))
            follower.start()
            time.sleep(0.05)
        finally:
            release.set()
            leader.join(2)
            if follower.ident is not None: follower.join(2)
        self.assertEqual(sorted(outcomes), [0, access.HELD])
        self.assertEqual(len(calls), 1)

    def seed_request(self, status="requested"):
        record = access._new_request(["fake-system-tool", "dump"], "Original", "Background task registration", "read-only", "owner")
        record.update(status=status, created_at=0)
        with access._locked_ledger(self.state) as (_, ledger):
            ledger["requests"]["legacy-signature"] = record
        return record

    def test_stale_requested_and_dispatched_stay_pending(self):
        for status in ["requested", "dispatched"]:
            with self.subTest(status=status):
                self.state = Path(self.temp.name) / status
                self.seed_request(status)
                self.assertEqual(self.invoke(wait_seconds=0, undispatched_seconds=0), access.HELD)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.records()[0]["status"], status)

    def test_failed_to_dispatch_remains_retryable(self):
        def missing(command, check=False): raise FileNotFoundError("fake tool absent")
        self.assertEqual(access.run_request(args(self.state), missing), 127)
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.records()), 2)

    def test_check_only_returns_metadata_without_runner_or_ledger_creation(self):
        metadata = self.metadata(check_only=True)
        self.assertEqual(metadata["status"], "not_requested")
        self.assertTrue(metadata["dispatch_allowed"])
        self.assertFalse(metadata["executed"])
        self.assertFalse((self.state / "ledger.json").exists())
        self.assertEqual(self.calls, [])

    def test_external_blocked_requires_evidence_and_holds_changed_commands(self):
        with self.assertRaises(ValueError): self.metadata(record_blocked=True)
        metadata = self.metadata(record_blocked=True, evidence="owned-requester-stop-receipt")
        self.assertEqual(metadata["status"], "blocked")
        self.assertFalse(metadata["dispatch_allowed"])
        self.assertEqual(self.invoke(command=["another-probe"]), access.HELD)
        self.assertEqual(self.calls, [])
        inspected = self.metadata(check_only=True)
        self.assertEqual(inspected["request_id"], metadata["request_id"])
        self.assertEqual(inspected["evidence"], "owned-requester-stop-receipt")

    def test_external_record_cannot_overwrite_pending_even_with_evidence(self):
        record = self.seed_request("dispatched")
        with self.assertRaises(ValueError):
            self.metadata(record_blocked=True, evidence="some-other-receipt")
        self.assertEqual(self.records()[0]["id"], record["id"])
        self.assertEqual(self.records()[0]["status"], "dispatched")
        self.assertEqual(self.calls, [])

    def test_resume_requires_exact_terminal_reference_and_human_reference(self):
        self.invoke()
        original = self.records()[0]["id"]
        for kwargs in [dict(resume_request=original),
                       dict(resume_request="wrong", user_confirmation_ref="user-message"),
                       dict(resume_request=original, user_confirmation_ref="user-message", scope="wrong scope")]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.invoke(**kwargs)
        self.assertEqual(self.invoke(resume_request=original, user_confirmation_ref="user-message"), 0)
        with self.assertRaises(ValueError):
            self.invoke(resume_request=original, user_confirmation_ref="same-user-message")
        latest = next(record for record in self.records() if not record.get("resumed_by"))
        with self.assertRaises(ValueError):
            self.invoke(resume_request=latest["id"], user_confirmation_ref=" user-message ")
        self.assertEqual(len(self.calls), 2)

    def test_blocked_can_resume_once_after_explicit_human_reference(self):
        record = self.metadata(record_blocked=True, evidence="owned-requester-stopped")
        self.assertEqual(self.invoke(resume_request=record["request_id"], user_confirmation_ref="user-message-2"), 0)
        with self.assertRaises(ValueError):
            self.invoke(resume_request=record["request_id"], user_confirmation_ref="user-message-2")
        self.assertEqual(len(self.calls), 1)

    def test_pending_cannot_be_resumed_even_with_human_reference(self):
        record = self.seed_request("requested")
        with self.assertRaises(ValueError):
            self.invoke(resume_request=record["id"], user_confirmation_ref="user-message")
        self.assertEqual(self.calls, [])

    def test_two_concurrent_resumes_can_dispatch_only_one_new_attempt(self):
        self.invoke()
        original = self.records()[0]["id"]
        barrier = threading.Barrier(2)
        outcomes = []
        def caller():
            barrier.wait()
            try:
                outcomes.append(self.invoke(resume_request=original, user_confirmation_ref="same-user-message"))
            except ValueError:
                outcomes.append("refused")
        callers = [threading.Thread(target=caller) for _ in range(2)]
        for thread in callers: thread.start()
        for thread in callers: thread.join(2)
        self.assertCountEqual(outcomes, [0, "refused"])
        self.assertEqual(len(self.calls), 2)

    def test_corrupt_ledger_fails_closed_and_is_preserved(self):
        self.state.mkdir()
        for data in ['{', '[]', '{"requests": []}', '{"requests": {"bad": {"status": "completed"}}}']:
            with self.subTest(data=data):
                (self.state / "ledger.json").write_text(data)
                with self.assertRaises(ValueError): self.invoke()
                self.assertEqual((self.state / "ledger.json").read_text(), data)
        self.assertEqual(self.calls, [])

    def test_structurally_valid_json_with_invalid_timestamp_fails_closed(self):
        record = self.seed_request("completed")
        path = self.state / "ledger.json"
        ledger = json.loads(path.read_text())
        ledger["requests"]["legacy-signature"]["created_at"] = "not-a-time"
        path.write_text(json.dumps(ledger))
        before = path.read_text()
        with self.assertRaises(ValueError): self.invoke()
        self.assertEqual(path.read_text(), before)
        self.assertEqual(self.calls, [])

    def test_interrupted_runner_keeps_dispatched_hold_without_retry(self):
        def interrupted(command, check=False): raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            access.run_request(args(self.state), interrupted)
        self.assertEqual(self.records()[0]["status"], "dispatched")
        self.assertEqual(self.invoke(wait_seconds=0), access.HELD)
        self.assertEqual(self.calls, [])

    def test_atomic_write_failure_preserves_old_ledger_and_prevents_dispatch(self):
        self.invoke()
        before = (self.state / "ledger.json").read_bytes()
        original = self.records()[0]["id"]
        with patch.object(access.os, "replace", side_effect=OSError("fake disk failure")):
            with self.assertRaises(OSError):
                self.invoke(resume_request=original, user_confirmation_ref="user-message")
        self.assertEqual((self.state / "ledger.json").read_bytes(), before)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(list(self.state.glob('.ledger-*')), [])

    def test_legacy_terminal_record_holds_after_ttl_and_changed_purpose(self):
        record = self.seed_request("failed")
        with access._locked_ledger(self.state) as (_, ledger):
            legacy = ledger["requests"]["legacy-signature"]
            legacy.pop("command_signature")
            legacy.update(finished_at=0, exit_code=1)
        self.assertEqual(self.invoke(purpose="New words", dedupe_seconds=0), 1)
        self.assertEqual(self.invoke(command=["other"]), access.HELD)
        self.assertEqual(self.calls, [])

    def test_legacy_newer_dispatch_failure_does_not_erase_older_terminal_hold(self):
        held = self.seed_request("failed")
        with access._locked_ledger(self.state) as (_, ledger):
            ledger["requests"]["legacy-signature"].update(exit_code=1, finished_at=0)
            newer = access._new_request(["other-probe"], "New reason", held["scope"], "read-only", "other")
            newer.update(status=access.RETRYABLE, exit_code=127)
            ledger["requests"]["second-legacy-signature"] = newer
        self.assertEqual(self.invoke(command=["other-probe"]), access.HELD)
        self.assertEqual(self.calls, [])

    def test_cli_parser_accepts_normal_separator_without_live_command(self):
        run = access.run_request
        with patch.object(access, "run_request", side_effect=lambda parsed: run(parsed, self.runner)):
            result = access.main(["--purpose", "Test parser", "--scope", "Test scope",
                                  "--state-dir", str(self.state), "--", "fake-system-tool", "dump"])
        self.assertEqual(result, 0)
        self.assertEqual(self.calls, [["fake-system-tool", "dump"]])

    def test_normal_double_dash_separator_is_accepted(self):
        self.assertEqual(self.invoke(command=["--", "fake-system-tool", "dump"]), 0)
        self.assertEqual(self.calls, [["fake-system-tool", "dump"]])

    def test_mutations_and_empty_attribution_refused_before_dispatch(self):
        for kwargs in [dict(risk="mutating"), dict(purpose=" "), dict(scope=""), dict(requester=" ")]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.invoke(**kwargs)
        self.assertEqual(self.calls, [])

    def test_password_argument_is_not_written_to_ledger_or_message(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(self.invoke(command=["fake-system-tool", "--password", "do-not-store"]), 0)
        text = (self.state / "ledger.json").read_text() + output.getvalue()
        self.assertNotIn("do-not-store", text)
        self.assertIn("<redacted>", text)

    def test_cli_io_and_value_errors_return_clean_nonzero(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output), patch.object(access, "run_request", side_effect=OSError("fake I/O error")):
            self.assertEqual(access.main(["--scope", "test", "--purpose", "test", "--check-only"]), 2)
        self.assertIn("REFUSED", output.getvalue())


if __name__ == "__main__":
    unittest.main()
