#!/usr/bin/env python3
"""The README's surface claims must match what the repo actually ships.

The 2026-08-28 audit found README.md advertising "44 skills, 28 agents" against a
measured 51 and 29, and pinning `@tyroneross/build-loop@0.38.0` in three places
while package.json shipped 0.39.0. Nothing checked either number, so both drifted
silently across releases and a reader following the quick start installed a stale
version.

Counts come from `git ls-files`, not the filesystem: what ships is what is tracked.
A scratch skill directory in a dirty working tree must not fail this test — a gate
that fires on unrelated local state is one people learn to skip.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def tracked(*patterns: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", *patterns],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def shipped_skill_count() -> int:
    """Every tracked SKILL.md under skills/, including nested sub-skills.

    skills/architecture/ holds six sub-skills one directory down, so counting
    top-level directories undercounts what an agent can actually reach.
    """
    return len([p for p in tracked("skills/") if p.endswith("/SKILL.md")])


def shipped_agent_count() -> int:
    return len([p for p in tracked("agents/") if p.endswith(".md")])


def package_version() -> str:
    return json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]


class ReadmeSurfaceClaimsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_skill_and_agent_counts_match_what_is_tracked(self) -> None:
        match = re.search(r"Surface counts in this release:.*?(\d+) skills, (\d+) agents", self.readme)
        self.assertIsNotNone(match, "README lost its 'Surface counts in this release' line")
        claimed_skills, claimed_agents = int(match.group(1)), int(match.group(2))
        self.assertEqual(
            claimed_skills, shipped_skill_count(),
            "README advertises a skill count the repo does not ship — update the README "
            "line, not this test",
        )
        self.assertEqual(
            claimed_agents, shipped_agent_count(),
            "README advertises an agent count the repo does not ship — update the README "
            "line, not this test",
        )

    def test_every_pinned_version_matches_package_json(self) -> None:
        pinned = set(re.findall(r"@tyroneross/build-loop@(\d+\.\d+\.\d+)", self.readme))
        pinned |= set(re.findall(r"--version v(\d+\.\d+\.\d+)", self.readme))
        version = package_version()
        self.assertTrue(pinned, "README pins no version — the quick start lost its install line")
        self.assertEqual(
            pinned, {version},
            f"README pins {sorted(pinned)} but package.json ships {version}",
        )

    def test_readme_links_the_generated_skill_index(self) -> None:
        self.assertTrue((ROOT / "docs" / "SKILL-INDEX.md").exists())
        self.assertIn(
            "docs/SKILL-INDEX.md", self.readme,
            "docs/SKILL-INDEX.md is the routing table for every skill; the README must "
            "point a reader at it",
        )

    def test_every_public_command_is_documented(self) -> None:
        commands = sorted(p.stem for p in (ROOT / "commands").glob("*.md"))
        self.assertTrue(commands, "commands/ is empty")
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(
                    f"/build-loop:{command}", self.readme,
                    f"/build-loop:{command} ships in commands/ but the README never names "
                    f"it — an undocumented command is an unreachable one",
                )


if __name__ == "__main__":
    unittest.main()
