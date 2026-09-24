#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fixtures are the two failure modes from the 2026-07-25 A/B test, and their fixes."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import unittest

import dispatch_prompt_lint as lint

PROMPT_B = (
    "Drive the browser and inspect the rendered page. "
    "Do not read the code to determine behavior."
)
PROMPT_C = (
    "You are a reviewer of the rendered UI. Capture screenshots of each screen. "
    "Do not review from HTML source."
)
PROMPT_B_FIXED = (
    "Drive the browser and inspect the rendered page. "
    "Do not read the code to determine behavior; if the browser step is "
    "blocked, stop and report BLOCKED with the error."
)
PROMPT_C_FIXED = (
    "You are a reviewer of the rendered UI. Capture screenshots of each screen. "
    "Do not review from HTML source; if screenshots fail, stop and report "
    "BLOCKED with the error."
)
PLAIN_IMPLEMENTATION = (
    "Implement a new function foo in bar.py that returns the sum of two "
    "integers. Add a docstring and a unit test."
)
UNGUARDED_URL = (
    "Fetch data from https://example.com/api and parse the JSON response "
    "into a list of records."
)
JUDGE_ROLE_ALONE = "You are a reviewer. Review the summary for tone and clarity."
PLAN_CRITIC_SCOPE_LIMITS = (
    "You are an adversarial plan critic. Do not write to files. "
    "Do not score; emit findings."
)
PLAN_CRITIC_WITH_UNGUARDED_RESOURCE = (
    "You are an adversarial plan critic. Do not write to files. "
    "Do not score; emit findings. "
    "Take a screenshot of http://localhost:3000."
)


class PromptsThatMissedAGapClass(unittest.TestCase):
    """B and C are the literal examples from the A/B test writeup."""

    def test_prompt_b_hits_prohibition_without_fallback(self) -> None:
        result = lint.check(PROMPT_B)
        self.assertTrue(result["hit"])
        rules = {f["rule"] for f in result["findings"]}
        self.assertIn("prohibition-without-fallback", rules)
        self.assertIn("unguarded-runtime-dependency", rules)
        self.assertEqual(result["severity"], "normal")  # no judge role in B

    def test_prompt_c_hits_and_is_severity_high(self) -> None:
        """C assigns an evaluator role, so the judge-role amplifier fires."""
        result = lint.check(PROMPT_C)
        self.assertTrue(result["hit"])
        rules = {f["rule"] for f in result["findings"]}
        self.assertIn("prohibition-without-fallback", rules)
        self.assertIn("judge-role", rules)
        self.assertEqual(result["severity"], "high")

    def test_prompt_b_fixed_with_a_fallback_is_clean(self) -> None:
        result = lint.check(PROMPT_B_FIXED)
        self.assertFalse(result["hit"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["severity"], "none")

    def test_prompt_c_fixed_with_a_fallback_is_clean(self) -> None:
        """The fixed clause still contains 'do not' but is guarded in the same
        sentence by 'if screenshots fail, stop and report' — must not re-hit."""
        result = lint.check(PROMPT_C_FIXED)
        self.assertFalse(result["hit"])
        self.assertEqual(result["findings"], [])


class PromptsThatShouldNotHit(unittest.TestCase):
    def test_a_plain_implementation_prompt_is_clean(self) -> None:
        result = lint.check(PLAIN_IMPLEMENTATION)
        self.assertFalse(result["hit"])
        self.assertEqual(result["severity"], "none")

    def test_judge_role_alone_does_not_hit(self) -> None:
        """Rule (c) is an amplifier only — never a hit by itself."""
        result = lint.check(JUDGE_ROLE_ALONE)
        self.assertFalse(result["hit"])
        self.assertEqual(result["severity"], "none")

    def test_scope_limits_with_no_failable_resource_are_clean(self) -> None:
        """A plan-critic-style '## What you must NOT do' list — real
        prohibitions, but nothing failable to fall back from. Orchestrator
        decision: rule (a) is gated on a runtime resource being present."""
        result = lint.check(PLAN_CRITIC_SCOPE_LIMITS)
        self.assertFalse(result["hit"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["severity"], "none")


class GatingUnlocksOnAFailableResource(unittest.TestCase):
    def test_same_scope_limits_plus_an_unguarded_resource_hits_high(self) -> None:
        """Adding an unguarded screenshot/localhost dependency to the same
        scope-limited prompt now hits both rules, amplified by judge-role."""
        result = lint.check(PLAN_CRITIC_WITH_UNGUARDED_RESOURCE)
        self.assertTrue(result["hit"])
        rules = {f["rule"] for f in result["findings"]}
        self.assertIn("prohibition-without-fallback", rules)
        self.assertIn("unguarded-runtime-dependency", rules)
        self.assertEqual(result["severity"], "high")


class UnguardedRuntimeDependency(unittest.TestCase):
    def test_a_url_with_no_fallback_anywhere_hits(self) -> None:
        result = lint.check(UNGUARDED_URL)
        self.assertTrue(result["hit"])
        rules = [f["rule"] for f in result["findings"]]
        self.assertIn("unguarded-runtime-dependency", rules)


class CliBehavior(unittest.TestCase):
    def _run(self, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
        script = pathlib.Path(__file__).resolve().parent / "dispatch_prompt_lint.py"
        return subprocess.run(
            [sys.executable, str(script), *args],
            input=stdin, capture_output=True, text=True,
        )

    def test_stdin_dash_works(self) -> None:
        proc = self._run(["-"], stdin=PROMPT_B)
        self.assertEqual(proc.returncode, 1)

    def test_json_shape_on_hit(self) -> None:
        proc = self._run(["--json", "-"], stdin=PROMPT_C)
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertIn("hit", payload)
        self.assertIn("severity", payload)
        self.assertIn("findings", payload)
        self.assertIn("next", payload)
        self.assertTrue(payload["hit"])
        self.assertEqual(payload["severity"], "high")

    def test_json_shape_on_clean(self) -> None:
        proc = self._run(["--json", "-"], stdin=PLAIN_IMPLEMENTATION)
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["hit"])
        self.assertEqual(payload["findings"], [])

    def test_unreadable_path_exits_two(self) -> None:
        proc = self._run(["/nonexistent/path/does-not-exist.md"])
        self.assertEqual(proc.returncode, 2)

    def test_clean_exit_zero(self) -> None:
        proc = self._run(["-"], stdin=PLAIN_IMPLEMENTATION)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
