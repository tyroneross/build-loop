#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/hooks/git_command_classifier — segment-wise git commit/push detection.

Covers the two false-fire classes that motivated the fix (repo paths / prose / heredoc TEXT
containing example git commands) and the true-fire classes (bare / compound / piped / worktree
`git -C` / tab-spaced pushes) — replacing the coarse `*git*push*` / `*commit*` substring globs.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import git_command_classifier as gcc  # noqa: E402

CLASSIFIER = HERE / "git_command_classifier.py"


class ClassifyCommandTests(unittest.TestCase):
    # ---- FALSE-FIRE classes: must NOT trigger ----

    def test_a_rally_say_with_gitignore_path_and_pushed_prose(self) -> None:
        """rally say with a .gitignore path + 'pushed' in the subject → argv[0] is not git."""
        cmd = 'rally say claim --tool claude_code --subject "pushed auth fix" --path .gitignore'
        self.assertEqual(gcc.classify_command(cmd), set())

    def test_a_repo_path_under_git_folder(self) -> None:
        """A path containing 'git' and prose 'push' must not match."""
        cmd = 'ls /Users/x/dev/git-folder/proj && echo "remember to push later"'
        self.assertEqual(gcc.classify_command(cmd), set())

    def test_b_heredoc_body_with_example_git_commands(self) -> None:
        """A heredoc whose TEXT contains 'git commit && git push' must NOT trigger."""
        cmd = (
            "python3 - <<'PY'\n"
            "# example workflow: git commit -m x && git push origin main\n"
            'print("hello")\n'
            "PY"
        )
        self.assertEqual(gcc.classify_command(cmd), set())

    def test_b_heredoc_unquoted_delimiter(self) -> None:
        cmd = "cat <<EOF\ngit push origin main\nEOF"
        self.assertEqual(gcc.classify_command(cmd), set())

    def test_backlog_title_prose(self) -> None:
        cmd = 'echo "pre_bash_dispatch push-scan trigger: replace glob (git push)"'
        self.assertEqual(gcc.classify_command(cmd), set())

    # ---- TRUE-FIRE classes: must trigger ----

    def test_c_bare_push(self) -> None:
        self.assertEqual(gcc.classify_command("git push"), {"push"})

    def test_d_commit_then_push_compound(self) -> None:
        self.assertEqual(
            gcc.classify_command("git commit -m x && git push"), {"commit", "push"}
        )

    def test_e_piped_push(self) -> None:
        self.assertEqual(gcc.classify_command("git push 2>&1 | tail -1"), {"push"})

    def test_worktree_dash_c_push(self) -> None:
        self.assertEqual(
            gcc.classify_command("git -C /Users/x/wt push --mirror backup"), {"push"}
        )

    def test_tab_spaced_push(self) -> None:
        self.assertEqual(gcc.classify_command("git\tpush --mirror backup"), {"push"})

    def test_absolute_git_binary_path(self) -> None:
        self.assertEqual(gcc.classify_command("/usr/bin/git push origin main"), {"push"})

    def test_plain_commit(self) -> None:
        self.assertEqual(gcc.classify_command("git commit -m 'msg'"), {"commit"})

    def test_multiline_push_not_on_first_line(self) -> None:
        cmd = "git add -A\ngit status\ngit push --mirror backup"
        self.assertEqual(gcc.classify_command(cmd), {"push"})

    def test_real_push_before_heredoc_still_triggers(self) -> None:
        cmd = "git push && cat <<'PY'\ntext body\nPY"
        self.assertEqual(gcc.classify_command(cmd), {"push"})

    # ---- edge / conservatism ----

    def test_empty_command(self) -> None:
        self.assertEqual(gcc.classify_command(""), set())
        self.assertEqual(gcc.classify_command("   "), set())

    def test_unbalanced_quote_is_conservative(self) -> None:
        # Can't parse — must not silently drop a possible push.
        got = gcc.classify_command('git push "unterminated')
        self.assertIn("push", got)

    def test_non_git_subcommand_word(self) -> None:
        # `git log --oneline` mentions nothing of interest.
        self.assertEqual(gcc.classify_command("git log --oneline -3"), set())


class SubprocessRoundTripTests(unittest.TestCase):
    """Drive the classifier as the dispatcher does: event JSON on stdin → space-sep stdout."""

    def _run(self, command: str) -> str:
        event = json.dumps({"tool_input": {"command": command}, "cwd": "/tmp"})
        r = subprocess.run(
            [sys.executable, str(CLASSIFIER)],
            input=event, capture_output=True, text=True, check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_bare_push_stdout(self) -> None:
        self.assertEqual(self._run("git push"), "push")

    def test_compound_stdout(self) -> None:
        self.assertEqual(self._run("git commit -m x && git push"), "commit push")

    def test_heredoc_no_trigger_stdout(self) -> None:
        cmd = "python3 - <<'PY'\ngit push origin main\nPY"
        self.assertEqual(self._run(cmd), "")

    def test_rally_say_no_trigger_stdout(self) -> None:
        self.assertEqual(
            self._run('rally say --subject "pushed" --path .gitignore'), ""
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
