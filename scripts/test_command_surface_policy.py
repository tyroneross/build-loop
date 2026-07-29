# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Command-surface policy: `/build-loop:run` is the ONLY human-facing command.

Every former mode/utility command (debug, optimize, research, test, assess,
self-improve, promote-experiment, verify-plan, start-prd, setup-memory,
knowledge review mode, compose-handoff, rally-point, debugger*) is reached by INTENT
through `/build-loop:run` + plain language, and invoked internally as a skill —
never as a separate slash-command. This test locks that surface so a stray
command file can't silently re-clutter the human palette.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DIR = REPO_ROOT / "commands"
PUBLIC_ROUTING_DOCS = (
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "references" / "halt-and-ask-protocol.md",
    REPO_ROOT / "references" / "implementer-envelope-schema.md",
    REPO_ROOT / "references" / "iterate-protocol.md",
    REPO_ROOT / "references" / "keep-going-policy.md",
    REPO_ROOT / "references" / "m-series-protocol.md",
    REPO_ROOT / "references" / "phase-gate-checklist.md",
    REPO_ROOT / "references" / "resume-protocol.md",
    REPO_ROOT / "references" / "trigger-rules.md",
    REPO_ROOT / "skills" / "build-loop" / "SKILL.md",
    REPO_ROOT / "skills" / "build-loop" / "references" / "autonomous-and-per-commit-modes.md",
    REPO_ROOT / "skills" / "build-loop" / "references" / "codex-subagents.md",
    REPO_ROOT / "skills" / "build-loop" / "references" / "phase-1-assess.md",
)
LEGACY_INVOCATION_FLAG_PATTERNS = (
    re.compile(r"--long(?![-\w])"),
    re.compile(r"--budget(?![-\w])"),
    re.compile(r"--autonomous(?:=false)?(?![-\w])"),
    re.compile(r"--resume(?![-\w])"),
    re.compile(r"--per-commit(?![-\w])"),
    re.compile(r"--no-per-commit(?![-\w])"),
    re.compile(r"--parallel(?![-\w])"),
)

# The single human-facing command. If build-loop ever needs a second genuinely
# human-only command, add it here WITH a comment justifying why plain-language
# routing through `run` cannot cover it.
PUBLIC_COMMANDS = {"run"}


class CommandSurfaceTests(unittest.TestCase):
    def test_only_run_is_human_facing(self):
        found = {p.stem for p in COMMANDS_DIR.glob("*.md")}
        self.assertEqual(
            found,
            PUBLIC_COMMANDS,
            f"commands/ must expose only {sorted(PUBLIC_COMMANDS)} — one human command; all modes "
            f"route via /build-loop:run + plain language (skills/build-loop/SKILL.md §Routing). "
            f"Found: {sorted(found)}. Retire the extra command file(s) or route the intent through run.",
        )

    def test_run_accepts_only_a_plain_language_goal(self):
        text = (COMMANDS_DIR / "run.md").read_text(encoding="utf-8")
        match = re.search(r'^argument-hint:\s*"([^"]+)"\s*$', text, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "[goal description]")
        self.assertNotIn("--", match.group(1))

    def test_run_delegates_directly_to_the_build_loop_skill(self):
        text = (COMMANDS_DIR / "run.md").read_text(encoding="utf-8")
        self.assertIn("Load the `build-loop:build-loop` skill.", text)

    def test_public_routing_uses_plain_language_instead_of_mode_flags(self):
        for path in PUBLIC_ROUTING_DOCS:
            text = path.read_text(encoding="utf-8")
            for pattern in LEGACY_INVOCATION_FLAG_PATTERNS:
                self.assertIsNone(
                    pattern.search(text),
                    f"{path}: advertises retired invocation flag matching {pattern.pattern}",
                )


if __name__ == "__main__":
    unittest.main()
