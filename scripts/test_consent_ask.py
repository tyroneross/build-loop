#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for consent_ask.py. Zero deps. Run: python3 scripts/test_consent_ask.py

Every test runs against a throwaway store via AGENT_CONSENT_SELFTEST=1 +
AGENT_CONSENT_STORE_PATH=<tmpfile> — the real `~/.build-loop/cli-dispatch-
consent.json` must never be touched by this suite.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "consent_ask.py"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import cli_dispatch_consent  # noqa: E402
import consent_ask  # noqa: E402


def _selftest_env(store_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["AGENT_CONSENT_SELFTEST"] = "1"
    env["AGENT_CONSENT_STORE_PATH"] = str(store_path)
    # Never inherit a real dispatch-depth value from the outer session.
    env.pop("AGENT_DISPATCH_DEPTH", None)
    return env


def run_cli(store_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=_selftest_env(store_path),
    )


class ConsentAskTestCase(unittest.TestCase):
    """Base fixture: an isolated, empty consent store per test.

    `consent_ask.ask_plan`/`resolve` take no `path` override (by spec) — they
    reach the store only through `cli_dispatch_consent.check()`, which in turn
    resolves the path via `cli_dispatch_consent.store_path()`. That function
    reads the REAL `os.environ` directly (not any `env=` dict passed around in
    this process), so the only way to redirect in-process calls away from the
    real `~/.agent-consent/cli-dispatch-consent.json` is to patch `os.environ`
    itself for the duration of the test — a plain `env={...}` kwarg does not
    reach it. Subprocess (CLI) tests get the same guarantee by copying this
    same patched environment into the child process.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmp.name) / "cli-dispatch-consent.json"
        self._env_overrides = {
            "AGENT_CONSENT_SELFTEST": "1",
            "AGENT_CONSENT_STORE_PATH": str(self.store_path),
        }
        self._saved_env = {k: os.environ.get(k) for k in self._env_overrides}
        os.environ.update(self._env_overrides)
        # Verify the instrument before trusting it: confirm the override
        # actually takes effect before any test relies on it.
        assert cli_dispatch_consent.store_path() == self.store_path, (
            "test harness bug: cli_dispatch_consent.store_path() did not "
            "honor AGENT_CONSENT_SELFTEST + AGENT_CONSENT_STORE_PATH"
        )

    def tearDown(self) -> None:
        for key, prior in self._saved_env.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior
        self.tmp.cleanup()

    def _record(self, product: str, vendor: str, mode: str) -> None:
        cli_dispatch_consent.record(product, vendor, mode, path=self.store_path)


class FailClosedHostTests(ConsentAskTestCase):
    """Cursor headless and unknown hosts have no ask primitive."""

    def test_cursor_fails_closed(self) -> None:
        plan = consent_ask.ask_plan(
            "build-loop", "cursor", "agent -p 'do the thing'", host="cursor"
        )
        self.assertFalse(plan["can_ask"])
        self.assertEqual(plan["surface"], "none")
        self.assertIsNone(plan["envelope"])
        self.assertIn("fail-closed", plan["reason"])
        self.assertIn("Cursor", plan["reason"])

    def test_unknown_host_fails_closed(self) -> None:
        plan = consent_ask.ask_plan(
            "build-loop", "ollama", "ollama run llama3", host="some_future_host"
        )
        self.assertFalse(plan["can_ask"])
        self.assertEqual(plan["surface"], "none")
        self.assertIsNone(plan["envelope"])
        self.assertIn("fail-closed", plan["reason"])

    def test_gemini_and_opencode_also_fail_closed(self) -> None:
        for host in ("gemini", "opencode"):
            plan = consent_ask.ask_plan("build-loop", "codex", "codex exec x", host=host)
            self.assertFalse(plan["can_ask"], msg=host)
            self.assertEqual(plan["surface"], "none", msg=host)
            self.assertIsNone(plan["envelope"], msg=host)


class ClaudeCodeAskTests(ConsentAskTestCase):
    """claude_code produces a valid PreToolUse ask envelope."""

    def test_claude_code_produces_valid_ask_envelope(self) -> None:
        command = "codex exec --sandbox workspace-write 'implement thing'"
        plan = consent_ask.ask_plan(
            "build-loop", "codex", command, host="claude_code"
        )
        self.assertTrue(plan["can_ask"])
        self.assertEqual(plan["surface"], "AskUserQuestion")
        self.assertIsInstance(plan["envelope"], dict)

        hso = plan["envelope"]["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "ask")
        self.assertEqual(hso["permissionDecisionReason"], plan["request_text"])
        # request_text must be the UNMODIFIED output of cli_dispatch_consent's
        # own request_text() — this module authors no prompt copy of its own.
        expected_text = cli_dispatch_consent.request_text("build-loop", "codex", command)
        self.assertEqual(plan["request_text"], expected_text)
        self.assertIn(command, plan["request_text"])

    def test_envelope_is_json_serializable(self) -> None:
        plan = consent_ask.ask_plan("build-loop", "claude", "claude -p hi", host="claude_code")
        # Must round-trip cleanly — this is what a hook script would emit on stdout.
        json.dumps(plan["envelope"])


class CodexAskTests(ConsentAskTestCase):
    """codex reports its own approval surface, no envelope."""

    def test_codex_reports_approval_surface(self) -> None:
        plan = consent_ask.ask_plan("rally-point", "claude", "claude -p hi", host="codex")
        self.assertTrue(plan["can_ask"])
        self.assertEqual(plan["surface"], "codex_approval")
        self.assertIsNone(plan["envelope"])
        self.assertTrue(plan["request_text"])


class ResolveShortCircuitTests(ConsentAskTestCase):
    """resolve() checks first and only asks when check() says needs_prompt."""

    def test_auto_key_short_circuits_with_no_ask(self) -> None:
        self._record("build-loop", "codex", "auto")
        result = consent_ask.resolve(
            "build-loop", "codex", "codex exec x", host="claude_code", env={}
        )
        self.assertTrue(result["check"]["allowed"])
        self.assertEqual(result["check"]["mode"], "auto")
        self.assertFalse(result["check"]["needs_prompt"])
        self.assertIsNone(result["ask_plan"])

    def test_denied_key_does_not_produce_an_ask(self) -> None:
        self._record("build-loop", "codex", "denied")
        result = consent_ask.resolve(
            "build-loop", "codex", "codex exec x", host="claude_code", env={}
        )
        self.assertFalse(result["check"]["allowed"])
        self.assertEqual(result["check"]["mode"], "denied")
        self.assertFalse(result["check"]["needs_prompt"])
        self.assertIsNone(result["ask_plan"])

    def test_no_record_needs_prompt_and_builds_ask_plan(self) -> None:
        result = consent_ask.resolve(
            "build-loop", "codex", "codex exec x", host="claude_code", env={}
        )
        self.assertFalse(result["check"]["allowed"])
        self.assertTrue(result["check"]["needs_prompt"])
        self.assertIsNotNone(result["ask_plan"])
        self.assertTrue(result["ask_plan"]["can_ask"])

    def test_once_mode_still_needs_prompt(self) -> None:
        self._record("build-loop", "codex", "once")
        result = consent_ask.resolve(
            "build-loop", "codex", "codex exec x", host="claude_code", env={}
        )
        self.assertFalse(result["check"]["allowed"])
        self.assertTrue(result["check"]["needs_prompt"])
        self.assertIsNotNone(result["ask_plan"])

    def test_depth_exceeded_short_circuits_with_no_ask(self) -> None:
        result = consent_ask.resolve(
            "build-loop",
            "codex",
            "codex exec x",
            host="claude_code",
            env={"AGENT_DISPATCH_DEPTH": "5"},
        )
        self.assertFalse(result["check"]["allowed"])
        self.assertFalse(result["check"]["needs_prompt"])
        self.assertEqual(result["check"]["exit"], cli_dispatch_consent.EXIT_DENIED)
        self.assertIsNone(result["ask_plan"])


class NeverWritesStoreTests(ConsentAskTestCase):
    """The module has no path that records consent — never writes the store."""

    def test_resolve_never_writes_store(self) -> None:
        self._record("build-loop", "codex", "ask")
        before_bytes = self.store_path.read_bytes()
        before_mtime = self.store_path.stat().st_mtime_ns

        for host in ("claude_code", "codex", "cursor", "unknown_host"):
            consent_ask.resolve(
                "build-loop", "codex", "codex exec x", host=host, env={}
            )

        after_bytes = self.store_path.read_bytes()
        after_mtime = self.store_path.stat().st_mtime_ns
        self.assertEqual(before_bytes, after_bytes)
        self.assertEqual(before_mtime, after_mtime)

    def test_ask_plan_never_creates_store_file(self) -> None:
        # No record() call at all in this test — the store must not even come
        # into existence as a side effect of building an ask plan.
        self.assertFalse(self.store_path.exists())
        for host in ("claude_code", "codex", "cursor", "unknown_host"):
            consent_ask.ask_plan("build-loop", "codex", "codex exec x", host=host)
        self.assertFalse(self.store_path.exists())

    def test_module_source_has_no_recording_calls(self) -> None:
        """Static guarantee, not just a runtime observation: the module text
        contains no call to record( or note_kill_switch(."""
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("record(", source)
        self.assertNotIn("note_kill_switch(", source)


class CliTests(ConsentAskTestCase):
    """CLI surface: exit codes reuse cli_dispatch_consent's constants."""

    def test_cli_must_ask_exit_code(self) -> None:
        result = run_cli(
            self.store_path,
            "--product", "build-loop",
            "--vendor", "codex",
            "--command", "codex exec x",
            "--host", "claude_code",
            "--json",
        )
        self.assertEqual(result.returncode, cli_dispatch_consent.EXIT_MUST_ASK, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["check"]["needs_prompt"])
        self.assertIsNotNone(data["ask_plan"])

    def test_cli_allowed_exit_code(self) -> None:
        self._record("build-loop", "codex", "auto")
        result = run_cli(
            self.store_path,
            "--product", "build-loop",
            "--vendor", "codex",
            "--command", "codex exec x",
            "--host", "claude_code",
            "--json",
        )
        self.assertEqual(result.returncode, cli_dispatch_consent.EXIT_ALLOWED, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["check"]["allowed"])
        self.assertIsNone(data["ask_plan"])

    def test_cli_denied_exit_code(self) -> None:
        self._record("build-loop", "codex", "denied")
        result = run_cli(
            self.store_path,
            "--product", "build-loop",
            "--vendor", "codex",
            "--command", "codex exec x",
            "--host", "claude_code",
            "--json",
        )
        self.assertEqual(result.returncode, cli_dispatch_consent.EXIT_DENIED, msg=result.stderr)

    def test_cli_cursor_host_fails_closed(self) -> None:
        result = run_cli(
            self.store_path,
            "--product", "build-loop",
            "--vendor", "cursor",
            "--command", "agent -p x",
            "--host", "cursor",
            "--json",
        )
        # Still "must ask" from the consent gate's point of view (no record
        # exists), but the ask_plan itself must say can_ask=False.
        self.assertEqual(result.returncode, cli_dispatch_consent.EXIT_MUST_ASK, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertIsNotNone(data["ask_plan"])
        self.assertFalse(data["ask_plan"]["can_ask"])

    def test_cli_never_touches_real_store(self) -> None:
        """Belt-and-suspenders on the CLI path specifically: even though the
        subprocess inherits AGENT_CONSENT_SELFTEST + the redirected path,
        confirm the real per-operator store path is untouched by this run."""
        real_store = Path.home() / ".agent-consent" / "cli-dispatch-consent.json"
        before = real_store.read_bytes() if real_store.exists() else None

        run_cli(
            self.store_path,
            "--product", "build-loop",
            "--vendor", "codex",
            "--command", "codex exec x",
            "--host", "claude_code",
            "--json",
        )

        after = real_store.read_bytes() if real_store.exists() else None
        self.assertEqual(before, after, msg="the real per-operator store must never change")


if __name__ == "__main__":
    unittest.main(verbosity=2)
