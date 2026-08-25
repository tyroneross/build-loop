#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Subprocess-level tests for scripts/hooks/pre_bash_consent.sh. Zero deps.

WARN-ONLY rollout of the CLI dispatch consent gate (contract:
references/cli-dispatch-consent-contract.md). Drives the real hook script
directly (not through pre_bash_dispatch.sh) in a throwaway git repo, using
AGENT_CONSENT_SELFTEST + AGENT_CONSENT_STORE_PATH to point
cli_dispatch_consent.py at a per-test store — the real
~/.agent-consent/cli-dispatch-consent.json is never read or written by this
suite (see cli_dispatch_consent.py store_path(): the env override is honored
ONLY inside a test process).

Coverage:
  - vendor invocation detection: codex exec / claude -p / ollama run /
    cursor-agent fire; `grep codex f.txt` and `echo "use codex later"`
    (mention, not invocation) do NOT fire
  - allowed key (mode=auto) emits `{}` (silent pass)
  - must-ask key (no record) emits "ask", NEVER "deny"
  - denied key (mode=denied) still emits "ask", NEVER "deny" (WARN-ONLY)
  - a would-block dispatch appends one line to
    <repo>/.build-loop/consent-warn-count.jsonl
  - the hook exits 0 in every case above
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
HOOK = HERE / "pre_bash_consent.sh"
PLUGIN_ROOT = HERE.parent.parent
CONSENT_MODULE = PLUGIN_ROOT / "scripts" / "cli_dispatch_consent.py"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def make_buildloop_repo(tmp: Path, name: str = "repo") -> Path:
    repo = tmp / name
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "seed")
    (repo / ".build-loop").mkdir(parents=True, exist_ok=True)
    (repo / ".build-loop" / "config.json").write_text("{}", encoding="utf-8")
    return repo


