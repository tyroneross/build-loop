# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Command-surface policy: `/build-loop:run` is the ONLY human-facing command.

Every former mode/utility command (debug, optimize, research, test, assess,
self-improve, promote-experiment, verify-plan, start-prd, setup-memory,
review-knowledge, compose-handoff, rally-point, debugger*) is reached by INTENT
through `/build-loop:run` + plain language, and invoked internally as a skill —
never as a separate slash-command. This test locks that surface so a stray
command file can't silently re-clutter the human palette.
"""
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DIR = REPO_ROOT / "commands"

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


if __name__ == "__main__":
    unittest.main()
