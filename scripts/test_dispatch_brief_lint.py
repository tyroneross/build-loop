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


if __name__ == "__main__":
    unittest.main()
