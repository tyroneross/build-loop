#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
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

Bridges in build-loop (post-fold, 2026-07):
  - prd-bridge             → docs/prd-*.md (PRD-grounded planning)
  - api-registry-bridge    → api-registry (api discovery)
  - defenseclaw-bridge     → DefenseClaw (spec / threat-model generation)
  - ibr-bridge             → IBR (UI visual verification)

logging-tracer-bridge was folded into the logging-tracer skill as an internal
"Coding Debugger escalation" hop (pool-consolidation Inc 4); its preflight is
now covered by test_non_bridge_escalation_hops_have_preflight (see
NON_BRIDGE_PREFLIGHT_SKILLS below).

If a new bridge is added without a pre-flight, this test fails with a
hint pointing at the missing skill body.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SKILLS_DIR = REPO_ROOT / "skills"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
# Single source of truth for "is this skill allowed to be public?" — see
# UserInvocableFlagTests below.
from test_agent_surface_policy import surface_violation  # noqa: E402

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

    # Non-bridge skills that absorbed a former *-bridge as an internal
    # escalation hop must keep the SAME availability preflight — otherwise the
    # graceful-degradation contract silently leaves suffix-based test coverage.
    # logging-tracer absorbed logging-tracer-bridge (2026-07, pool-consolidation
    # Inc 4); its "Extended capability — Coding Debugger escalation" section must
    # keep the availablePlugins preflight so it no-ops when standalone is absent.
    NON_BRIDGE_PREFLIGHT_SKILLS = {"logging-tracer"}

    def test_non_bridge_escalation_hops_have_preflight(self) -> None:
        if not SKILLS_DIR.is_dir():
            self.skipTest(f"{SKILLS_DIR} not present")
        missing: list[str] = []
        for name in sorted(self.NON_BRIDGE_PREFLIGHT_SKILLS):
            skill_md = SKILLS_DIR / name / "SKILL.md"
            if not skill_md.is_file():
                missing.append(f"{name}: SKILL.md missing")
            elif not has_preflight(skill_md):
                missing.append(
                    f"{name}: no preflight pattern — folded escalation hop lost "
                    "its graceful-degradation check"
                )
        self.assertEqual(
            missing, [],
            "Non-bridge escalation hops without an availability pre-flight:\n  "
            + "\n  ".join(missing),
        )


class UserInvocableFlagTests(unittest.TestCase):
    """Bridges are `user-invocable: false` — they're called by other skills or
    the orchestrator, not directly by the user. A user-invocable bridge surfaces
    in the skills index and confuses routing.

    This is the SAME invariant `test_agent_surface_policy.py` applies to every
    skill, so it delegates to that module's `surface_violation` rather than
    restating the rule. The old local `USER_INVOCABLE_EXCEPTIONS` allowlist was
    deleted for exactly the reason the hardcoded `CLAUDE_PUBLIC_ENTRYPOINTS` set
    was: a second copy of the policy drifts. Its one entry
    (`defenseclaw-bridge`) had already gone stale — that skill has shipped
    `user-invocable: false` for some time, so the exemption exempted nothing.
    A bridge that genuinely needs exposure declares `public-justification:` in
    its own frontmatter; no list here needs editing.
    """

    def test_bridges_are_not_user_invocable(self) -> None:
        if not SKILLS_DIR.is_dir():
            self.skipTest(f"{SKILLS_DIR} not present")
        bridges = [d for d in sorted(SKILLS_DIR.iterdir()) if d.is_dir() and is_bridge_skill(d)]
        self.assertNotEqual(bridges, [], "no bridge skills found — the scan is vacuous")
        violations: list[str] = []
        for bridge_dir in bridges:
            skill_md = bridge_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
            m = FRONTMATTER_RE.match(skill_md.read_text(encoding="utf-8"))
            if not m:
                continue
            violation = surface_violation(bridge_dir.name, m.group(1))
            if violation is not None:
                violations.append(violation)
        self.assertEqual(
            violations, [],
            "Bridges must set `user-invocable: false` so they're called by "
            "skills/orchestrator, not surfaced to users:\n  " + "\n  ".join(violations),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
