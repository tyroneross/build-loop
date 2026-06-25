#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for sync_agent_model_defaults.py — drift detection, idempotent apply, guards."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "sync_agent_model_defaults.py"
REPO_AGENTS = HERE.parent / "agents"

sys.path.insert(0, str(HERE))
import sync_agent_model_defaults as sync  # noqa: E402


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "BUILD_LOOP_HOST_PROVIDERS": "anthropic"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False, capture_output=True, text=True, env=env,
    )


def _agent(adir: Path, name: str, *, segment: str, tier: str, model: str) -> Path:
    adir.mkdir(parents=True, exist_ok=True)
    p = adir / f"{name}.md"
    p.write_text(
        f"---\nname: {name}\ndescription: t\nmodel: {model}\ntier: {tier}\nsegment: {segment}\n---\nBody\n",
        encoding="utf-8",
    )
    return p


class RealAgentsZeroDrift(unittest.TestCase):
    """The shipped agents are already in sync with the index (back-compat)."""

    def test_check_reports_zero_drift(self):
        cp = run("--check", "--workdir", str(HERE.parent),
                 "--agents-dir", str(REPO_AGENTS), "--json")
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        report = json.loads(cp.stdout)
        self.assertEqual(report["drift_count"], 0)
        self.assertEqual(report["total"], len(list(REPO_AGENTS.glob("*.md"))))


class DriftApplyIdempotent(unittest.TestCase):
    def test_stale_model_drift_then_apply_then_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            adir = Path(td) / "agents"
            # agentic_execution/code -> recommended sonnet; seed stale haiku.
            p = _agent(adir, "impl", segment="agentic_execution", tier="code", model="haiku")

            chk = run("--check", "--workdir", td, "--agents-dir", str(adir), "--json")
            self.assertEqual(chk.returncode, 1)
            self.assertEqual(json.loads(chk.stdout)["drift_count"], 1)

            ap = run("--apply", "--workdir", td, "--agents-dir", str(adir), "--json")
            self.assertEqual(ap.returncode, 0)
            self.assertEqual(json.loads(ap.stdout)["applied"], ["impl"])
            self.assertIn("model: sonnet", p.read_text())
            self.assertNotIn("model: haiku", p.read_text())

            chk2 = run("--check", "--workdir", td, "--agents-dir", str(adir), "--json")
            self.assertEqual(chk2.returncode, 0)
            self.assertEqual(json.loads(chk2.stdout)["drift_count"], 0)

            ap2 = run("--apply", "--workdir", td, "--agents-dir", str(adir), "--json")
            self.assertEqual(json.loads(ap2.stdout)["applied"], [])

    def test_only_model_line_changes(self):
        with tempfile.TemporaryDirectory() as td:
            adir = Path(td) / "agents"
            p = _agent(adir, "impl", segment="agentic_execution", tier="code", model="haiku")
            before = p.read_text().splitlines()
            run("--apply", "--workdir", td, "--agents-dir", str(adir), "--json")
            after = p.read_text().splitlines()
            # Same line count; only the model: line differs.
            self.assertEqual(len(before), len(after))
            diffs = [(b, a) for b, a in zip(before, after) if b != a]
            self.assertEqual(len(diffs), 1)
            self.assertTrue(diffs[0][0].startswith("model:") and diffs[0][1].startswith("model:"))


class Guards(unittest.TestCase):
    def test_inherit_agent_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            adir = Path(td) / "agents"
            _agent(adir, "rci", segment="inherit", tier="inherit", model="inherit")
            v = sync.evaluate_agent(adir / "rci.md", Path(td), adir, {"anthropic"})
            self.assertEqual(v["status"], "skipped")
            self.assertEqual(v["reason"], "inherit")

    def test_cross_provider_recommendation_kept_not_written(self):
        # Force a cross-provider recommendation: anthropic models unavailable for
        # the code tier so the resolver returns a non-anthropic id; with the host
        # filter disabled (any) it can surface. Guard must keep the existing token.
        with tempfile.TemporaryDirectory() as td:
            adir = Path(td) / "agents"
            _agent(adir, "impl", segment="agentic_execution", tier="code", model="sonnet")
            bl = Path(td) / ".build-loop"; bl.mkdir()
            (bl / "model-availability.json").write_text(
                json.dumps({"unavailable": ["sonnet"], "hostProviders": ["openai"]}))
            v = sync.evaluate_agent(adir / "impl.md", Path(td), adir, None)
            self.assertEqual(v["status"], "skipped")
            self.assertEqual(v["reason"], "non-harness-token")
            self.assertEqual(v["current"], "sonnet")  # original kept


if __name__ == "__main__":
    unittest.main()
