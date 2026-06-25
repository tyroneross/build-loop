#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for resolve_agent_model.py — frontmatter role -> dispatch model + fallback chain."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "resolve_agent_model.py"
REPO_AGENTS = HERE.parent / "agents"

sys.path.insert(0, str(HERE))
import resolve_agent_model as ram  # noqa: E402


def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    run_env = {**os.environ, "BUILD_LOOP_HOST_PROVIDERS": "anthropic", **(env or {})}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False, capture_output=True, text=True, env=run_env,
    )


def _write_agent(adir: Path, name: str, segment: str | None, tier: str | None, model: str | None) -> None:
    adir.mkdir(parents=True, exist_ok=True)
    fm = ["---"]
    fm.append(f"name: {name}")
    fm.append("description: test agent")
    if model is not None:
        fm.append(f"model: {model}")
    if tier is not None:
        fm.append(f"tier: {tier}")
    if segment is not None:
        fm.append(f"segment: {segment}")
    fm.append("---")
    fm.append("")
    fm.append("Body.")
    (adir / f"{name}.md").write_text("\n".join(fm), encoding="utf-8")


class FrontmatterParse(unittest.TestCase):
    def test_flat_scalars(self):
        fm = ram._parse_frontmatter(
            "---\nname: x\nmodel: sonnet\ntier: code\nsegment: agentic_execution\n---\nbody"
        )
        self.assertEqual(fm["model"], "sonnet")
        self.assertEqual(fm["tier"], "code")
        self.assertEqual(fm["segment"], "agentic_execution")

    def test_block_scalar_body_skipped(self):
        # A `description: |` block scalar must not leak its indented body as keys.
        text = "---\ndescription: |\n  line one\n  model: NOTAMODEL\nmodel: opus\ntier: thinking\nsegment: agentic_execution\n---\n"
        fm = ram._parse_frontmatter(text)
        self.assertEqual(fm["model"], "opus")
        self.assertNotIn("line one", fm)

    def test_no_frontmatter(self):
        self.assertEqual(ram._parse_frontmatter("no front matter here"), {})


class RealAgentsBackCompat(unittest.TestCase):
    """On an anthropic host every agent resolves to its current model: token."""

    EXPECT = {
        "advisor": "fable",
        "build-orchestrator": "opus",
        "implementer": "sonnet",
        "mock-scanner": "haiku",
        "self-improvement-architect": "sonnet",
        "plan-critic": "fable",
    }

    def test_resolved_equals_frontmatter_model(self):
        for agent, expected in self.EXPECT.items():
            with self.subTest(agent=agent):
                cp = run(agent, "--workdir", str(HERE.parent), "--plain")
                self.assertEqual(cp.returncode, 0, cp.stderr)
                self.assertEqual(cp.stdout.strip(), expected)

    def test_envelope_keys(self):
        cp = run("implementer", "--workdir", str(HERE.parent), "--json")
        env = json.loads(cp.stdout)
        for key in ("agent", "segment", "tier", "model", "source", "resolution_path"):
            self.assertIn(key, env)
        self.assertEqual(env["agent"], "implementer")
        self.assertEqual(env["source"], "role-preferred")


class InheritAgent(unittest.TestCase):
    def test_root_cause_investigator_is_inherit(self):
        cp = run("root-cause-investigator", "--workdir", str(HERE.parent), "--plain")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(cp.stdout.strip(), "inherit")

    def test_inherit_envelope_source(self):
        env = ram.resolve(agent="root-cause-investigator", workdir=HERE.parent)
        self.assertEqual(env["model"], "inherit")
        self.assertEqual(env["source"], "inherit")


class FallbackChain(unittest.TestCase):
    def test_missing_segment_falls_back_to_frontmatter_model(self):
        with tempfile.TemporaryDirectory() as td:
            adir = Path(td) / "agents"
            _write_agent(adir, "noseg", segment=None, tier=None, model="sonnet")
            env = ram.resolve(agent="noseg", workdir=Path(td), agents_dir=adir,
                              host_providers={"anthropic"})
            self.assertEqual(env["model"], "sonnet")
            self.assertEqual(env["source"], "frontmatter-fallback")

    def test_invalid_tier_falls_back_to_frontmatter_model(self):
        with tempfile.TemporaryDirectory() as td:
            adir = Path(td) / "agents"
            _write_agent(adir, "badtier", segment="agentic_execution", tier="bogus", model="haiku")
            env = ram.resolve(agent="badtier", workdir=Path(td), agents_dir=adir,
                              host_providers={"anthropic"})
            self.assertEqual(env["model"], "haiku")
            self.assertEqual(env["source"], "frontmatter-fallback")

    def test_no_model_no_valid_tags_unresolved(self):
        with tempfile.TemporaryDirectory() as td:
            adir = Path(td) / "agents"
            _write_agent(adir, "empty", segment="agentic_execution", tier="bogus", model=None)
            env = ram.resolve(agent="empty", workdir=Path(td), agents_dir=adir,
                              host_providers={"anthropic"})
            self.assertIsNone(env["model"])
            self.assertEqual(env["source"], "unresolved")

    def test_tier_default_fallback_when_segment_missing_but_legacy_tier(self):
        # No segment, no model:, but a known legacy tier -> tier default.
        with tempfile.TemporaryDirectory() as td:
            adir = Path(td) / "agents"
            _write_agent(adir, "tieronly", segment=None, tier="code", model=None)
            env = ram.resolve(agent="tieronly", workdir=Path(td), agents_dir=adir,
                              host_providers={"anthropic"})
            self.assertEqual(env["model"], "sonnet")
            self.assertEqual(env["source"], "tier-default-fallback")

    def test_missing_agent_file(self):
        cp = run("does-not-exist", "--workdir", str(HERE.parent), "--plain")
        self.assertEqual(cp.returncode, 1)


if __name__ == "__main__":
    unittest.main()
