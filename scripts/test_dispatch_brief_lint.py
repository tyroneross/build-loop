#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Every case here is a real dispatch failure from 2026-09-04, or its fix."""
from __future__ import annotations

import pathlib
import tempfile
import unittest

import dispatch_brief_lint as lint

GOOD = """---
goal: navgator CI is green on all three jobs on main
max_iterations: 5
report_primary: rally
report_backup: .build-loop/followup/
durable: build-loop-memory/projects/navgator/handoffs/
---
body
"""


def _brief(fm: str) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp())
    p = d / "brief.md"
    p.write_text(fm, encoding="utf-8")
    return p


class ABriefThatCanReport(unittest.TestCase):
    def test_the_worked_example_passes(self) -> None:
        self.assertEqual(lint.check(_brief(GOOD)), [])

    def test_the_shipped_template_is_not_itself_a_valid_brief(self) -> None:
        """The template carries <placeholders>; it must fail until filled in.

        A template that lints clean is one a dispatcher can copy unedited.
        """
        tpl = pathlib.Path(__file__).resolve().parent.parent / "templates" / "dispatch-brief.md"
        if tpl.is_file():
            self.assertNotEqual(lint.check(tpl), [], "the blank template must not pass")


class TheOmissionsThatCostTime(unittest.TestCase):
    def test_absent_durable_fails_but_the_word_none_passes(self) -> None:
        """The asymmetry IS the feature.

        A handoff was written only to .build-loop/ (gitignored) and would have
        died with the machine. `none` records a decision; absence records that
        nobody decided.
        """
        absent = GOOD.replace("durable: build-loop-memory/projects/navgator/handoffs/\n", "")
        self.assertTrue(any("durable" in p for p in lint.check(_brief(absent))))
        explicit = GOOD.replace(
            "durable: build-loop-memory/projects/navgator/handoffs/", "durable: none")
        self.assertEqual(lint.check(_brief(explicit)), [])

    def test_a_durable_path_inside_the_gitignored_tree_fails(self) -> None:
        bad = GOOD.replace(
            "durable: build-loop-memory/projects/navgator/handoffs/",
            "durable: .build-loop/handoffs/")
        self.assertTrue(any(".build-loop" in p and "GITIGNORED" in p
                            for p in lint.check(_brief(bad))))

    def test_a_goal_stated_as_a_count_fails(self) -> None:
        bad = GOOD.replace("goal: navgator CI is green on all three jobs on main",
                           "goal: run the suite 5 times")
        self.assertTrue(any("BOUND, not" in p for p in lint.check(_brief(bad))))

    def test_a_missing_iteration_bound_fails(self) -> None:
        """The two-day watcher had a condition and no cap."""
        bad = GOOD.replace("max_iterations: 5\n", "")
        self.assertTrue(any("max_iterations" in p for p in lint.check(_brief(bad))))

    def test_a_zero_iteration_bound_fails(self) -> None:
        bad = GOOD.replace("max_iterations: 5", "max_iterations: 0")
        self.assertTrue(any("positive integer" in p for p in lint.check(_brief(bad))))

    def test_a_backup_nobody_reads_fails(self) -> None:
        bad = GOOD.replace("report_backup: .build-loop/followup/",
                           "report_backup: /tmp/my-notes/")
        self.assertTrue(any("no agent is known to read" in p for p in lint.check(_brief(bad))))

    def test_an_unedited_placeholder_fails(self) -> None:
        bad = GOOD.replace("report_primary: rally", "report_primary: <rally | commit>")
        self.assertTrue(any("placeholder" in p for p in lint.check(_brief(bad))))

    def test_no_frontmatter_at_all_fails(self) -> None:
        self.assertTrue(lint.check(_brief("just prose, no contract\n")))