def run_hook(
    repo: Path,
    command: str,
    *,
    consent_store: Path,
) -> subprocess.CompletedProcess:
    """Drive pre_bash_consent.sh exactly as the dispatcher's _run_gate would:
    the raw PreToolUse event JSON on stdin. AGENT_CONSENT_SELFTEST +
    AGENT_CONSENT_STORE_PATH isolate cli_dispatch_consent.py's store to a
    per-test tmp file — see store_path()'s test-only override contract."""
    event = json.dumps({"tool_input": {"command": command}, "cwd": str(repo)})
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    env["AGENT_CONSENT_SELFTEST"] = "1"
    env["AGENT_CONSENT_STORE_PATH"] = str(consent_store)
    env.pop("BUILD_LOOP_HOOKS", None)
    return subprocess.run(
        ["bash", str(HOOK)],
        cwd=repo,
        input=event,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def record_consent(store: Path, vendor: str, mode: str) -> None:
    """Write one decision directly via the frozen module's own CLI, isolated
    to `store` through the same test-only env override the hook uses."""
    env = dict(os.environ)
    env["AGENT_CONSENT_SELFTEST"] = "1"
    env["AGENT_CONSENT_STORE_PATH"] = str(store)
    r = subprocess.run(
        [sys.executable, str(CONSENT_MODULE), "--product", "build-loop",
         "--vendor", vendor, "--set", mode],
        capture_output=True, text=True, env=env, check=False,
    )
    assert r.returncode == 0, f"record_consent setup failed: {r.stderr}"


class VendorInvocationDetectionTests(unittest.TestCase):
    """Match the INVOCATION, not a mention. No consent has been recorded in
    any of these tests, so a fire always yields 'ask' (must-ask, exit 1) and
    a non-fire always yields the silent '{}' pass — the two are
    distinguishable by the presence of hookSpecificOutput."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = make_buildloop_repo(self.tmp)
        self.store = self.tmp / "consent-store.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _fires(self, command: str) -> subprocess.CompletedProcess:
        r = run_hook(self.repo, command, consent_store=self.store)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        return r

    def test_codex_exec_fires(self) -> None:
        r = self._fires('codex exec "hi"')
        out = json.loads(r.stdout)
        self.assertIn("hookSpecificOutput", out)
        self.assertEqual(
            out["hookSpecificOutput"]["permissionDecision"], "ask"
        )

    def test_claude_dash_p_fires(self) -> None:
        r = self._fires('claude -p "hi"')
        out = json.loads(r.stdout)
        self.assertIn("hookSpecificOutput", out)

    def test_ollama_run_fires(self) -> None:
        r = self._fires("ollama run llama3")
        out = json.loads(r.stdout)
        self.assertIn("hookSpecificOutput", out)

    def test_cursor_agent_fires(self) -> None:
        r = self._fires("cursor-agent")
        out = json.loads(r.stdout)
        self.assertIn("hookSpecificOutput", out)
        # Vendor mapping: cursor-agent -> "cursor" (contract "Key granularity").
        self.assertIn("cursor", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_grep_mention_does_not_fire(self) -> None:
        r = self._fires("grep codex f.txt")
        self.assertEqual(r.stdout.strip(), "{}")

    def test_echo_mention_does_not_fire(self) -> None:
        r = self._fires('echo "use codex later"')
        self.assertEqual(r.stdout.strip(), "{}")

    def test_comment_mention_does_not_fire(self) -> None:
        r = self._fires("# codex")
        self.assertEqual(r.stdout.strip(), "{}")

    def test_compound_command_leading_segment_fires(self) -> None:
        """`cd x && codex exec` — the consent-relevant segment is not first,
        but it is still the LEADING token of its own segment after splitting
        on [;|&], so it must fire."""
        r = self._fires('cd /tmp && codex exec "hi"')
        out = json.loads(r.stdout)
        self.assertIn("hookSpecificOutput", out)


class ConsentDecisionTests(unittest.TestCase):
    """WARN-ONLY: allowed passes silently; every non-allowed outcome
    (must-ask, denied, chain-broken) becomes 'ask', never 'deny'."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = make_buildloop_repo(self.tmp)
        self.store = self.tmp / "consent-store.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_allowed_key_emits_silent_pass(self) -> None:
        record_consent(self.store, "codex", "auto")
        r = run_hook(self.repo, 'codex exec "hi"', consent_store=self.store)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        self.assertEqual(r.stdout.strip(), "{}")

    def test_must_ask_key_emits_ask_never_deny(self) -> None:
        """No record for this key at all -> must-ask (exit 1)."""
        r = run_hook(self.repo, 'codex exec "hi"', consent_store=self.store)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        out = json.loads(r.stdout)
        decision = out["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(decision, "ask")
        self.assertNotEqual(decision, "deny")
        self.assertIn("must ask", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_denied_key_still_emits_ask_never_deny(self) -> None:
        """WARN-ONLY is unconditional: even a recorded 'denied' mode must
        surface as 'ask' with the real verdict named in the reason, not as
        a literal 'deny' — that is the entire point of this rollout phase."""
        record_consent(self.store, "codex", "denied")
        r = run_hook(self.repo, 'codex exec "hi"', consent_store=self.store)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        out = json.loads(r.stdout)
        decision = out["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(decision, "ask")
        self.assertNotEqual(decision, "deny")
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("denied", reason)

    def test_once_mode_key_emits_ask(self) -> None:
        """`once`/`ask` modes grant nothing forward (contract "Modes") ->
        must-ask (exit 1) every time, surfaced as 'ask'."""
        record_consent(self.store, "claude", "once")
        r = run_hook(self.repo, 'claude -p "hi"', consent_store=self.store)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")


class WarnCounterTests(unittest.TestCase):
    """The warn-count evidence file is the record the later decision to arm
    this gate depends on (contract 'Rollout')."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = make_buildloop_repo(self.tmp)
        self.store = self.tmp / "consent-store.json"
        self.warn_log = self.repo / ".build-loop" / "consent-warn-count.jsonl"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_would_block_appends_one_line(self) -> None:
        self.assertFalse(self.warn_log.exists())
        r = run_hook(self.repo, 'codex exec "hi"', consent_store=self.store)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        self.assertTrue(self.warn_log.exists())
        lines = self.warn_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["vendor"], "codex")
        self.assertIn("timestamp", entry)
        self.assertIn("would_be_exit", entry)
        self.assertEqual(entry["would_be_exit"], 1)

    def test_allowed_dispatch_does_not_append(self) -> None:
        record_consent(self.store, "ollama", "auto")
        r = run_hook(self.repo, "ollama run llama3", consent_store=self.store)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        self.assertFalse(self.warn_log.exists())

    def test_no_vendor_invocation_does_not_append(self) -> None:
        r = run_hook(self.repo, "ls -la", consent_store=self.store)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        self.assertFalse(self.warn_log.exists())


class FailOpenTests(unittest.TestCase):
    """The hook must exit 0 regardless of environment breakage."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = make_buildloop_repo(self.tmp)
        self.store = self.tmp / "consent-store.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_kill_switch_short_circuits(self) -> None:
        env = dict(os.environ)
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        env["BUILD_LOOP_HOOKS"] = "off"
        event = json.dumps(
            {"tool_input": {"command": 'codex exec "hi"'}, "cwd": str(self.repo)}
        )
        r = subprocess.run(
            ["bash", str(HOOK)],
            cwd=self.repo,
            input=event,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        self.assertEqual(r.stdout.strip(), "{}")

    def test_missing_consent_module_fails_open(self) -> None:
        fake_root = self.tmp / "fake_plugin_root"
        (fake_root / "scripts" / "hooks").mkdir(parents=True, exist_ok=True)
        # cli_dispatch_consent.py deliberately absent.
        event = json.dumps(
            {"tool_input": {"command": 'codex exec "hi"'}, "cwd": str(self.repo)}
        )
        env = dict(os.environ)
        env["CLAUDE_PLUGIN_ROOT"] = str(fake_root)
        env["AGENT_CONSENT_SELFTEST"] = "1"
        env["AGENT_CONSENT_STORE_PATH"] = str(self.store)
        r = subprocess.run(
            ["bash", str(HOOK)],
            cwd=self.repo,
            input=event,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        self.assertEqual(r.stdout.strip(), "{}")

    def test_empty_command_passes_silently(self) -> None:
        event = json.dumps({"tool_input": {"command": ""}, "cwd": str(self.repo)})
        env = dict(os.environ)
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        env["AGENT_CONSENT_SELFTEST"] = "1"
        env["AGENT_CONSENT_STORE_PATH"] = str(self.store)
        r = subprocess.run(
            ["bash", str(HOOK)],
            cwd=self.repo,
            input=event,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        self.assertEqual(r.stdout.strip(), "{}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
