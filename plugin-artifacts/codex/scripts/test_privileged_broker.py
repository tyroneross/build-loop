#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for privileged_broker.py. Zero deps. Run: python3 test_privileged_broker.py

NO TEST EVER RUNS A REAL PRIVILEGED COMMAND. Every case drives a fake executable
(``_fake_tool``) that records its own invocations, so "did macOS prompt?" is
measured as "did the broker invoke the command?" — the same proxy the broker
reports. A test suite that shelled out to ``sudo`` would be untrustworthy and
un-runnable in CI.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "privileged_broker.py"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import privileged_broker as pb  # noqa: E402


FAKE_TOOL_SRC = """#!/usr/bin/env python3
import os, sys, time
counter = os.environ["FAKE_COUNTER"]
with open(counter, "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\\n")
delay = float(os.environ.get("FAKE_DELAY", "0"))
if delay:
    time.sleep(delay)
sys.stdout.write(os.environ.get("FAKE_STDOUT", "OK"))
sys.stderr.write(os.environ.get("FAKE_STDERR", ""))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


def make_registry(tmp: Path, tool: Path, **overrides) -> dict:
    """Registry whose only privileged executable is the fake tool."""
    entry = {
        "id": "fake-read",
        "executable": tool.name,
        "argv_prefix": ["read"],
        "scope": "fake:read",
        "mutating": False,
        "cacheable": True,
        "ttl_seconds": 900,
        "negative_ttl_seconds": 600,
        "confidence": "observed",
    }
    entry.update(overrides)
    return {
        "schema": "buildloop.privileged.registry/1",
        "version": "test.1",
        "defaults": {
            "trust_domain": "local-admin",
            "ttl_seconds": 0,
            "negative_ttl_seconds": 300,
            "cacheable": False,
            "mutating": True,
            "purpose_required": True,
        },
        "entries": [
            entry,
            {
                "id": "fake-write",
                "executable": tool.name,
                "argv_prefix": ["write"],
                "scope": "fake:write",
                "mutating": True,
                "cacheable": False,
                "ttl_seconds": 0,
                "confidence": "observed",
            },
        ],
    }


class BrokerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="privbroker-"))
        self.root = self.tmp / "store"
        self.counter = self.tmp / "invocations.log"
        self.counter.write_text("", encoding="utf-8")

        self.tool = self.tmp / "faketool"
        self.tool.write_text(FAKE_TOOL_SRC, encoding="utf-8")
        self.tool.chmod(self.tool.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        os.environ["FAKE_COUNTER"] = str(self.counter)
        for key in ("FAKE_DELAY", "FAKE_EXIT", "FAKE_STDOUT", "FAKE_STDERR"):
            os.environ.pop(key, None)
        os.environ.pop("SUDO_ASKPASS", None)

        self.registry = make_registry(self.tmp, self.tool)
        self.config = json.loads(json.dumps(pb.DEFAULT_CONFIG))
        self.config["default_timeout_seconds"] = 20
        self.config["heartbeat_seconds"] = 0.05
        # Reset the once-per-process Ambient gap latch so each test sees a receipt.
        pb._AMBIENT_GAP_ONCE.clear()

    def tearDown(self) -> None:
        import shutil

        # Restore any directory a test made unwritable, BEFORE rmtree — otherwise
        # the tree survives and the temp dir leaks.
        for path in getattr(self, "_locked_dirs", []):
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------
    def invocations(self) -> list[str]:
        return [ln for ln in self.counter.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def req(self, *args: str, purpose: str = "test", task_id: str = "T1", **kw) -> dict:
        kw.setdefault("initiating_app", "pytest")
        return pb.build_request(
            argv=[str(self.tool), *args], purpose=purpose, task_id=task_id,
            registry=self.registry, **kw,
        )

    def run_one(self, *args: str, task_id: str = "T1", timeout: float = 20, **kw) -> dict:
        return pb.execute(self.req(*args, task_id=task_id, **kw),
                          timeout=timeout, root=self.root, config=self.config, quiet=True)

    def run_concurrent(self, requests: list[dict], timeout: float = 20) -> list[dict]:
        results: list[dict | None] = [None] * len(requests)
        errors: list[str] = []

        def worker(i: int, r: dict) -> None:
            try:
                results[i] = pb.execute(r, timeout=timeout, root=self.root, config=self.config, quiet=True)
            except Exception:  # noqa: BLE001 - a swallowed worker crash would fake a pass
                import traceback

                errors.append(traceback.format_exc())

        threads = [threading.Thread(target=worker, args=(i, r)) for i, r in enumerate(requests)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout + 10)
        self.assertEqual(errors, [], "a concurrent worker raised")
        self.assertNotIn(None, results, "a concurrent worker never returned")
        return [r for r in results if r is not None]

    def ledger_events(self) -> list[dict]:
        path = self.root / "ledger.jsonl"
        if not path.exists():
            return []
        return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def gaps(self) -> list[dict]:
        path = self.root / "gaps.jsonl"
        if not path.exists():
            return []
        return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ==========================================================================
# Acceptance 7 — deterministic concurrency
# ==========================================================================
class TestCoalescing(BrokerCase):
    def test_two_identical_readonly_requests_produce_one_prompt(self):
        os.environ["FAKE_DELAY"] = "0.4"
        results = self.run_concurrent([self.req("read", task_id="A"), self.req("read", task_id="B")])
        self.assertEqual(len(self.invocations()), 1, "identical read-only requests must single-flight")
        self.assertEqual({r["state"] for r in results}, {"completed"})
        self.assertEqual(sum(1 for r in results if r["prompt_opened"]), 1)
        self.assertEqual(sum(1 for r in results if r["coalesced"]), 1)
        for r in results:
            self.assertEqual(r["stdout"], "OK", "the waiter must receive the owner's result")

    def test_three_identical_requests_produce_one_prompt(self):
        os.environ["FAKE_DELAY"] = "0.4"
        reqs = [self.req("read", task_id=f"T{i}") for i in range(3)]
        results = self.run_concurrent(reqs)
        self.assertEqual(len(self.invocations()), 1)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r["state"] == "completed" for r in results))

    def test_different_arguments_never_coalesce(self):
        os.environ["FAKE_DELAY"] = "0.3"
        results = self.run_concurrent(
            [self.req("read", "alpha", task_id="A"), self.req("read", "beta", task_id="B")]
        )
        self.assertEqual(len(self.invocations()), 2, "different argv is a different request")
        self.assertTrue(all(r["prompt_opened"] for r in results))

    def test_different_scope_never_coalesces(self):
        os.environ["FAKE_DELAY"] = "0.3"
        a = self.req("read", task_id="A")
        b = self.req("read", task_id="B", scope="fake:other-scope")
        self.assertNotEqual(a["key"], b["key"])
        self.run_concurrent([a, b])
        self.assertEqual(len(self.invocations()), 2)

    def test_different_trust_domain_never_coalesces(self):
        os.environ["FAKE_DELAY"] = "0.3"
        a = self.req("read", task_id="A")
        b = self.req("read", task_id="B", trust_domain="remote-admin")
        self.assertNotEqual(a["key"], b["key"])
        self.run_concurrent([a, b])
        self.assertEqual(len(self.invocations()), 2)

    def test_mutating_requests_never_coalesce(self):
        os.environ["FAKE_DELAY"] = "0.3"
        results = self.run_concurrent([self.req("write", task_id="A"), self.req("write", task_id="B")])
        self.assertEqual(len(self.invocations()), 2, "a mutation must get its own authorization")
        self.assertTrue(all(r["prompt_opened"] for r in results))
        self.assertFalse(any(r["coalesced"] for r in results))

    def test_mutating_never_inherits_a_readonly_approval(self):
        self.run_one("read", task_id="A")
        self.assertEqual(len(self.invocations()), 1)
        # Same executable, cached read-only approval on file. The mutation must
        # still open its own prompt.
        out = self.run_one("write", task_id="B")
        self.assertEqual(len(self.invocations()), 2)
        self.assertTrue(out["prompt_opened"])
        self.assertFalse(out["coalesced"])

    def test_second_identical_request_replays_the_cache_without_prompting(self):
        first = self.run_one("read", task_id="A")
        second = self.run_one("read", task_id="B")
        self.assertEqual(len(self.invocations()), 1)
        self.assertTrue(first["prompt_opened"])
        self.assertFalse(second["prompt_opened"])
        self.assertEqual(second["source"], "cache")
        self.assertEqual(second["stdout"], "OK")


class TestNegativeOutcomes(BrokerCase):
    def test_denial_reaches_every_waiter(self):
        os.environ["FAKE_DELAY"] = "0.4"
        os.environ["FAKE_EXIT"] = "1"
        os.environ["FAKE_STDERR"] = "Operation not permitted"
        results = self.run_concurrent([self.req("read", task_id=f"T{i}") for i in range(3)])
        self.assertEqual(len(self.invocations()), 1)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r["state"] == "denied" for r in results),
                        f"every waiter must see the denial, got {[r['state'] for r in results]}")

    def test_denial_is_remembered_so_the_retry_does_not_reprompt(self):
        """The exact 2026-08-20 failure: a refused read retried 14s later."""
        os.environ["FAKE_EXIT"] = "1"
        os.environ["FAKE_STDERR"] = "Operation not permitted"
        first = self.run_one("read", task_id="A")
        self.assertEqual(first["state"], "denied")
        self.assertEqual(len(self.invocations()), 1)

        # The agent retries with a different shell wrapper — same privileged argv.
        second = self.run_one("read", task_id="A")
        self.assertEqual(second["state"], "denied", "a cached negative replays as a negative")
        self.assertFalse(second["prompt_opened"], "the retry must NOT open a second dialog")
        self.assertEqual(len(self.invocations()), 1, "no second invocation")

        third = self.run_one("read", task_id="B")
        self.assertFalse(third["prompt_opened"])
        self.assertEqual(len(self.invocations()), 1)

    def test_a_cached_negative_is_never_upgraded_to_an_approval(self):
        os.environ["FAKE_EXIT"] = "1"
        os.environ["FAKE_STDERR"] = "Operation not permitted"
        self.run_one("read", task_id="A")
        os.environ["FAKE_EXIT"] = "0"  # the command would now succeed
        replay = self.run_one("read", task_id="B")
        self.assertEqual(replay["state"], "denied")
        self.assertNotIn(replay["state"], pb.APPROVED_STATES)

    def test_timeout_is_a_negative_not_a_silent_pass(self):
        os.environ["FAKE_DELAY"] = "5"
        out = self.run_one("read", task_id="A", timeout=1)
        self.assertEqual(out["state"], "timeout")
        self.assertNotIn(out["state"], pb.APPROVED_STATES)


class TestExpiry(BrokerCase):
    def test_stale_result_expires_then_reinvokes_exactly_once(self):
        self.registry["entries"][0]["ttl_seconds"] = 1
        first = self.run_one("read", task_id="A")
        self.assertTrue(first["prompt_opened"])
        self.assertEqual(len(self.invocations()), 1)

        cached = self.run_one("read", task_id="B")
        self.assertFalse(cached["prompt_opened"])
        self.assertEqual(len(self.invocations()), 1)

        time.sleep(1.2)
        after = self.run_one("read", task_id="C")
        self.assertTrue(after["prompt_opened"], "an expired result must not be replayed")
        self.assertEqual(len(self.invocations()), 2, "exactly one re-invocation, not a storm")

    def test_gc_emits_an_expired_event(self):
        self.registry["entries"][0]["ttl_seconds"] = 1
        self.run_one("read", task_id="A")
        time.sleep(1.2)
        out = pb.gc(self.root, self.config)
        self.assertEqual(out["count"], 1)
        self.assertIn("expired", [e.get("event") for e in self.ledger_events()])


class TestCrashedOwner(BrokerCase):
    def _plant_dead_owner(self, req: dict, *, heartbeat_age: float = 999.0) -> Path:
        keydir = pb._keydir(self.root, req["key"], req["mutating"], req["request_id"])
        keydir.mkdir(parents=True, exist_ok=True)
        pb._write_json(keydir / "owner.json", {
            "pid": 99999999,  # never a live pid on macOS
            "request_id": "ghost", "task_id": "GHOST",
            "started_at": time.time() - heartbeat_age,
            "heartbeat": time.time() - heartbeat_age,
            "attempt": 1,
        })
        return keydir

    def test_a_crashed_owner_does_not_strand_waiters(self):
        pb.ensure_root(self.root)
        self.config["lease_seconds"] = 0.2
        req = self.req("read", task_id="A")
        self._plant_dead_owner(req)
        out = pb.execute(req, timeout=10, root=self.root, config=self.config, quiet=True)
        self.assertEqual(out["state"], "completed", "the waiter must take over, not hang")
        self.assertEqual(len(self.invocations()), 1, "takeover invokes exactly once")
        self.assertIn("owner_crashed", [e.get("event") for e in self.ledger_events()])

    def test_a_crash_loop_cannot_reopen_prompts_repeatedly(self):
        pb.ensure_root(self.root)
        self.config["lease_seconds"] = 0.2
        self.config["max_prompt_attempts"] = 2
        req = self.req("read", task_id="A")
        keydir = pb._keydir(self.root, req["key"], req["mutating"], req["request_id"])
        keydir.mkdir(parents=True, exist_ok=True)
        (keydir / "attempts").write_text("2", encoding="utf-8")  # cap already reached

        out = pb.execute(req, timeout=10, root=self.root, config=self.config, quiet=True)
        self.assertEqual(out["state"], "denied_exhausted")
        self.assertFalse(out["prompt_opened"], "the cap must prevent another dialog")
        self.assertEqual(len(self.invocations()), 0, "no invocation past the attempt cap")

        # And the terminal state is shared, so later waiters are answered too.
        again = self.run_one("read", task_id="B")
        self.assertEqual(again["state"], "denied_exhausted")
        self.assertEqual(len(self.invocations()), 0)


class TestWindowRoll(BrokerCase):
    def test_the_attempt_cap_is_a_rate_limit_not_a_permanent_lockout(self):
        """Regression: the attempt counter outlived its TTL window.

        A key that once hit the prompt cap kept its counter after the cached
        result expired, so every later request re-read the cap and returned
        denied_exhausted forever — a rate limit silently became a lockout.
        """
        self.registry["entries"][0]["ttl_seconds"] = 1
        self.registry["entries"][0]["negative_ttl_seconds"] = 1
        self.config["max_prompt_attempts"] = 1

        first = self.run_one("read", task_id="A")
        self.assertEqual(first["state"], "completed")
        self.assertEqual(len(self.invocations()), 1)

        time.sleep(1.2)  # the window rolls
        after = self.run_one("read", task_id="B")
        self.assertEqual(after["state"], "completed",
                         "an expired key must be usable again, not locked out")
        self.assertEqual(len(self.invocations()), 2)
        self.assertIn("expired", [e.get("event") for e in self.ledger_events()])

    def test_a_late_waiter_reads_the_fresh_result_instead_of_reinvoking(self):
        """Regression: a waiter that polled across the owner's finish re-invoked.

        Reading result.json an instant BEFORE the owner wrote it and owner.json
        an instant AFTER the owner removed it made the waiter leave the wait loop
        and contest the lease, producing a second invocation — and so a second
        dialog — from a pure race.
        """
        pb.ensure_root(self.root)
        req = self.req("read", task_id="LATE")
        keydir = pb._keydir(self.root, req["key"], False, req["request_id"])
        keydir.mkdir(parents=True, exist_ok=True)
        # Exactly the post-race state: a finished owner's result, no owner file.
        pb._write_json(keydir / "result.json", {
            "schema": pb.SCHEMA_RESULT, "key": req["key"], "state": "completed",
            "exit_code": 0, "stdout_path": None, "stdout_bytes": 0, "stderr": "",
            "owner_request_id": "prior", "owner_task_id": "OWNER",
            "created_at": time.time(), "expires_at": time.time() + 600,
            "cacheable": True, "duration_seconds": 0.1, "attempt": 1,
        })
        out = pb.execute(req, timeout=10, root=self.root, config=self.config, quiet=True)
        self.assertEqual(out["state"], "completed")
        self.assertTrue(out["coalesced"])
        self.assertEqual(len(self.invocations()), 0, "the fresh result must be reused")


class TestForget(BrokerCase):
    def test_forget_clears_a_cached_denial_so_the_user_can_retry_deliberately(self):
        os.environ["FAKE_EXIT"] = "1"
        os.environ["FAKE_STDERR"] = "Operation not permitted"
        self.run_one("read", task_id="A")
        self.assertEqual(len(self.invocations()), 1)
        self.assertFalse(self.run_one("read", task_id="B")["prompt_opened"])

        req = self.req("read", task_id="C")
        out = pb.forget(self.root, req["key"], self.config)
        self.assertEqual(out["forgotten"], 1)

        os.environ["FAKE_EXIT"] = "0"
        after = self.run_one("read", task_id="C")
        self.assertTrue(after["prompt_opened"], "forget must allow a fresh request")
        self.assertEqual(after["state"], "completed")
        self.assertEqual(len(self.invocations()), 2)
        self.assertIn("forgotten", [e.get("event") for e in self.ledger_events()])

    def test_forget_never_grants_anything(self):
        """Forgetting removes an answer; it can only ever cause a prompt."""
        os.environ["FAKE_DELAY"] = "0"
        self.run_one("read", task_id="A")
        req = self.req("read", task_id="B")
        pb.forget(self.root, req["key"], self.config)
        self.assertIsNone(pb._read_result(pb._keydir(self.root, req["key"], False, "x")),
                          "no result may survive a forget")

    def test_forget_leaves_an_in_flight_request_alone(self):
        os.environ["FAKE_DELAY"] = "3"
        req = self.req("read", task_id="A")
        thread = threading.Thread(
            target=lambda: pb.execute(req, timeout=20, root=self.root,
                                      config=self.config, quiet=True))
        thread.start()
        time.sleep(0.8)
        out = pb.forget(self.root, req["key"], self.config)
        thread.join(30)
        self.assertEqual(out["forgotten"], 0)
        self.assertEqual(out["skipped_in_flight"], [req["key"]],
                         "forgetting a running request would strand its waiters")


class TestCancellation(BrokerCase):
    def test_cancel_reaches_every_waiter(self):
        os.environ["FAKE_DELAY"] = "5"
        reqs = [self.req("read", task_id=f"T{i}") for i in range(3)]
        results: list[dict | None] = [None] * len(reqs)

        def worker(i, r):
            results[i] = pb.execute(r, timeout=15, root=self.root, config=self.config, quiet=True)

        threads = [threading.Thread(target=worker, args=(i, r)) for i, r in enumerate(reqs)]
        for t in threads:
            t.start()
        time.sleep(0.8)  # let one become owner and the rest register as waiters
        out = pb.cancel(self.root, reqs[0]["key"], "operator cancelled", self.config)
        self.assertGreaterEqual(out["cancelled"], 1)
        for t in threads:
            t.join(20)

        waiter_states = [r["state"] for r in results if r and r["coalesced"]]
        self.assertTrue(waiter_states, "expected at least one waiter")
        self.assertTrue(all(s == "cancelled" for s in waiter_states),
                        f"cancellation must reach every waiter, got {waiter_states}")

    def test_cancellation_is_remembered_so_it_does_not_retry(self):
        pb.ensure_root(self.root)
        req = self.req("read", task_id="A")
        keydir = pb._keydir(self.root, req["key"], False, req["request_id"])
        keydir.mkdir(parents=True, exist_ok=True)
        pb.cancel(self.root, req["key"], "operator cancelled", self.config)
        out = pb.execute(req, timeout=5, root=self.root, config=self.config, quiet=True)
        self.assertEqual(out["state"], "cancelled")
        self.assertEqual(len(self.invocations()), 0)


# ==========================================================================
# Acceptance 4 — the password is never touched
# ==========================================================================
class TestPasswordSafety(BrokerCase):
    def test_sudo_stdin_password_shape_is_refused(self):
        for flag in ("-S", "--stdin", "-A", "--askpass"):
            reason = pb._reject_password_capture(["/usr/bin/sudo", flag, "ls"])
            self.assertIsNotNone(reason, f"sudo {flag} must be refused")

    def test_sudo_askpass_env_is_refused(self):
        os.environ["SUDO_ASKPASS"] = "/tmp/askpass.sh"
        try:
            self.assertIsNotNone(pb._reject_password_capture(["/usr/bin/sudo", "ls"]))
        finally:
            os.environ.pop("SUDO_ASKPASS", None)

    def test_password_argument_is_refused(self):
        self.assertIsNotNone(pb._reject_password_capture(["/usr/bin/foo", "--password=hunter2"]))

    def test_refusal_happens_before_any_invocation(self):
        os.environ["SUDO_ASKPASS"] = "/tmp/askpass.sh"
        try:
            out = self.run_one("read", task_id="A")
            self.assertEqual(out["state"], "refused")
            self.assertEqual(len(self.invocations()), 0)
        finally:
            os.environ.pop("SUDO_ASKPASS", None)

    def test_no_password_material_is_ever_written_to_the_store(self):
        os.environ["FAKE_STDOUT"] = "harmless output"
        self.run_one("read", task_id="A")
        blob = ""
        for path in self.root.rglob("*"):
            if path.is_file():
                blob += path.read_bytes().decode("utf-8", "replace")
        for forbidden in ("password", "passwd", "SUDO_ASKPASS", "askpass"):
            self.assertNotIn(forbidden, blob,
                             f"the store must never contain {forbidden!r}")

    def test_child_stdin_is_never_a_pipe(self):
        """A piped stdin is how a password would enter this process."""
        src = SCRIPT.read_text(encoding="utf-8")
        run_block = src.split("def _run_privileged", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("stdin=None", run_block)
        self.assertNotIn("stdin=subprocess.PIPE", run_block)


class TestRequestValidation(BrokerCase):
    def test_purpose_is_mandatory(self):
        req = self.req("read", purpose="   ")
        out = pb.execute(req, root=self.root, config=self.config, quiet=True)
        self.assertEqual(out["state"], "refused")
        self.assertEqual(out["reason"], "purpose_required")
        self.assertEqual(len(self.invocations()), 0)

    def test_request_record_carries_every_required_field(self):
        req = self.req("read", task_id="T9", thread_id="th-1", repo="/r", worktree="/r/wt",
                       branch="main", initiating_app="Codex")
        for field in ("task_id", "thread_id", "repo", "worktree", "executable", "argv",
                      "purpose", "scope", "trust_domain", "initiating_app", "timestamp",
                      "mutating", "entry_id", "risk_class", "key"):
            self.assertIn(field, req)
            self.assertIsNotNone(req[field], f"{field} must be populated")

    def test_attribution_block_names_app_task_repo_command_and_reason(self):
        req = self.req("read", task_id="T9", repo="/repo", branch="main",
                       initiating_app="Codex", purpose="enumerate background items")
        block = pb.attribution_block(req, waiters=2, ttl=900)
        for needle in ("Codex", "T9", "/repo", "main", "faketool", "enumerate background items",
                       "fake:read", "read-only"):
            self.assertIn(needle, block)


# ==========================================================================
# Acceptance 1 regression — the two observed shell strings are ONE request
# ==========================================================================
class TestObservedIncident(unittest.TestCase):
    OBSERVED_A = "sfltool dumpbtm 2>/dev/null | rg -n -C 2 'bash|env|python|RossLabs' | sed -n '1,260p'"
    OBSERVED_B = "set -o pipefail\nsfltool dumpbtm | sed -n '1,120p'\nrc=$?\necho \"sfltool_rc=$rc\""
    OBSERVED_C = "sfltool dumpbtm"

    def test_the_three_observed_shell_strings_yield_one_privileged_argv(self):
        def priv(cmd: str) -> list[list[str]]:
            return [s for s in pb.split_segments(cmd) if Path(s[0]).name == "sfltool"]

        for cmd in (self.OBSERVED_A, self.OBSERVED_B, self.OBSERVED_C):
            segs = priv(cmd)
            self.assertEqual(segs, [["sfltool", "dumpbtm"]],
                             f"failed to extract the privileged argv from: {cmd!r}")

    def test_the_three_observed_strings_share_one_coalescing_key(self):
        registry = pb.load_registry()
        keys = set()
        for cmd in (self.OBSERVED_A, self.OBSERVED_B, self.OBSERVED_C):
            found = pb.classify_command(cmd, registry)
            self.assertEqual(len(found), 1, f"expected one privileged segment in {cmd!r}")
            c = found[0]
            keys.add(pb.request_key(c["argv"], c["scope"], c["trust_domain"], c["mutating"],
                                    c["entry_id"], registry["version"]))
        self.assertEqual(len(keys), 1, "the three observed invocations must coalesce to one key")

    def test_redirections_and_env_prefixes_do_not_change_identity(self):
        base = pb.split_segments("sfltool dumpbtm")
        for variant in ("FOO=bar sfltool dumpbtm", "sfltool dumpbtm 2>&1", "sfltool dumpbtm >/tmp/x"):
            self.assertEqual(pb.split_segments(variant)[0], base[0], variant)

    def test_the_shipped_registry_classifies_the_incident_command(self):
        found = pb.classify_command("sfltool dumpbtm")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["entry_id"], "sfltool-dumpbtm")
        self.assertFalse(found[0]["mutating"])
        self.assertEqual(found[0]["risk_class"], "read_only")
        self.assertGreater(found[0]["ttl_seconds"], 0)

    def test_unprivileged_registry_entries_are_excluded(self):
        for cmd in ("csrutil status", "nvram -p", "spctl --status", "fdesetup status"):
            self.assertEqual(pb.classify_command(cmd), [], f"{cmd} must not be treated as privileged")

    def test_privileged_variants_of_the_same_tool_are_still_caught(self):
        for cmd, entry_id in (("csrutil disable", "csrutil-mutate"),
                              ("nvram boot-args=x", "nvram-write"),
                              ("spctl --master-disable", "spctl-mutate"),
                              ("fdesetup disable", "fdesetup-mutate")):
            found = pb.classify_command(cmd)
            self.assertEqual([f["entry_id"] for f in found], [entry_id], cmd)


# ==========================================================================
# Acceptance 5 — durable + live visibility; Ambient never decides
# ==========================================================================
class TestLedgerAndAmbient(BrokerCase):
    def test_every_state_transition_is_recorded_with_task_ids(self):
        os.environ["FAKE_DELAY"] = "0.4"
        self.run_concurrent([self.req("read", task_id="A"), self.req("read", task_id="B")])
        events = self.ledger_events()
        names = [e["event"] for e in events]
        for expected in ("requested", "prompted", "approved", "completed", "coalesced"):
            self.assertIn(expected, names, f"missing {expected} in {names}")
        for e in events:
            if e["event"] in ("requested", "prompted", "coalesced", "completed", "approved"):
                self.assertIn("initiating_task_id", e)
                self.assertIn("waiter_task_ids", e)

    def test_ledger_hash_chain_verifies(self):
        self.run_one("read", task_id="A")
        out = pb.verify_ledger(self.root)
        self.assertTrue(out["ok"], out)
        self.assertGreater(out["records"], 0)

    def test_a_deleted_ledger_line_is_detected(self):
        self.run_one("read", task_id="A")
        path = self.root / "ledger.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(lines), 2)
        path.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")
        out = pb.verify_ledger(self.root)
        self.assertFalse(out["ok"], "a removed record must break the chain")

    def test_an_edited_ledger_line_is_detected(self):
        self.run_one("read", task_id="A")
        path = self.root / "ledger.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[0])
        rec["purpose"] = "something else"
        lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertFalse(pb.verify_ledger(self.root)["ok"])

    def test_ambient_cannot_approve_deny_or_terminate(self):
        """A hostile Ambient sink must not change any verdict."""
        hostile = self.tmp / "hostile_ambient.py"
        hostile.write_text(
            "#!/usr/bin/env python3\nimport sys\n"
            "sys.stdin.read()\nprint('DENY')\nsys.exit(3)\n",
            encoding="utf-8",
        )
        hostile.chmod(0o755)
        self.config["ambient"] = {"mode": "live", "notify_command": [sys.executable, str(hostile)],
                                  "notify_timeout_seconds": 3}
        out = self.run_one("read", task_id="A")
        self.assertEqual(out["state"], "completed", "an Ambient failure must not change the verdict")
        self.assertEqual(len(self.invocations()), 1)
        reasons = {g["reason"] for g in self.gaps()}
        self.assertIn("ambient_sink_unreachable", reasons)

    def test_ambient_ledger_only_mode_records_a_single_coverage_gap(self):
        self.config["ambient"] = {"mode": "ledger-only", "notify_command": None}
        self.run_one("read", task_id="A")
        gaps = [g for g in self.gaps() if g["reason"] == "ambient_live_unconfigured"]
        self.assertEqual(len(gaps), 1, "the unconfigured-live gap is reported once, not per event")
        self.assertTrue(gaps[0]["unattributed_possible"])


# ==========================================================================
# Acceptance 6 — risk-class behaviour when the coordinator is unavailable
# ==========================================================================
class TestCoverageGaps(BrokerCase):
    def _unusable_root(self) -> Path:
        parent = self.tmp / "locked"
        parent.mkdir()
        os.chmod(parent, 0o500)
        self._locked_dirs = [*getattr(self, "_locked_dirs", []), parent]
        return parent / "store"

    @unittest.skipIf(os.getuid() == 0, "root bypasses directory permissions")
    def test_readonly_proceeds_uncoalesced_and_emits_a_gap_receipt(self):
        root = self._unusable_root()
        out = pb.execute(self.req("read", task_id="A"), root=root, config=self.config, quiet=True)
        self.assertEqual(out["state"], "completed")
        self.assertFalse(out["recorded"], "an unrecorded run must say so")
        gap = out["coverage_gap"]
        self.assertEqual(gap["reason"], "broker_root_unusable")
        self.assertEqual(gap["risk_class"], "read_only")
        self.assertTrue(gap["unattributed_possible"],
                        "unavailability is never proof that no request occurred")

    @unittest.skipIf(os.getuid() == 0, "root bypasses directory permissions")
    def test_mutating_refuses_when_the_coordinator_is_unavailable(self):
        root = self._unusable_root()
        out = pb.execute(self.req("write", task_id="A"), root=root, config=self.config, quiet=True)
        self.assertEqual(out["state"], "refused")
        self.assertEqual(out["reason"], "broker_unavailable")
        self.assertEqual(len(self.invocations()), 0,
                         "a privileged mutation must never run with no record")
        self.assertTrue(out["coverage_gap"]["unattributed_possible"])

    def test_a_dropped_ledger_event_leaves_a_receipt(self):
        """A lost event must not make the ledger look complete."""
        pb.ensure_root(self.root)
        lock = self.root / "ledger.lock"
        lock.write_text("held", encoding="utf-8")
        try:
            out = pb.append_event(self.root, {"event": "requested", "key": "k1"}, self.config)
        finally:
            lock.unlink(missing_ok=True)
        self.assertIsNone(out, "the append must fail rather than corrupt the chain")
        reasons = {g["reason"] for g in self.gaps()}
        self.assertIn("ledger_lock_unavailable", reasons)

    def test_gap_receipt_is_machine_readable(self):
        gap = pb.coverage_gap(self.root, reason="test", risk_class="read_only",
                              behavior="proceed_uncoalesced", detail="d")
        for field in ("schema", "timestamp", "reason", "risk_class", "behavior",
                      "unattributed_possible", "pid"):
            self.assertIn(field, gap)
        self.assertEqual(gap["schema"], pb.SCHEMA_GAP)
        persisted = self.gaps()
        self.assertEqual(persisted[-1]["reason"], "test", "the receipt must be on disk, not just returned")

    def test_concurrent_ensure_root_never_reports_a_healthy_store_as_unusable(self):
        """Regression: a shared writability probe raced and faked an outage.

        Three concurrent callers both created and removed the same probe file;
        the loser's unlink raised, ensure_root returned unusable, and the request
        silently ran UNCOALESCED — reintroducing the duplicate prompt this module
        exists to remove. The probe is now unique per caller.
        """
        verdicts: list[tuple[bool, str | None]] = []
        lock = threading.Lock()

        def worker() -> None:
            out = pb.ensure_root(self.root)
            with lock:
                verdicts.append(out)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(len(verdicts), 12)
        self.assertTrue(all(v[0] for v in verdicts),
                        f"every concurrent caller must see a usable store, got {verdicts}")
        leftover = [p.name for p in self.root.glob(".writable*")]
        self.assertEqual(leftover, [], "probe files must not accumulate")


# ==========================================================================
# CLI surface
# ==========================================================================
class TestCLI(BrokerCase):
    def cli(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, BUILD_LOOP_PRIVILEGED_ROOT=str(self.root))
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, env=env)

    def test_classify_reports_the_incident_command(self):
        out = self.cli("classify", "--command", "sfltool dumpbtm | head", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        payload = json.loads(out.stdout)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["privileged_segments"][0]["entry_id"], "sfltool-dumpbtm")

    def test_classify_exits_nonzero_when_nothing_is_privileged(self):
        out = self.cli("classify", "--command", "ls -la", "--json")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(json.loads(out.stdout)["count"], 0)

    def test_request_without_purpose_is_rejected_by_argparse(self):
        out = self.cli("request", "--task-id", "T", "--argv", "ls")
        self.assertNotEqual(out.returncode, 0)

    def test_verify_ledger_and_status_run_clean_on_an_empty_store(self):
        self.assertEqual(self.cli("verify-ledger", "--json").returncode, 0)
        out = self.cli("status", "--json")
        self.assertEqual(out.returncode, 0)
        self.assertIn("in_flight", json.loads(out.stdout))

    def test_registry_ships_valid_and_loadable(self):
        registry = pb.load_registry()
        self.assertTrue(registry["entries"])
        ids = [e["id"] for e in registry["entries"]]
        self.assertEqual(len(ids), len(set(ids)), "registry ids must be unique")
        for entry in registry["entries"]:
            for field in ("id", "executable", "argv_prefix", "scope", "confidence"):
                self.assertIn(field, entry, entry.get("id"))
            self.assertIn(entry["confidence"], ("observed", "documented", "inferred"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