class ForbiddenActionsForWriteCapableBriefs(unittest.TestCase):
    """A brief that can Write/Edit/Bash needs to say what it must not do."""

    def test_read_only_brief_without_forbidden_passes(self) -> None:
        self.assertEqual(lint.check(_brief(GOOD)), [])

    def test_write_capable_via_tools_field_without_forbidden_fails(self) -> None:
        bad = GOOD.replace("durable: build-loop-memory/projects/navgator/handoffs/\n",
                            "durable: build-loop-memory/projects/navgator/handoffs/\n"
                            "tools: Read, Write, Bash\n")
        problems = lint.check(_brief(bad))
        self.assertTrue(any("forbidden" in p for p in problems))

    def test_write_capable_via_body_text_without_forbidden_fails(self) -> None:
        bad = GOOD + "\nYou may commit your changes when the goal is met.\n"
        problems = lint.check(_brief(bad))
        self.assertTrue(any("forbidden" in p for p in problems))

    def test_write_capable_with_forbidden_default_passes(self) -> None:
        good = GOOD.replace(
            "durable: build-loop-memory/projects/navgator/handoffs/\n",
            "durable: build-loop-memory/projects/navgator/handoffs/\n"
            "forbidden: default\n"
            "tools: Read, Write, Bash\n",
        )
        self.assertEqual(lint.check(_brief(good)), [])

    def test_custom_forbidden_missing_one_prohibition_names_it(self) -> None:
        custom = (
            "no persistence beyond the task; no owner identity in outbound "
            "requests; never acknowledge or skip a safety gate"
            # deliberately omits "commit only your own files"
        )
        bad = GOOD.replace(
            "durable: build-loop-memory/projects/navgator/handoffs/\n",
            f"durable: build-loop-memory/projects/navgator/handoffs/\n"
            f"forbidden: {custom}\n"
            f"tools: Read, Write, Bash\n",
        )
        problems = lint.check(_brief(bad))
        self.assertTrue(any("commit only your own" in p for p in problems))

    def test_custom_forbidden_covering_all_four_passes(self) -> None:
        custom = (
            "no persistence beyond the task (no LaunchAgents, registries, "
            "cron jobs); never include owner identity/email in outbound "
            "requests; never acknowledge, override, or skip a safety gate; "
            "commit only your own files"
        )
        good = GOOD.replace(
            "durable: build-loop-memory/projects/navgator/handoffs/\n",
            f"durable: build-loop-memory/projects/navgator/handoffs/\n"
            f"forbidden: {custom}\n"
            f"tools: Read, Write, Bash\n",
        )
        self.assertEqual(lint.check(_brief(good)), [])

    def test_custom_value_that_permits_the_act_fails(self) -> None:
        """Auditor f6: naming a keyword is not prohibiting it."""
        bad = GOOD.replace(
            "durable: build-loop-memory/projects/navgator/handoffs/\n",
            "durable: build-loop-memory/projects/navgator/handoffs/\n"
            "forbidden: we skip nothing; persist nothing; email ok; your own\n"
            "tools: Read, Write, Bash\n",
        )
        problems = lint.check(_brief(bad))
        self.assertTrue(any("owner identity" in p for p in problems), problems)

    def test_template_default_line_passes_when_write_capable(self) -> None:
        """Auditor f4: a brief copied from the template's frontmatter must lint clean."""
        import re as _re

        tpl = (pathlib.Path(__file__).resolve().parents[1] / "templates" / "dispatch-brief.md").read_text()
        line = next(l for l in tpl.splitlines() if l.startswith("forbidden:"))
        self.assertEqual(line.split(":", 1)[1].strip(), "default")
        good = GOOD.replace(
            "durable: build-loop-memory/projects/navgator/handoffs/\n",
            f"durable: build-loop-memory/projects/navgator/handoffs/\n{line}\ntools: Write\n",
        )
        self.assertEqual(lint.check(_brief(good)), [])

    def test_forbidden_placeholder_fails(self) -> None:
        bad = GOOD.replace(
            "durable: build-loop-memory/projects/navgator/handoffs/\n",
            "durable: build-loop-memory/projects/navgator/handoffs/\n"
            "forbidden: <default | your own list>\n"
            "write_capable: true\n",
        )
        problems = lint.check(_brief(bad))
        self.assertTrue(any("placeholder" in p for p in problems))


class CodexExecStdinHang(unittest.TestCase):
    """`codex exec` launched non-interactively without stdin closed hangs."""

    def test_codex_exec_without_dev_null_fails(self) -> None:
        bad = GOOD.replace(
            "durable: build-loop-memory/projects/navgator/handoffs/\n",
            "durable: build-loop-memory/projects/navgator/handoffs/\n"
            "forbidden: default\n"
            "tools: Read, Write, Bash\n",
        ) + "\nRun: codex exec 'do the thing'\n"
        problems = lint.check(_brief(bad))
        self.assertTrue(any("codex exec" in p and "/dev/null" in p for p in problems))

    def test_codex_exec_with_dev_null_passes(self) -> None:
        good = GOOD.replace(
            "durable: build-loop-memory/projects/navgator/handoffs/\n",
            "durable: build-loop-memory/projects/navgator/handoffs/\n"
            "forbidden: default\n"
            "tools: Read, Write, Bash\n",
        ) + "\nRun: codex exec 'do the thing' < /dev/null\n"
        self.assertEqual(lint.check(_brief(good)), [])

    def test_codex_exec_with_piped_or_heredoc_stdin_passes(self) -> None:
        for cmd in ("cat brief.md | codex exec -", "codex exec - <<'EOF'", "codex exec - < brief.md"):
            with self.subTest(cmd=cmd):
                good = GOOD.replace(
                    "durable: build-loop-memory/projects/navgator/handoffs/\n",
                    "durable: build-loop-memory/projects/navgator/handoffs/\n"
                    "forbidden: default\ntools: Read, Write, Bash\n",
                ) + f"\nRun: {cmd}\n"
                self.assertEqual(lint.check(_brief(good)), [])

    def test_codex_exec_on_read_only_brief_is_not_checked(self) -> None:
        """The codex-exec stdin check only fires once a brief is write-capable."""
        read_only = GOOD + "\nRun: codex exec 'do the thing'\n"
        problems = lint.check(_brief(read_only))
        self.assertFalse(any("codex exec" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
