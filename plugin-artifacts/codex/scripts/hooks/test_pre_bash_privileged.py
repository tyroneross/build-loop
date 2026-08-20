#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for pre_bash_privileged.py. Zero deps. Run: python3 test_pre_bash_privileged.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "pre_bash_privileged.py"
PLUGIN_ROOT = HERE.parent.parent


class HookCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="privhook-"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_hook(self, command: str, *, cwd: str = "/repo", env: dict | None = None,
                 plugin_root: Path | None = None) -> dict:
        event = {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": command}}
        full_env = dict(os.environ)
        full_env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root or PLUGIN_ROOT)
        full_env["BUILD_LOOP_PRIVILEGED_ROOT"] = str(self.tmp / "store")
        full_env.pop("BUILD_LOOP_HOOKS", None)
        full_env.pop("BUILD_LOOP_PRIVILEGED_HOOK", None)
        full_env.update(env or {})
        proc = subprocess.run(
            [sys.executable, str(HOOK)], input=json.dumps(event),
            capture_output=True, text=True, env=full_env, timeout=60,
        )
        self.assertEqual(proc.returncode, 0,
                         f"a hook must always exit 0; stderr={proc.stderr}")
        return json.loads(proc.stdout)

    def decision(self, payload: dict) -> str | None:
        return (payload.get("hookSpecificOutput") or {}).get("permissionDecision")

    def reason(self, payload: dict) -> str:
        return (payload.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")


class TestPassThrough(HookCase):
    def test_ordinary_commands_pass_through_silently(self):
        for command in ("ls -la", "git status", "python3 -m pytest", "rg sfltool docs/"):
            self.assertEqual(self.run_hook(command), {}, command)

    def test_unprivileged_variants_pass_through(self):
        for command in ("csrutil status", "nvram -p", "spctl -a -vv -t exec /Applications/X.app"):
            self.assertEqual(self.run_hook(command), {}, command)

    def test_empty_command_passes_through(self):
        self.assertEqual(self.run_hook(""), {})

    def test_kill_switch_disables_the_hook(self):
        for var in ("BUILD_LOOP_HOOKS", "BUILD_LOOP_PRIVILEGED_HOOK"):
            self.assertEqual(self.run_hook("sfltool dumpbtm", env={var: "off"}), {}, var)


class TestRedirect(HookCase):
    def test_the_incident_command_is_redirected_to_the_broker(self):
        payload = self.run_hook("sfltool dumpbtm 2>/dev/null | rg -n btm")
        self.assertEqual(self.decision(payload), "deny")
        reason = self.reason(payload)
        self.assertIn("privileged_broker.py", reason)
        self.assertIn("--purpose", reason)
        self.assertIn("sfltool dumpbtm", reason)
        self.assertIn("btm:read", reason)
        self.assertIn("read-only", reason)

    def test_the_retry_form_of_the_incident_is_also_redirected(self):
        payload = self.run_hook("set -o pipefail\nsfltool dumpbtm | sed -n '1,120p'\nrc=$?")
        self.assertEqual(self.decision(payload), "deny")
        self.assertIn("sfltool dumpbtm", self.reason(payload))

    def test_a_mutating_command_is_labelled_as_state_changing(self):
        payload = self.run_hook("csrutil disable")
        self.assertEqual(self.decision(payload), "deny")
        self.assertIn("MUTATING", self.reason(payload))

    def test_the_redirect_names_the_repository(self):
        payload = self.run_hook("sfltool dumpbtm", cwd="/Users/x/myrepo")
        self.assertIn("/Users/x/myrepo", self.reason(payload))

    def test_multiple_privileged_segments_are_all_reported(self):
        payload = self.run_hook("sfltool dumpbtm && csrutil disable")
        reason = self.reason(payload)
        self.assertIn("Other privileged segments", reason)
        self.assertIn("csrutil", reason)

    def test_an_already_brokered_command_is_allowed(self):
        command = ("python3 /p/scripts/privileged_broker.py request --purpose x "
                   "--task-id T --argv sfltool dumpbtm")
        payload = self.run_hook(command)
        self.assertEqual(self.decision(payload), "allow")
        self.assertIn("already routed", self.reason(payload))


class TestDegradation(HookCase):
    def test_a_missing_broker_stays_out_of_the_way(self):
        empty = self.tmp / "no-plugin"
        (empty / "scripts").mkdir(parents=True)
        self.assertEqual(self.run_hook("sfltool dumpbtm", plugin_root=empty), {},
                         "a hook with nothing to route to must not block work")

    def test_a_broken_classifier_allows_and_records_a_coverage_gap(self):
        broken_root = self.tmp / "broken-plugin"
        (broken_root / "scripts").mkdir(parents=True)
        (broken_root / "scripts" / "privileged_broker.py").write_text(
            "import sys\nsys.exit(3)\n", encoding="utf-8")
        payload = self.run_hook("sfltool dumpbtm", plugin_root=broken_root)
        self.assertEqual(self.decision(payload), "allow")
        self.assertIn("UNATTRIBUTED", self.reason(payload))
        gaps = (self.tmp / "store" / "gaps.jsonl")
        self.assertTrue(gaps.exists(), "a blind spot must leave a receipt")
        record = json.loads(gaps.read_text(encoding="utf-8").splitlines()[0])
        self.assertTrue(record["unattributed_possible"])
        self.assertEqual(record["reason"], "classifier_unavailable")

    def test_malformed_stdin_does_not_break_the_tool_call(self):
        proc = subprocess.run([sys.executable, str(HOOK)], input="not json",
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
