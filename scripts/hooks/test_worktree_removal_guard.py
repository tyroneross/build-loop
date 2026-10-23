#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for worktree_removal_guard.py + worktree_archive.py. Zero deps.

Run: python3 scripts/hooks/test_worktree_removal_guard.py

Every git call strips GIT_* from the environment: a hook-exported GIT_DIR once
made `git init` in a test set core.bare=true on the REAL repository.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent.parent
GUARD = HERE / "worktree_removal_guard.py"
DISPATCH = HERE / "pre_bash_dispatch.sh"
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import worktree_archive as wa  # noqa: E402


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.receipts = self.tmp / "receipts"
        os.environ["BUILD_LOOP_WORKTREE_RECEIPTS_DIR"] = str(self.receipts)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "Test")
        (self.repo / ".gitignore").write_text(".build-loop/\nnode_modules/\n__pycache__/\n")
        (self.repo / "README.md").write_text("seed\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "seed")
        self.wt_parent = self.tmp / "repo.worktrees"
        self.wt = self.wt_parent / "wt1"
        git(self.repo, "worktree", "add", "-q", "-b", "wt1", str(self.wt))
        (self.wt / ".build-loop").mkdir()
        (self.wt / ".build-loop" / "notes.md").write_text("design notes\n")
        (self.wt / "package.json").write_text("{}\n")  # makes node_modules/ a real cache root
        git(self.wt, "add", "package.json")
        git(self.wt, "commit", "-q", "-m", "pkg")
        (self.wt / "node_modules" / "pkg").mkdir(parents=True)
        (self.wt / "node_modules" / "pkg" / "index.js").write_text("x\n")
        (self.wt / "__pycache__").mkdir()
        (self.wt / "__pycache__" / "m.pyc").write_bytes(b"\0")
        (self.wt / "scratch.txt").write_text("untracked\n")

    def tearDown(self) -> None:
        os.environ.pop("BUILD_LOOP_WORKTREE_RECEIPTS_DIR", None)
        self._tmp.cleanup()

    # helpers
    def run_guard(self, command: str, cwd: Path | None = None) -> dict:
        event = {"tool_name": "Bash", "tool_input": {"command": command},
                 "cwd": str(cwd or self.repo)}
        proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def assertDenied(self, out: dict) -> str:
        hso = out.get("hookSpecificOutput") or {}
        self.assertEqual(hso.get("permissionDecision"), "deny", out)
        return hso["permissionDecisionReason"]

    def assertAllowed(self, out: dict) -> None:
        self.assertNotIn("hookSpecificOutput", out, out)

    # guard behaviour
    def test_remove_without_receipt_is_blocked_and_names_the_command(self) -> None:
        reason = self.assertDenied(self.run_guard(f"git worktree remove --force {self.wt}"))
        self.assertIn("worktree_archive.py", reason)
        self.assertIn(f"--path {self.wt}", reason)

    def test_remove_with_fresh_matching_receipt_is_allowed(self) -> None:
        wa.archive(self.wt)
        self.assertAllowed(self.run_guard(f"git worktree remove --force {self.wt}"))

    def test_relative_remove_after_cd_is_judged(self) -> None:
        self.assertDenied(self.run_guard("cd ../repo.worktrees && git -C ../repo worktree remove ../repo.worktrees/wt1",
                                         cwd=self.repo))

    def test_receipt_is_stale_when_head_moves(self) -> None:
        wa.archive(self.wt)
        (self.wt / "new.txt").write_text("n\n")
        git(self.wt, "add", "new.txt")
        git(self.wt, "commit", "-q", "-m", "move head")
        reason = self.assertDenied(self.run_guard(f"git worktree remove {self.wt}"))
        self.assertIn("HEAD moved", reason)

    def test_receipt_is_stale_after_thirty_minutes(self) -> None:
        wa.archive(self.wt)
        rp = wa.receipt_path_for(self.wt)
        data = json.loads(rp.read_text())
        data["created_at_epoch"] = time.time() - wa.RECEIPT_TTL_S - 5
        rp.write_text(json.dumps(data))
        reason = self.assertDenied(self.run_guard(f"git worktree remove {self.wt}"))
        self.assertIn("minutes old", reason)

    def test_rm_rf_of_registered_worktree_is_blocked(self) -> None:
        self.assertDenied(self.run_guard(f"rm -rf {self.wt}"))

    def test_rm_rf_of_parent_of_worktree_is_blocked(self) -> None:
        self.assertDenied(self.run_guard(f"rm -rf {self.wt_parent}"))
        self.assertDenied(self.run_guard("trash repo.worktrees", cwd=self.tmp))
        self.assertDenied(self.run_guard(f"bash -lc 'safe-del {self.wt_parent}'"))

    def test_rm_of_parent_holding_the_archive_stays_blocked(self) -> None:
        wa.archive(self.wt)  # default dest is repo.worktrees/_preserved
        reason = self.assertDenied(self.run_guard(f"rm -rf {self.wt_parent}"))
        self.assertIn("would also delete its archive", reason)
        self.assertAllowed(self.run_guard(f"rm -rf {self.wt}"))

    def test_unrelated_rm_and_prune_are_allowed(self) -> None:
        other = self.tmp / "other"
        other.mkdir()
        self.assertEqual(self.run_guard(f"rm -rf {other}"), {})
        self.assertEqual(self.run_guard(f"rm {self.wt}/scratch.txt"), {})  # not recursive
        self.assertEqual(self.run_guard(f"rm -rf {self.wt}/node_modules"), {})  # inside, not the root
        self.assertEqual(self.run_guard("git worktree prune"), {})
        self.assertEqual(self.run_guard("ls -la"), {})

    def test_unresolvable_target_warns_and_allows(self) -> None:
        out = self.run_guard('rm -rf "$WT"')
        self.assertAllowed(out)
        self.assertIn("not resolvable", out.get("systemMessage", ""))

    # archive behaviour
    def test_archive_round_trip_keeps_ignored_state_and_skips_caches(self) -> None:
        receipt = wa.archive(self.wt)
        listed = {f["path"]: f for f in receipt["files"]}
        self.assertIn(".build-loop/notes.md", listed)
        self.assertEqual(listed[".build-loop/notes.md"]["kind"], "ignored")
        self.assertEqual(listed[".build-loop/notes.md"]["bytes"], len("design notes\n"))
        self.assertIn("scratch.txt", listed)
        self.assertNotIn("node_modules/pkg/index.js", listed)
        self.assertIn("node_modules/", receipt["caches_listed_not_archived"])
        self.assertIn("__pycache__/", receipt["caches_listed_not_archived"])
        self.assertEqual(receipt["head"], git(self.wt, "rev-parse", "HEAD"))
        self.assertEqual(receipt["branch"], "wt1")
        ap = Path(receipt["archive_path"])
        self.assertEqual(ap.parent, self.wt_parent / "_preserved")
        with tarfile.open(ap) as tar:
            names = tar.getnames()
            self.assertIn("wt1/.build-loop/notes.md", names)
            self.assertEqual(tar.extractfile("wt1/.build-loop/notes.md").read(), b"design notes\n")
        self.assertFalse(any("node_modules" in n or "__pycache__" in n for n in names), names)
        import hashlib
        self.assertEqual(receipt["archive_sha256"], hashlib.sha256(ap.read_bytes()).hexdigest())
        stored = json.loads(wa.receipt_path_for(self.wt).read_text())
        self.assertEqual(stored["archive_sha256"], receipt["archive_sha256"])

    def test_archive_refuses_a_non_worktree(self) -> None:
        with self.assertRaises(RuntimeError):
            wa.archive(self.repo)  # the main checkout is not a linked worktree

    # dispatcher wiring
    def test_dispatcher_denies_even_outside_an_opted_in_repo(self) -> None:
        event = {"tool_name": "Bash", "tool_input": {"command": f"git worktree remove {self.wt}"},
                 "cwd": str(self.repo)}
        env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
        env.pop("BUILD_LOOP_HOOKS", None)
        proc = subprocess.run(["bash", str(DISPATCH)], input=json.dumps(event),
                              capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertDenied(json.loads(proc.stdout))


if __name__ == "__main__":
    unittest.main()
