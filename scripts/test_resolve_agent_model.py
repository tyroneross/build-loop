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
import model_taxonomy  # noqa: E402
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
        "fact-checker": "opus",
        "fix-critique": "opus",
        "implementer": "sonnet",
        "mock-scanner": "haiku",
        "overfitting-reviewer": "opus",
        "promotion-reviewer": "opus",
        "scope-auditor": "opus",
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


class OpenAIAgentApprovals(unittest.TestCase):
    """Codex resolves approved roles to the right GPT-5.6 family member."""

    EXPECT = {
        # Planning + gating verification: strongest model.
        "advisor": "gpt-5.6-sol",
        "fact-checker": "gpt-5.6-sol",
        "fix-critique": "gpt-5.6-sol",
        "plan-critic": "gpt-5.6-sol",
        "independent-auditor": "gpt-5.6-sol",
        "overfitting-reviewer": "gpt-5.6-sol",
        "promotion-reviewer": "gpt-5.6-sol",
        "scope-auditor": "gpt-5.6-sol",
        "security-reviewer": "gpt-5.6-sol",
        # Coordination + bounded execution: capable lower-cost workhorse.
        "build-orchestrator": "gpt-5.6-terra",
        "assessment-orchestrator": "gpt-5.6-terra",
        "implementer": "gpt-5.6-terra",
        "api-assessor": "gpt-5.6-terra",
        "architecture-scout": "gpt-5.6-terra",
        "database-assessor": "gpt-5.6-terra",
        "design-contract-specialist": "gpt-5.6-terra",
        "frontend-assessor": "gpt-5.6-terra",
        "optimize-runner": "gpt-5.6-terra",
        "performance-assessor": "gpt-5.6-terra",
        "retrospective-synthesizer": "gpt-5.6-terra",
        "self-improvement-architect": "gpt-5.6-terra",
        "synthesis-critic": "gpt-5.6-terra",
        "ui-validator": "gpt-5.6-terra",
        "alignment-checker": "gpt-5.6-terra",
        # Bounded recognition: fastest efficient model.
        "mock-scanner": "gpt-5.6-luna",
        "recurring-pattern-detector": "gpt-5.6-luna",
        "transcript-pattern-miner": "gpt-5.6-luna",
    }

    def test_openai_host_role_resolution(self):
        for agent, expected in self.EXPECT.items():
            with self.subTest(agent=agent):
                env = ram.resolve(
                    agent=agent,
                    workdir=HERE.parent,
                    host_providers={"openai"},
                )
                self.assertEqual(env["model"], expected, env)
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


class PromptingProfileEnvelope(unittest.TestCase):
    """T-03: prompting_profile rides the envelope for role-resolved + inherit agents."""

    def test_role_resolved_agent_carries_its_tier_profile(self):
        # advisor is tier: frontier (legacy) -> normalizes to T1.
        env = ram.resolve(agent="advisor", workdir=HERE.parent, host_providers={"anthropic"})
        self.assertEqual(env["source"], "role-preferred")
        self.assertEqual(env["prompting_profile"], model_taxonomy.prompting_profile(env["tier"]))
        self.assertIsNotNone(env["prompting_profile"])

    def test_inherit_agent_carries_no_profile(self):
        env = ram.resolve(agent="root-cause-investigator", workdir=HERE.parent)
        self.assertEqual(env["source"], "inherit")
        self.assertIsNone(env["prompting_profile"])


class LegacyTierProfileParity(unittest.TestCase):
    """T-04: a legacy tier token resolves to the same profile as its ladder rung."""

    def test_legacy_and_ladder_tier_tokens_agree_via_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            adir = Path(td) / "agents"
            _write_agent(adir, "legacy-tier-agent", segment="agentic_execution", tier="code", model=None)
            _write_agent(adir, "ladder-tier-agent", segment="agentic_execution", tier="T3", model=None)
            legacy_env = ram.resolve(agent="legacy-tier-agent", workdir=Path(td), agents_dir=adir,
                                      host_providers={"anthropic"})
            ladder_env = ram.resolve(agent="ladder-tier-agent", workdir=Path(td), agents_dir=adir,
                                      host_providers={"anthropic"})
            self.assertEqual(legacy_env["prompting_profile"], ladder_env["prompting_profile"])
            self.assertEqual(
                legacy_env["prompting_profile"],
                model_taxonomy.prompting_profile("T3"),
            )
            self.assertEqual(
                model_taxonomy.prompting_profile("code"),
                model_taxonomy.prompting_profile("T3"),
            )


class EnvelopeRegression(unittest.TestCase):
    """T-05: pre-existing envelope keys/semantics unchanged; --plain untouched."""

    OLD_KEYS = {"agent", "segment", "tier", "model", "source", "resolution_path"}

    def test_old_keys_are_a_subset_of_new_envelope(self):
        env = ram.resolve(agent="implementer", workdir=HERE.parent, host_providers={"anthropic"})
        self.assertTrue(self.OLD_KEYS.issubset(env.keys()))
        self.assertIn("prompting_profile", env)

    def test_plain_output_is_still_only_the_model_id(self):
        cp = run("implementer", "--workdir", str(HERE.parent), "--plain")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(cp.stdout, "sonnet\n")

    def test_prompting_profile_present_at_all_five_return_sites(self):
        # role-preferred (real agent, role resolves normally).
        role_env = ram.resolve(agent="implementer", workdir=HERE.parent, host_providers={"anthropic"})
        self.assertEqual(role_env["source"], "role-preferred")
        self.assertIn("prompting_profile", role_env)

        # inherit.
        inherit_env = ram.resolve(agent="root-cause-investigator", workdir=HERE.parent)
        self.assertEqual(inherit_env["source"], "inherit")
        self.assertIn("prompting_profile", inherit_env)
        self.assertIsNone(inherit_env["prompting_profile"])

        with tempfile.TemporaryDirectory() as td:
            adir = Path(td) / "agents"

            # frontmatter-fallback: missing segment, model: present.
            _write_agent(adir, "noseg", segment=None, tier=None, model="sonnet")
            fm_env = ram.resolve(agent="noseg", workdir=Path(td), agents_dir=adir,
                                  host_providers={"anthropic"})
            self.assertEqual(fm_env["source"], "frontmatter-fallback")
            self.assertIn("prompting_profile", fm_env)

            # tier-default-fallback: no segment, no model:, known legacy tier.
            _write_agent(adir, "tieronly", segment=None, tier="code", model=None)
            tier_env = ram.resolve(agent="tieronly", workdir=Path(td), agents_dir=adir,
                                    host_providers={"anthropic"})
            self.assertEqual(tier_env["source"], "tier-default-fallback")
            self.assertIn("prompting_profile", tier_env)
            self.assertEqual(tier_env["prompting_profile"], model_taxonomy.prompting_profile("code"))

            # unresolved: no segment, no model:, unknown tier.
            _write_agent(adir, "empty", segment="agentic_execution", tier="bogus", model=None)
            unresolved_env = ram.resolve(agent="empty", workdir=Path(td), agents_dir=adir,
                                          host_providers={"anthropic"})
            self.assertEqual(unresolved_env["source"], "unresolved")
            self.assertIn("prompting_profile", unresolved_env)
            self.assertIsNone(unresolved_env["prompting_profile"])


if __name__ == "__main__":
    unittest.main()
