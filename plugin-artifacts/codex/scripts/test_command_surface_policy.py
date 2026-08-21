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
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DIR = REPO_ROOT / "commands"

# Human-facing commands. Adding one REQUIRES a comment justifying why
# plain-language routing through `run` cannot cover it.
#
# `run` — every build/debug/research/optimize/plan intent, routed by plain language.
#
# `feedback` — added 2026-08-18 (c8bee50) to file a GitHub issue against
#   tyroneross/build-loop; the manifest carries no contact address by design.
#   `run` cannot cover it: run dispatches the build orchestrator against the
#   USER'S repo, so "file a bug about build-loop" would start a build loop to
#   write an issue. Reporting a defect in the tool is not work on the tree the
#   tool is pointed at, and it must stay reachable when the orchestrator is the
#   thing that is broken — which is exactly when a user needs it.
PUBLIC_COMMANDS = {"run", "feedback"}


def _justification_block() -> str:
    """The comment block directly above the PUBLIC_COMMANDS assignment.

    Read from source on purpose. The policy's requirement is that a rationale is
    WRITTEN DOWN, and a rationale that lives only in a commit message is invisible
    to the next person who edits this line.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    head = source.split("PUBLIC_COMMANDS =")[0]
    block: list[str] = []
    for line in reversed(head.splitlines()):
        if line.startswith("#"):
            block.append(line)
        elif not line.strip():
            continue
        else:
            break
    return "\n".join(reversed(block))


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

    def test_every_public_command_carries_a_written_justification(self):
        """The allowlist's own rule is 'add it here WITH a comment justifying why
        plain-language routing through run cannot cover it.' Nothing enforced that,
        so a command could be added with no rationale and stay green — the exact
        partial-oracle gap recorded in the audit verdict for 06ea320."""
        block = _justification_block()
        for command in sorted(PUBLIC_COMMANDS):
            with self.subTest(command=command):
                self.assertIn(
                    f"`{command}`", block,
                    f"{command!r} is in PUBLIC_COMMANDS but is not named in the "
                    f"justification comment above it. State why `run` cannot cover it.",
                )

    def test_justifications_say_something(self):
        """A bare name in the comment is not a justification. Require enough prose
        after the name to constitute a reason, so the check cannot be satisfied by
        adding `foo` — to the block."""
        block = _justification_block()
        for command in sorted(PUBLIC_COMMANDS):
            with self.subTest(command=command):
                after = block.split(f"`{command}`", 1)[1]
                # Stop at the next command bullet so each rationale is measured alone.
                for other in PUBLIC_COMMANDS - {command}:
                    after = after.split(f"`{other}`")[0]
                prose = " ".join(after.replace("#", " ").split())
                self.assertGreaterEqual(
                    len(prose), 40,
                    f"justification for {command!r} is {len(prose)} chars — too short to be a "
                    f"reason. Say what run cannot cover.",
                )



if __name__ == "__main__":
    unittest.main()
