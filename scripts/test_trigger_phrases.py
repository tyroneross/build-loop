#!/usr/bin/env python3
"""Tests that key skill descriptions cover their expected trigger phrases.

Stdlib only. Run: python3 test_trigger_phrases.py

A skill activates when the user's prompt contains words/phrases that match
its `description:` frontmatter field. If the description doesn't include
the right substrings, the right skill won't fire when it should.

This test asserts a curated set of (skill_name, expected_phrase) pairs —
adding a phrase here is the contract: "future builds must keep this
trigger phrase covered in the description, or update the test if the
trigger is intentionally retired."

Adding new pairs:
  Edit EXPECTED_TRIGGERS below. One pair per (skill, phrase) — keep
  phrases short (3-6 words) and lowercase. The check is case-insensitive
  substring match, so 'magic links' matches 'wire up magic links'.

Pattern borrowed from atomize-ai's trigger spec testing approach but
simplified to stdlib-only golden-list comparison.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SKILLS_DIR = REPO_ROOT / "skills"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
DESCRIPTION_RE = re.compile(r"^description:\s*(.+?)(?:\n[a-z_-]+:|\Z)", re.MULTILINE | re.DOTALL)

# Curated trigger contract. Each (skill, phrase) is a "if a user says
# this, the skill should fire" assertion. Keep phrases short and
# substring-matchable. To retire a trigger, remove the pair (don't
# silence the test).
EXPECTED_TRIGGERS: list[tuple[str, str]] = [
    # build-loop core
    ("build-loop", "build"),
    ("build-loop", "implement"),
    ("build-loop", "refactor"),
    ("build-loop", "migrate"),

    # authentication — multi-provider after the 2026-05-02 audit
    ("authentication", "google"),
    ("authentication", "supabase"),
    ("authentication", "magic link"),
    ("authentication", "resend"),
    ("authentication", "redirect_uri_mismatch"),
    ("authentication", "refresh_token"),

    # debugging-memory — memory-first workflow triggers
    ("debugging-memory", "debug"),
    ("debugging-memory", "fix"),
    ("debugging-memory", "investigate"),
    ("debugging-memory", "root cause"),

    # debug-loop — deeper investigation
    ("debug-loop", "deep iterative debugging"),
    ("debug-loop", "root cause analysis"),

    # logging-tracer — observability
    ("logging-tracer", "logging"),
    ("logging-tracer", "tracing"),
    ("logging-tracer", "observability"),

    # plan-verify — plan claim verification
    ("plan-verify", "plan"),
    ("plan-verify", "evidence"),

    # research — pre-decision research
    ("research", "research"),

    # optimize — autoresearch optimization
    ("optimize", "optimization"),
    ("optimize", "metric"),

    # self-improve — phase 6 learn
    ("self-improve", "recurring pattern"),
]

# Skills whose description must end with a sentence describing TRIGGER
# phrases (not just internal usage). Useful sanity check on any skill
# that's user-routed (not just internal coordinator).
SKILLS_REQUIRING_USER_TRIGGER_LANGUAGE = {
    "build-loop",
    "authentication",
    "debugging-memory",
    "debug-loop",
    "logging-tracer",
    "research",
    "optimize",
    "plan-verify",
}

MIN_DESCRIPTION_CHARS = 40  # short descriptions don't carry enough trigger surface
MAX_DESCRIPTION_CHARS = 1000  # very long ones bloat the skill index — soft warn


def read_description(skill_md: Path) -> str | None:
    text = skill_md.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    d = DESCRIPTION_RE.search(m.group(1))
    if not d:
        return None
    return d.group(1).strip().strip('"').strip("'")


def find_skill_md(skill_name: str) -> Path | None:
    candidate = SKILLS_DIR / skill_name / "SKILL.md"
    return candidate if candidate.is_file() else None


class ExpectedTriggersTests(unittest.TestCase):
    def test_every_expected_trigger_is_covered(self) -> None:
        if not SKILLS_DIR.is_dir():
            self.skipTest(f"{SKILLS_DIR} not present")
        misses: list[str] = []
        for skill_name, phrase in EXPECTED_TRIGGERS:
            skill_md = find_skill_md(skill_name)
            if skill_md is None:
                misses.append(f"{skill_name}: SKILL.md not found")
                continue
            desc = read_description(skill_md)
            if desc is None:
                misses.append(f"{skill_name}: no description in frontmatter")
                continue
            if phrase.lower() not in desc.lower():
                misses.append(f"{skill_name}: missing phrase {phrase!r}")
        self.assertEqual(
            misses, [],
            "Trigger-phrase coverage gaps:\n  " + "\n  ".join(misses)
            + "\n\nFix: extend the skill's `description:` to include the missing phrase, "
            "OR remove the pair from EXPECTED_TRIGGERS if the trigger is intentionally retired.",
        )


class DescriptionLengthTests(unittest.TestCase):
    def test_descriptions_meet_minimum(self) -> None:
        if not SKILLS_DIR.is_dir():
            self.skipTest(f"{SKILLS_DIR} not present")
        too_short: list[str] = []
        for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            desc = read_description(skill_md)
            if desc is None:
                continue
            if len(desc) < MIN_DESCRIPTION_CHARS:
                too_short.append(
                    f"{skill_md.relative_to(REPO_ROOT)}: {len(desc)} chars "
                    f"(need ≥{MIN_DESCRIPTION_CHARS})"
                )
        self.assertEqual(too_short, [], f"descriptions too short: {too_short}")

    def test_descriptions_under_max(self) -> None:
        if not SKILLS_DIR.is_dir():
            self.skipTest(f"{SKILLS_DIR} not present")
        too_long = [
            f"{p.relative_to(REPO_ROOT)}: {len(read_description(p) or '')} chars"
            for p in sorted(SKILLS_DIR.glob("*/SKILL.md"))
            if (read_description(p) or "") and len(read_description(p) or "") > MAX_DESCRIPTION_CHARS
        ]
        if too_long:
            self.skipTest(
                f"descriptions over {MAX_DESCRIPTION_CHARS} chars (informational, not blocking): "
                f"{too_long}"
            )


class FrontmatterParseTests(unittest.TestCase):
    def test_every_skill_has_description(self) -> None:
        if not SKILLS_DIR.is_dir():
            self.skipTest(f"{SKILLS_DIR} not present")
        missing = [
            str(p.relative_to(REPO_ROOT))
            for p in sorted(SKILLS_DIR.glob("*/SKILL.md"))
            if read_description(p) is None
        ]
        self.assertEqual(missing, [], f"skills without description: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
