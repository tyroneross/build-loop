#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the cross-agent public/helper skill surface policy."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
CODEX_PLUGIN_JSON = REPO_ROOT / ".codex-plugin" / "plugin.json"
CODEX_SKILLS_DIR = REPO_ROOT / "codex-skills"
SKILLS_DIR = REPO_ROOT / "skills"

PUBLIC_ENTRYPOINTS = {
    "build-loop",
    "debug-loop",
    "optimize",
    "research",
    "knowledge",
    "handoff",
}

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
USER_INVOCABLE_RE = re.compile(r"^user-invocable:\s*(.+?)\s*$", re.MULTILINE)


def read_frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if match is None:
        raise AssertionError(f"missing frontmatter: {path}")
    return match.group(1)


def read_name(path: Path) -> str:
    frontmatter = read_frontmatter(path)
    match = NAME_RE.search(frontmatter)
    if match is None:
        raise AssertionError(f"missing name: {path}")
    name = match.group(1).strip().strip('"').strip("'")
    return name.split(":", 1)[1] if ":" in name else name


def read_user_invocable(path: Path) -> str | None:
    frontmatter = read_frontmatter(path)
    match = USER_INVOCABLE_RE.search(frontmatter)
    if match is None:
        return None
    return match.group(1).strip().strip('"').strip("'")


class CodexSurfaceTests(unittest.TestCase):
    def test_codex_manifest_uses_public_skill_root(self) -> None:
        data = json.loads(CODEX_PLUGIN_JSON.read_text(encoding="utf-8"))
        self.assertEqual(data.get("skills"), "./codex-skills")

    def test_codex_public_wrappers_are_exact_entrypoint_set(self) -> None:
        names = {
            read_name(path)
            for path in sorted(CODEX_SKILLS_DIR.glob("*/SKILL.md"))
        }
        self.assertEqual(names, PUBLIC_ENTRYPOINTS)


class ClaudeSurfaceTests(unittest.TestCase):
    def test_claude_skill_invocability_matches_public_policy(self) -> None:
        actual: dict[str, str | None] = {}
        for path in sorted(SKILLS_DIR.rglob("SKILL.md")):
            actual[str(path.relative_to(REPO_ROOT))] = read_user_invocable(path)

        violations: list[str] = []
        for rel_path, flag in actual.items():
            name = read_name(REPO_ROOT / rel_path)
            expected = "true" if name in PUBLIC_ENTRYPOINTS else "false"
            if flag != expected:
                violations.append(f"{rel_path}: user-invocable={flag!r}, expected {expected!r}")

        self.assertEqual(violations, [], "\n".join(violations))


class OtherAgentSurfaceTests(unittest.TestCase):
    def test_host_neutral_policy_and_cursor_rule_exist(self) -> None:
        self.assertTrue((REPO_ROOT / "docs/agent-surface-policy.md").is_file())
        self.assertTrue((REPO_ROOT / ".cursor/rules/build-loop-surface.mdc").is_file())

    def test_agent_role_taxonomy_is_discoverable(self) -> None:
        taxonomy = REPO_ROOT / "references" / "agent-role-taxonomy.md"
        self.assertTrue(taxonomy.is_file())
        taxonomy_text = taxonomy.read_text(encoding="utf-8")
        self.assertIn("The lead is the session holding the current valid Rally Point leadership lease", taxonomy_text)
        self.assertIn("Build-loop already has a dedicated coder subagent: `implementer`", taxonomy_text)

        index_text = (REPO_ROOT / "references" / "INDEX.md").read_text(encoding="utf-8")
        orchestrator_text = (REPO_ROOT / "agents" / "build-orchestrator.md").read_text(encoding="utf-8")
        skill_text = (REPO_ROOT / "skills" / "build-loop" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("agent-role-taxonomy.md", index_text)
        self.assertIn("agent-role-taxonomy.md", orchestrator_text)
        self.assertIn("agent-role-taxonomy.md", skill_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
