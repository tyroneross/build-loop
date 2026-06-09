#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for dispatch task ids and model override resolution."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISPATCH_ID = HERE / "dispatch_identity.py"
MODEL_OVERRIDES = HERE / "model_overrides.py"
ROUTE_DECISION = HERE / "route_decision.py"
TASK_ID_RE = re.compile(r"^t-[0-9a-f]{8}$")


def run_script(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(path), *args],
        check=False,
        capture_output=True,
        text=True,
    )


class DispatchIdentityTests(unittest.TestCase):
    def test_plain_task_id_matches_contract(self) -> None:
        result = run_script(DISPATCH_ID, "--plain")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.strip(), TASK_ID_RE)

    def test_validate_rejects_bad_task_id(self) -> None:
        result = run_script(DISPATCH_ID, "--validate", "bad-id", "--json")
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["valid"])


class ModelOverrideTests(unittest.TestCase):
    def test_config_override_wins_over_state_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            build_loop = workdir / ".build-loop"
            build_loop.mkdir()
            (build_loop / "config.json").write_text(
                json.dumps({"modelOverrides": {"code": "gpt-5-codex"}}),
                encoding="utf-8",
            )
            (build_loop / "state.json").write_text(
                json.dumps({"config": {"modelOverrides": {"code": "sonnet"}}}),
                encoding="utf-8",
            )

            result = run_script(
                MODEL_OVERRIDES,
                "--workdir", str(workdir),
                "--tier", "code",
                "--fallback", "sonnet",
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-5-codex")
            self.assertEqual(payload["source"], "config")
            self.assertTrue(payload["configured"])

    def test_fallback_when_no_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_script(
                MODEL_OVERRIDES,
                "--workdir", td,
                "--tier", "pattern",
                "--fallback", "haiku",
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "haiku")
            self.assertEqual(payload["source"], "fallback")
            self.assertFalse(payload["configured"])

    def test_tier_default_used_when_no_override_or_fallback(self) -> None:
        # Without an explicit fallback, the tier's built-in default resolves.
        # frontier -> fable, thinking -> opus, code -> sonnet, pattern -> haiku.
        with tempfile.TemporaryDirectory() as td:
            for tier, expected in (
                ("frontier", "fable"),
                ("thinking", "opus"),
                ("code", "sonnet"),
                ("pattern", "haiku"),
            ):
                with self.subTest(tier=tier):
                    result = run_script(
                        MODEL_OVERRIDES,
                        "--workdir", td,
                        "--tier", tier,
                        "--json",
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["model"], expected)
                    self.assertEqual(payload["source"], "tier-default")
                    self.assertFalse(payload["configured"])

    def test_frontier_tier_accepts_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            build_loop = workdir / ".build-loop"
            build_loop.mkdir()
            (build_loop / "config.json").write_text(
                json.dumps({"modelOverrides": {"frontier": "gpt-5-thinking-pro"}}),
                encoding="utf-8",
            )
            result = run_script(
                MODEL_OVERRIDES,
                "--workdir", str(workdir),
                "--tier", "frontier",
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-5-thinking-pro")
            self.assertEqual(payload["source"], "config")
            self.assertTrue(payload["configured"])

    def test_explicit_fallback_beats_tier_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_script(
                MODEL_OVERRIDES,
                "--workdir", td,
                "--tier", "code",
                "--fallback", "gpt-5-codex",
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-5-codex")
            self.assertEqual(payload["source"], "fallback")


class RouteDecisionOverrideTests(unittest.TestCase):
    def test_route_decision_reads_config_model_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = root / "plan.md"
            config = root / "config.json"
            state = root / "state.json"
            plan.write_text(
                textwrap.dedent(
                    """\
                    # Low density

                    synthesis_dimensions:
                      one: x

                    ## Body
                    """
                ),
                encoding="utf-8",
            )
            config.write_text(
                json.dumps({"modelOverrides": {"thinking": "gpt-5-thinking"}}),
                encoding="utf-8",
            )

            result = run_script(
                ROUTE_DECISION,
                "--plan", str(plan),
                "--config", str(config),
                "--state", str(state),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["tier"], "thinking")
            self.assertEqual(payload["reason"], "explicit-override")
            self.assertEqual(payload["synthesis_dimensions_count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
