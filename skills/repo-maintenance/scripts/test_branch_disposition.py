#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for branch_disposition.py on real throwaway git repos (GIT_* env stripped).

Run: python3 -m pytest -q skills/repo-maintenance/scripts/test_branch_disposition.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import branch_disposition as bd  # noqa: E402

ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
ENV.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})


class Repo:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.write("a.txt", "one\n")
        self.commit("base")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True, env=ENV).stdout.strip()

    def write(self, rel: str, text: str) -> None:
        (self.root / rel).write_text(text)

    def commit(self, msg: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def advance_main(self, tag: str) -> None:
        """Move main past the fork point so a cherry-pick makes a NEW commit."""
        self.write(f"main-{tag}.txt", tag + "\n")
        self.commit(f"main {tag}")

    def branch_with(self, name: str, rel: str, text: str) -> str:
        self.git("checkout", "-q", "-b", name, "main")
        self.write(rel, text)
        head = self.commit(f"{name} change")
        self.git("checkout", "-q", "main")
        return head

    def exists(self, ref: str) -> bool:
        return subprocess.run(["git", "-C", str(self.root), "rev-parse", "-q", "--verify", ref],
                              capture_output=True, env=ENV).returncode == 0


def dispositions(out: dict) -> dict:
    return {b["name"]: b for b in out["branches"]}


class TestBranchDisposition(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self._tmp.name) / "r")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_classifies_each_case(self) -> None:
        r = self.repo
        r.branch_with("merged", "m.txt", "m\n")
        r.git("merge", "-q", "--ff-only", "merged")
        picked = r.branch_with("picked", "p.txt", "p\n")
        r.advance_main("x")
        r.git("cherry-pick", picked)
        r.write("p.txt", "p edited later on main\n")
        r.commit("main edits the picked lines")
        r.branch_with("squashed", "s.txt", "s1\n")
        r.git("checkout", "-q", "squashed")
        r.write("s.txt", "s2\n")
        r.commit("second")  # two commits -> one squash on main: cherry sees '+', merge-tree sees nothing new
        r.git("checkout", "-q", "main")
        r.write("s.txt", "s2\n")
        r.commit("squash of squashed")
        r.branch_with("unique", "u.txt", "u\n")
        r.branch_with("oc/abc123", "o.txt", "o\n")
        r.branch_with("release/1.0", "rel.txt", "r\n")
        r.branch_with("busy", "b.txt", "b\n")
        r.git("worktree", "add", "-q", str(Path(self._tmp.name) / "wt"), "busy")

        d = dispositions(bd.run(r.root))
        self.assertEqual(d["merged"]["disposition"], "merged")
        self.assertEqual(d["picked"]["disposition"], "patch-equivalent")
        self.assertEqual(d["squashed"]["disposition"], "content-on-main")
        self.assertEqual(d["unique"]["disposition"], "needs-decision")
        self.assertTrue(d["unique"]["commits"])
        self.assertIn("1 file changed", d["unique"]["diffstat"])
        self.assertEqual(d["oc/abc123"]["disposition"], "skipped")
        self.assertEqual(d["release/1.0"]["disposition"], "skipped")
        self.assertEqual(d["busy"]["disposition"], "skipped")
        self.assertEqual(d["main"]["disposition"], "skipped")

    def test_apply_archives_then_deletes_only_resolvable(self) -> None:
        r = self.repo
        picked = r.branch_with("picked", "p.txt", "p\n")
        r.advance_main("x")
        r.git("cherry-pick", picked)
        r.write("p.txt", "p edited later on main\n")
        r.commit("main edits the picked lines")
        unique = r.branch_with("unique", "u.txt", "u\n")
        r.branch_with("busy", "b.txt", "b\n")
        r.git("worktree", "add", "-q", str(Path(self._tmp.name) / "wt"), "busy")

        d = dispositions(bd.run(r.root, apply=True, today="2026-10-22"))
        tag = "refs/tags/archive/pre-closeout-2026-10-22/picked"
        self.assertEqual(d["picked"]["action"], "deleted")
        self.assertFalse(r.exists("refs/heads/picked"))
        self.assertEqual(r.git("rev-parse", f"{tag}^{{commit}}"), picked, "archive tag keeps the head")
        self.assertEqual(r.git("cat-file", "-t", tag), "tag", "annotated tag")
        self.assertEqual(r.git("rev-parse", "refs/heads/unique"), unique, "unique content never deleted")
        self.assertTrue(r.exists("refs/heads/busy"), "checked-out branch never deleted")
        self.assertNotIn("action", d["unique"])

    def test_branch_that_moved_after_classification_survives(self) -> None:
        r = self.repo
        picked = r.branch_with("picked", "p.txt", "p\n")
        r.advance_main("x")
        r.git("cherry-pick", picked)
        r.write("p.txt", "p edited later on main\n")
        r.commit("main edits the picked lines")
        item = bd.classify(r.root, "main", "picked", picked, set())
        self.assertEqual(item["disposition"], "patch-equivalent")
        r.git("checkout", "-q", "picked")
        r.write("late.txt", "peer work\n")
        late = r.commit("peer commit after classification")
        r.git("checkout", "-q", "main")
        out = bd.resolve(r.root, item, "2026-10-22")
        self.assertTrue(out["action"].startswith("kept"), out["action"])
        self.assertEqual(r.git("rev-parse", "refs/heads/picked"), late)

    def test_checked_out_after_classification_survives(self) -> None:
        r = self.repo
        picked = r.branch_with("picked", "p.txt", "p\n")
        r.advance_main("x")
        r.git("cherry-pick", picked)
        r.write("p.txt", "p edited later on main\n")
        r.commit("main edits the picked lines")
        item = bd.classify(r.root, "main", "picked", picked, set())
        r.git("worktree", "add", "-q", str(Path(self._tmp.name) / "wt2"), "picked")
        out = bd.resolve(r.root, item, "2026-10-22")
        self.assertEqual(out["action"], "kept: checked out since classification")
        self.assertTrue(r.exists("refs/heads/picked"))

    def test_checkout_between_precheck_and_delete_survives(self) -> None:
        # Codex review r2 #10: the worktree check must run inside the delete itself.
        r = self.repo
        picked = r.branch_with("picked", "p.txt", "p\n")
        r.advance_main("x")
        r.git("cherry-pick", picked)
        item = bd.classify(r.root, "main", "picked", picked, set())
        real, n = bd.checked_out_branches, {"calls": 0}

        def checkout_after_first_look(repo):
            n["calls"] += 1
            if n["calls"] == 1:  # resolve()'s pre-check sees nothing, then a session checks it out
                r.git("worktree", "add", "-q", str(Path(self._tmp.name) / "late"), "picked")
                return set()
            return real(repo)
        bd.checked_out_branches = checkout_after_first_look
        try:
            out = bd.resolve(r.root, item, "2026-10-22")
        finally:
            bd.checked_out_branches = real
        self.assertEqual(out["action"], "kept: checked out since classification")
        self.assertTrue(r.exists("refs/heads/picked"))

    def test_checkout_after_locked_check_restores_the_ref(self) -> None:
        # Codex review r3 #2/#6: a ref lock does not block checkout; a checkout landing
        # between the locked check and the commit must not leave the branch deleted.
        r = self.repo
        picked = r.branch_with("picked", "p.txt", "p\n")
        r.advance_main("x")
        r.git("cherry-pick", picked)
        item = bd.classify(r.root, "main", "picked", picked, set())
        real, n = bd.checked_out_branches, {"calls": 0}

        def checkout_after_locked_check(repo):
            n["calls"] += 1
            if n["calls"] == 2:  # the check made under the ref lock sees nothing...
                return set()
            if n["calls"] == 3:  # ...and the checkout lands before the post-commit check
                subprocess.run(["git", "-C", str(r.root), "symbolic-ref", "HEAD", "refs/heads/picked"],
                               check=True, env=ENV)
            return real(repo)
        bd.checked_out_branches = checkout_after_locked_check
        try:
            out = bd.resolve(r.root, item, "2026-10-22")
        finally:
            bd.checked_out_branches = real
        self.assertEqual(out["action"], "kept: checked out during delete; ref restored")
        self.assertEqual(r.git("rev-parse", "refs/heads/picked"), picked)

    def test_branch_only_merge_commit_is_not_patch_equivalent(self) -> None:
        # Codex review r3 #1: git cherry ignores merges, so a branch-only merge with its
        # own conflict resolution has no equivalence proof.
        r = self.repo
        r.branch_with("left", "c.txt", "left\n")
        r.branch_with("right", "c.txt", "right\n")
        r.git("checkout", "-q", "-b", "resolved", "left")
        subprocess.run(["git", "-C", str(r.root), "merge", "-q", "right"], env=ENV, capture_output=True)
        r.write("c.txt", "branch resolution\n")
        r.commit("merge right into left with a unique resolution")
        r.git("checkout", "-q", "main")
        r.git("merge", "-q", "--no-ff", "-m", "left", "left")
        subprocess.run(["git", "-C", str(r.root), "merge", "-q", "right"], env=ENV, capture_output=True)
        r.write("c.txt", "main resolution\n")
        r.commit("merge right into main with a different resolution")
        d = dispositions(bd.run(r.root))
        self.assertEqual(d["resolved"]["disposition"], "needs-decision")

    def test_tag_failure_means_no_delete(self) -> None:
        # Codex review r1 #13: archival must precede deletion; a failed tag keeps the branch.
        r = self.repo
        picked = r.branch_with("picked", "p.txt", "p\n")
        r.advance_main("x")
        r.git("cherry-pick", picked)
        item = bd.classify(r.root, "main", "picked", picked, set())
        self.assertIn(item["disposition"], bd.RESOLVABLE)
        real_git, calls = bd.git, []

        def failing_tag(repo, *args, check=True):
            calls.append(args[0])
            if args[0] == "tag":
                raise RuntimeError("tag refused")
            return real_git(repo, *args, check=check)
        bd.git = failing_tag
        try:
            with self.assertRaises(RuntimeError):
                bd.resolve(r.root, item, "2026-10-22")
        finally:
            bd.git = real_git
        self.assertEqual(calls.count("tag"), 1)
        self.assertEqual(calls[-1], "tag", "nothing runs after the failed tag")
        self.assertEqual(r.git("rev-parse", "refs/heads/picked"), picked)

    def test_merge_tree_error_is_skipped_not_resolved(self) -> None:
        # Codex review r1 #10: a git error is not evidence of redundancy.
        r = self.repo
        picked = r.branch_with("picked", "p.txt", "p\n")
        r.advance_main("x")
        r.git("cherry-pick", picked)
        real_git = bd.git

        def broken_merge_tree(repo, *args, check=True):
            if args[0] == "merge-tree":
                return subprocess.CompletedProcess(args, 129, "", "unknown option")
            return real_git(repo, *args, check=check)
        bd.git = broken_merge_tree
        try:
            item = bd.classify(r.root, "main", "picked", picked, set())
        finally:
            bd.git = real_git
        self.assertEqual(item["disposition"], "skipped")
        self.assertIn("merge-tree failed", item["reason"])

    def test_reverted_on_main_is_not_content_on_main(self) -> None:
        r = self.repo
        head = r.branch_with("feature", "f.txt", "f\n")
        r.git("merge", "-q", "--squash", "feature")
        r.commit("squash feature")
        r.git("revert", "--no-edit", "HEAD")
        d = dispositions(bd.run(r.root))
        self.assertEqual(d["feature"]["disposition"], "needs-decision", head)

    def test_inherited_git_dir_is_ignored(self) -> None:
        other = Repo(Path(self._tmp.name) / "other")
        other.branch_with("x", "x.txt", "x\n")
        os.environ["GIT_DIR"] = str(other.root / ".git")
        try:
            names = {b["name"] for b in bd.run(self.repo.root)["branches"]}
        finally:
            del os.environ["GIT_DIR"]
        self.assertEqual(names, {"main"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
