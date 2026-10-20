"""Exercise merge_plan.py through its CLI against temporary Git repos."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("merge_plan.py")


class MergePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.test")
        self.commit("core.txt", "base\n", "base")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], text=True,
                              capture_output=True, check=True).stdout.strip()

    def commit(self, path, body, message):
        (self.repo / path).write_text(body)
        self.git("add", "--", path)
        self.git("commit", "-m", message)

    def branch(self, name, path, body=None, start="main"):
        self.git("checkout", "-b", name, start)
        self.commit(path, body or name + "\n", name)
        self.git("checkout", "main")

    def plan(self, *args):
        p = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.repo), "--json", *args],
                           text=True, capture_output=True, check=True)
        return json.loads(p.stdout)

    @staticmethod
    def sets(r):
        return [set(b["branches"]) for b in r["batches"]]

    def test_disjoint_branches_batch_together(self):
        for n in "abc":
            self.branch(n, f"{n}.txt")
        r = self.plan("a", "b", "c")
        self.assertEqual(self.sets(r), [{"a", "b", "c"}])
        self.assertEqual(r["branches"]["a"]["commits_ahead"], 1)
        self.assertEqual(r["branches"]["a"]["files"], ["a.txt"])
        self.assertEqual(r["branches"]["a"]["base_verdict"], "clean")

    def test_overlapping_files_split(self):
        self.branch("a", "shared.txt", "one\n")
        self.branch("b", "shared.txt", "one\ntwo\n")  # add/add, same path
        self.branch("c", "c.txt")
        r = self.plan("a", "b", "c")
        self.assertEqual(len(r["batches"]), 2)
        for s in self.sets(r):
            self.assertFalse({"a", "b"} <= s)

    def test_overlap_without_conflict_still_split(self):
        (self.repo / "big.txt").write_text("\n".join(map(str, range(40))) + "\n")
        self.git("add", "big.txt"); self.git("commit", "-m", "big")
        lines = (self.repo / "big.txt").read_text().splitlines()
        top = "\n".join(["X"] + lines[1:]) + "\n"
        bot = "\n".join(lines[:-1] + ["Y"]) + "\n"
        self.branch("a", "big.txt", top)
        self.branch("b", "big.txt", bot)
        r = self.plan("a", "b")
        pair = r["pairs"][0]
        self.assertEqual(pair["conflict"], "clean")
        self.assertEqual(pair["shared_files"], ["big.txt"])
        self.assertEqual(len(r["batches"]), 2)

    def test_pairwise_conflict_separated(self):
        self.commit("f.txt", "1\n2\n3\n", "f")
        self.branch("a", "f.txt", "1\nA\n3\n")
        self.branch("b", "f.txt", "1\nB\n3\n")
        r = self.plan("a", "b")
        self.assertEqual(r["pairs"][0]["conflict"], "conflict")
        self.assertEqual(len(r["batches"]), 2)
        self.assertEqual(r["needs_resolution"], [])

    def test_base_conflict_reported(self):
        self.commit("f.txt", "1\n2\n3\n", "f")
        self.branch("a", "f.txt", "1\nA\n3\n")
        self.commit("f.txt", "1\nM\n3\n", "main moves")
        self.branch("ok", "ok.txt")
        r = self.plan("a", "ok")
        self.assertEqual([n["branch"] for n in r["needs_resolution"]], ["a"])
        self.assertIn("conflicts with base", r["needs_resolution"][0]["reason"])
        self.assertEqual(self.sets(r), [{"ok"}])

    def test_ancestry_ordering(self):
        self.branch("a", "a.txt")
        self.branch("b", "b.txt", start="a")  # contains a's tip
        r = self.plan("b", "a")  # given in the wrong order
        self.assertEqual([x["branches"] for x in r["batches"]], [["a"], ["b"]])

    def test_batch_cap(self):
        for n in "abcde":
            self.branch(n, f"{n}.txt")
        r = self.plan("--batch", "2", "a", "b", "c", "d", "e")
        self.assertEqual([len(b["branches"]) for b in r["batches"]], [2, 2, 1])

    def test_auto_discovery_and_match(self):
        self.branch("feat/a", "a.txt")
        self.branch("feat/b", "b.txt")
        self.branch("other", "o.txt")
        self.git("branch", "idle", "main")  # not ahead
        self.branch("done", "d.txt")
        self.git("merge", "--no-edit", "done")  # merged
        r = self.plan("--auto")
        self.assertEqual(set(r["branches"]), {"feat/a", "feat/b", "other"})
        r = self.plan("--auto", "--match", "feat/*")
        self.assertEqual(set(r["branches"]), {"feat/a", "feat/b"})

    def test_read_only(self):
        self.commit("f.txt", "1\n2\n3\n", "f")
        self.branch("a", "f.txt", "1\nA\n3\n")
        self.branch("b", "f.txt", "1\nB\n3\n")
        self.branch("c", "c.txt")
        snap = lambda: (self.git("for-each-ref"), self.git("worktree", "list"),
                        self.git("status", "--porcelain"), self.git("rev-parse", "HEAD"))
        before = snap()
        self.plan("--auto")
        self.assertEqual(before, snap())

    def test_json_first_then_summary(self):
        self.branch("a", "a.txt")
        p = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.repo), "a"],
                           text=True, capture_output=True, check=True)
        obj, end = json.JSONDecoder().raw_decode(p.stdout)
        self.assertIn("batch 1: a", p.stdout[end:])


if __name__ == "__main__":
    unittest.main()
