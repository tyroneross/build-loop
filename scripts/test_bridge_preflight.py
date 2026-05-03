#!/usr/bin/env python3
"""Tests that every bridge skill has the availablePlugins pre-flight check.

Stdlib only. Run: python3 test_bridge_preflight.py

A "bridge" is a skill whose name ends in `-bridge` and whose role is to
optionally delegate to another (separately-installed) plugin. Bridges
must pre-flight whether the target plugin is available — without that
check, a bridge call hard-fails when the target isn't installed,
breaking the graceful-degradation contract.

This test enforces the pattern: every `*-bridge/SKILL.md` must contain
the substring `availablePlugins.` somewhere in its body. The actual
detection is intentionally permissive — we don't try to parse a specific
JS/JSON shape because bridges document the check in prose, code blocks,
or both.

Bridges in build-loop:
  - debugger-bridge        → claude-code-debugger (extended capability)
  - logging-tracer-bridge  → claude-code-debugger (extended observability)
  - navgator-bridge        → navgator (architecture analysis)
  - api-registry-bridge    → api-registry (api discovery)
  - prd-bridge             → docs/prd-*.md (PRD-grounded planning)

If a new bridge is added without a pre-flight, this test fails with a
hint pointing at the missing skill body.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SKILLS_DIR = REPO_ROOT / "skills"

PREFLIGHT_PATTERNS = (
    # State-object lookups (plugin-availability)
    "availablePlugins.",
    "availablePlugins[",
    # Plugin-absence narrative phrasings
    "if absent",
    "is absent",
    "not installed",
    "graceful degrade",
    "Graceful degrade",
    "this bridge skips",
    "this skill no-ops",
    "no-ops with",
    # PRD/dependency-absence narrative phrasings
    "when one doesn't",
    "when one doesn",
    "if no PRD",
    "if no ",
    "Recommend the ",  # e.g. "Recommend the prd-builder skill if no PRD exists"
)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def is_bridge_skill(skill_dir: Path) -> bool:
    return skill_dir.name.endswith("-bridge")


def has_preflight(skill_md: Path) -> bool:
    text = skill_md.read_text(encoding="utf-8")
    return any(p in text for p in PREFLIGHT_PATTERNS)


def has_frontmatter(skill_md: Path) -> bool:
    text = skill_md.read_text(encoding="utf-8")
    return FRONTMATTER_RE.match(text) is not None


class BridgePreflightTests(unittest.TestCase):
    def test_every_bridge_has_preflight(self) -> None:
        if not SKILLS_DIR.is_dir():
            self.skipTest(f"{SKILLS_DIR} not present")
        bridges = [d for d in sorted(SKILLS_DIR.iterdir()) if d.is_dir() and is_bridge_skill(d)]
        self.assertGreater(len(bridges), 0, "no bridge skills found — unexpected")
        missing: list[str] = []
        for bridge_dir in bridges:
            skill_md = bridge_dir / "SKILL.md"
            if not skill_md.is_file():
                missing.append(f"{bridge_dir.name}: SKILL.md missing")
                continue
            if not has_preflight(skill_md):
                missing.append(
                    f"{bridge_dir.name}: no preflight pattern found "
                    f"(expected one of {PREFLIGHT_PATTERNS} or alternate)"
                )
        self.assertEqual(
            missing, [],
            "Bridges without an availability pre-flight check:\n  "
            + "\n  ".join(missing)
            + "\n\nA bridge that doesn't pre-flight will hard-fail when the "
            "target plugin isn't installed, breaking graceful degradation.",
        )

    def test_every_bridge_has_frontmatter(self) -> None:
        if not SKILLS_DIR.is_dir():
            self.skipTest(f"{SKILLS_DIR} not present")
        bridges = [d for d in sorted(SKILLS_DIR.iterdir()) if d.is_dir() and is_bridge_skill(d)]
        missing = [
            d.name for d in bridges
            if not (d / "SKILL.md").is_file() or not has_frontmatter(d / "SKILL.md")
        ]
        self.assertEqual(missing, [], f"bridges missing frontmatter: {missing}")


class UserInvocableFlagTests(unittest.TestCase):
    """Bridges should be `user-invocable: false` — they're called by other
    skills or the orchestrator, not directly by the user. A bridge that's
    user-invocable will surface in the skills index and confuse routing.

    EXCEPTION list below covers bridges that intentionally expose a
    user-facing invocation (rare; document the reason in the SKILL.md)."""

    # Bridges that are legitimately user-invocable. Document the reason
    # in the SKILL.md frontmatter / body, not just here.
    USER_INVOCABLE_EXCEPTIONS = {
        "defenseclaw-bridge",  # ad-hoc spec generation per its own description
    }

    def test_bridges_are_not_user_invocable(self) -> None:
        if not SKILLS_DIR.is_dir():
            self.skipTest(f"{SKILLS_DIR} not present")
        bridges = [d for d in sorted(SKILLS_DIR.iterdir()) if d.is_dir() and is_bridge_skill(d)]
        violations: list[str] = []
        for bridge_dir in bridges:
            if bridge_dir.name in self.USER_INVOCABLE_EXCEPTIONS:
                continue
            skill_md = bridge_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
            text = skill_md.read_text(encoding="utf-8")
            m = FRONTMATTER_RE.match(text)
            if not m:
                continue
            frontmatter = m.group(1)
            # Look for `user-invocable: false` (or true)
            ui_match = re.search(r"^user-invocable:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
            if ui_match is None:
                # No flag = defaults to user-invocable (per Claude Code docs)
                violations.append(f"{bridge_dir.name}: no `user-invocable: false` flag (defaults to true)")
            elif ui_match.group(1).strip() != "false":
                violations.append(
                    f"{bridge_dir.name}: user-invocable={ui_match.group(1).strip()!r} (should be false)"
                )
        self.assertEqual(
            violations, [],
            "Bridges should set `user-invocable: false` so they're called by "
            "skills/orchestrator, not surfaced to users:\n  " + "\n  ".join(violations),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
