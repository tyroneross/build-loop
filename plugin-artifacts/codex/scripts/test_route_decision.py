#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/route_decision.py — the Phase-1 thinking|code routing helper.

This file was missing before the two-axis taxonomy refactor; added per the plan.
route_decision is a LEGACY-TOKEN consumer (it emits and reasons in
thinking|code), so these tests double as a back-compat proof that the alias
layer keeps the legacy routing contract intact.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUTE_DECISION = HERE / "route_decision.py"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROUTE_DECISION), *args],
        capture_output=True, text=True,
    )


class RouteDecisionTests(unittest.TestCase):
    def test_self_test_passes(self) -> None:
        # The script ships an inline self-test demonstrating all 4 reasons.
        result = run("--self-test")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("all self-tests passed", result.stdout)

    def test_no_plan_defaults_to_code(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nope.md"
            result = run("--plan", str(missing), "--state", str(Path(td) / "s.json"))
            self.assertEqual(result.returncode, 0, result.stderr)
            v = json.loads(result.stdout)
            self.assertEqual(v["tier"], "code")
            self.assertEqual(v["reason"], "no-plan")

    def test_low_density_default_fanout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plan = Path(td) / "plan.md"
            plan.write_text(textwrap.dedent("""\
                # Plan
                synthesis_dimensions:
                  a: 1
                  b: 2
                ## Body
            """))
            result = run("--plan", str(plan), "--state", str(Path(td) / "s.json"))
            v = json.loads(result.stdout)
            self.assertEqual(v["tier"], "code")
            self.assertEqual(v["reason"], "default-fanout")

    def test_high_density_escalates_to_thinking(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plan = Path(td) / "plan.md"
            plan.write_text(textwrap.dedent("""\
                # Plan
                synthesis_dimensions:
                  a: 1
                  b: 2
                  c: 3
                  d: 4
                  e: 5
                  f: 6
                ## Body
            """))
            result = run("--plan", str(plan), "--state", str(Path(td) / "s.json"))
            v = json.loads(result.stdout)
            self.assertEqual(v["tier"], "thinking")
            self.assertEqual(v["reason"], "density-escalate")

    def test_legacy_thinking_override_via_config(self) -> None:
        # Back-compat: a config modelOverrides.thinking set (a LEGACY token)
        # still routes to thinking through the alias layer.
        with tempfile.TemporaryDirectory() as td:
            plan = Path(td) / "plan.md"
            plan.write_text("# Plan\nsynthesis_dimensions:\n  a: 1\n## Body\n")
            config = Path(td) / "config.json"
            config.write_text(json.dumps({"modelOverrides": {"thinking": "opus"}}))
            result = run("--plan", str(plan), "--state", str(Path(td) / "s.json"),
                         "--config", str(config))
            v = json.loads(result.stdout)
            self.assertEqual(v["tier"], "thinking")
            self.assertEqual(v["reason"], "explicit-override")

    def test_frontmatter_tier_thinking_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plan = Path(td) / "plan.md"
            plan.write_text(textwrap.dedent("""\
                ---
                tier: thinking
                ---
                # Plan
                synthesis_dimensions:
                  a: 1
                ## Body
            """))
            result = run("--plan", str(plan), "--state", str(Path(td) / "s.json"))
            v = json.loads(result.stdout)
            self.assertEqual(v["tier"], "thinking")
            self.assertEqual(v["reason"], "explicit-override")

    def test_threshold_five_stays_code(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plan = Path(td) / "plan.md"
            plan.write_text(textwrap.dedent("""\
                # Plan
                synthesis_dimensions:
                  a: 1
                  b: 2
                  c: 3
                  d: 4
                  e: 5
                ## Body
            """))
            result = run("--plan", str(plan), "--state", str(Path(td) / "s.json"))
            v = json.loads(result.stdout)
            self.assertEqual(v["tier"], "code")  # >5 means 6+; 5 stays code


if __name__ == "__main__":
    unittest.main()
