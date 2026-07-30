#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the architecture drift linter + the live manifest."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import drift_lint
import generate

REPO = Path(__file__).resolve().parent.parent.parent

MIN_HOOKS = '{"hooks":{"SessionStart":[{"hooks":[{"command":"bash ${ROOT}/hooks/real-hook.sh"}]}]}}'
AGENT_MD = "---\nname: {name}\nmodel: {model}\ndescription: x\n---\nbody\n"


def _mk_repo(tmp: Path, flow_yaml: str, agents: dict[str, str]):
    (tmp / "agents").mkdir()
    for name, model in agents.items():
        (tmp / "agents" / f"{name}.md").write_text(AGENT_MD.format(name=name, model=model))
    (tmp / "hooks").mkdir()
    (tmp / "hooks" / "hooks.json").write_text(MIN_HOOKS)
    (tmp / "architecture").mkdir()
    # the flow now lives in a fenced yaml block in ARCHITECTURE.md under the arch:flow marker
    (tmp / "architecture" / "ARCHITECTURE.md").write_text(
        "# test\n\n<!-- arch:flow -->\n```yaml\n" + flow_yaml + "\n```\n")


BASE_FLOW = """
pipeline: {{in: [a], out: [b]}}
proposed: []
gate_after: {{}}
roles: {{}}
subagents: {{}}
hook_overrides: {{}}
agent_aliases: {{}}
agent_groups: {{}}
coverage_exempt: []
phases:
  - id: p1
    no: P1
    name: One
    lane: Orchestrator
    desc: d
    in: [x]
    out: [y]
    agents: []
    steps:
      - {{id: s1, name: S1, kind: dispatch, desc: d, hooks: {hooks}, agents: {agents}}}
"""


class TestDriftLint(unittest.TestCase):
    def test_real_repo_has_no_errors(self):
        res = drift_lint.lint(REPO)
        self.assertEqual(res["errors"], [], f"unexpected drift: {res['errors']}")

    def test_missing_agent_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _mk_repo(tmp, BASE_FLOW.format(hooks="[]", agents='[["ghost", "", "Orchestrator"]]'),
                     {"foo": "sonnet"})
            res = drift_lint.lint(tmp)
            self.assertFalse(res["ok"])
            self.assertTrue(any("ghost" in e for e in res["errors"]))

    def test_missing_hook_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _mk_repo(tmp, BASE_FLOW.format(hooks='["ghost.sh"]', agents="[]"),
                     {"foo": "sonnet"})
            res = drift_lint.lint(tmp)
            self.assertFalse(res["ok"])
            self.assertTrue(any("ghost.sh" in e for e in res["errors"]))

    def test_real_hook_and_agent_pass(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _mk_repo(tmp, BASE_FLOW.format(hooks='["real-hook.sh"]', agents='[["foo", "", "Orchestrator"]]'),
                     {"foo": "sonnet"})
            res = drift_lint.lint(tmp)
            self.assertEqual(res["errors"], [])

    def test_unreferenced_agent_warns(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _mk_repo(tmp, BASE_FLOW.format(hooks="[]", agents='[["foo", "", "Orchestrator"]]'),
                     {"foo": "sonnet", "lonely": "haiku"})
            res = drift_lint.lint(tmp)
            self.assertTrue(any("lonely" in w for w in res["warnings"]))
            self.assertEqual(res["errors"], [])  # coverage gap is WARN, not ERROR


class TestGenerate(unittest.TestCase):
    def test_model_builds_and_fills_tiers(self):
        model = generate.build_model(REPO)
        self.assertGreaterEqual(len(model["phases"]), 7)
        # every agent ref in every step has a 3-tuple; known agents get a non-empty tier
        seen_filled = False
        for p in model["phases"]:
            for ref in p["agents"]:
                self.assertEqual(len(ref), 3)
                if ref[0] == "independent-auditor":
                    self.assertTrue(ref[1], "independent-auditor tier should fill from frontmatter")
                    seen_filled = True
        self.assertTrue(seen_filled)

    def test_provenance_is_content_hash_not_git(self):
        model = generate.build_model(REPO)
        prov = model["_provenance"]
        self.assertIn("content_sha256", prov)
        self.assertNotIn("source_commit", prov)  # must not embed volatile git state
        # deterministic: same source -> same hash
        self.assertEqual(prov["content_sha256"], generate.build_model(REPO)["_provenance"]["content_sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
