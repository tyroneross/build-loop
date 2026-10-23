#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the 15 findings of the independent review of 823d4e1a.

One test (or a small group) per finding, named test_fN_*. Each one reproduced
its finding against 823d4e1a before the fix. Zero deps.

Run: python3 scripts/hooks/test_worktree_removal_guard_review.py

Every git call strips GIT_* from the environment (a hook-exported GIT_DIR once
made `git init` in a test set core.bare=true on the real repository).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
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


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(os.path.realpath(self._tmp.name))
        os.environ["BUILD_LOOP_WORKTREE_RECEIPTS_DIR"] = str(self.tmp / "receipts")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "Test")
        (self.repo / ".gitignore").write_text(
            ".build-loop/\nnode_modules/\n__pycache__/\nscratch/\nbuild/\n")
        (self.repo / "README.md").write_text("seed\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "seed")
        self.trees = self.tmp / "trees"
        self.wt = self.add_wt(self.trees / "wt")
        (self.wt / ".build-loop").mkdir()
        (self.wt / ".build-loop" / "notes.md").write_text("design notes\n")

    def tearDown(self) -> None:
        for root, dirs, _ in os.walk(self.tmp):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), 0o755)
                except OSError:
                    pass
        os.environ.pop("BUILD_LOOP_WORKTREE_RECEIPTS_DIR", None)
        self._tmp.cleanup()

    _n = 0

    def add_wt(self, path: Path) -> Path:
        Base._n += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        git(self.repo, "worktree", "add", "-q", "-b", f"b{Base._n}-{path.name}", str(path))
        return path

    def run_guard(self, command: str, cwd: Path | None = None, env: dict | None = None) -> dict:
        event = {"tool_name": "Bash", "tool_input": {"command": command},
                 "cwd": str(cwd or self.repo)}
        proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event),
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, **(env or {})})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def run_dispatch(self, command: str, cwd: Path | None = None) -> dict:
        event = {"tool_name": "Bash", "tool_input": {"command": command},
                 "cwd": str(cwd or self.repo)}
        env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
        env.pop("BUILD_LOOP_HOOKS", None)
        proc = subprocess.run(["bash", str(DISPATCH)], input=json.dumps(event),
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout or "{}")

    def denied(self, out: dict) -> str:
        hso = out.get("hookSpecificOutput") or {}
        self.assertEqual(hso.get("permissionDecision"), "deny", out)
        return hso["permissionDecisionReason"]

    def allowed(self, out: dict) -> None:
        self.assertNotEqual((out.get("hookSpecificOutput") or {}).get("permissionDecision"), "deny", out)


class ArchiveFindings(Base):
    def test_f1_valuable_file_under_cache_like_name_is_archived(self) -> None:
        (self.wt / "scratch" / "vendor").mkdir(parents=True)
        (self.wt / "scratch" / "vendor" / ".env").write_text("K=v\n")
        (self.wt / "build").mkdir()
        (self.wt / "build" / "draft.md").write_text("handwritten\n")
        receipt = wa.archive(self.wt)
        paths = {f["path"] for f in receipt["files"]}
        self.assertIn("scratch/vendor/.env", paths)
        self.assertIn("build/draft.md", paths)

    def test_f1_true_cache_root_is_listed_not_archived(self) -> None:
        (self.wt / "package.json").write_text("{}\n")
        (self.wt / "node_modules" / "pkg").mkdir(parents=True)
        (self.wt / "node_modules" / "pkg" / "index.js").write_text("x\n")
        (self.wt / "node_modules" / "pkg" / ".env").write_text("K=v\n")
        receipt = wa.archive(self.wt)
        paths = {f["path"] for f in receipt["files"]}
        self.assertNotIn("node_modules/pkg/index.js", paths)
        self.assertIn("node_modules/pkg/.env", paths)  # credential-shaped: kept
        self.assertIn("node_modules/", receipt["caches_listed_not_archived"])

    def test_f2_unreadable_directory_refuses_receipt(self) -> None:
        locked = self.wt / ".build-loop" / "locked"
        locked.mkdir()
        (locked / "secret.md").write_text("x\n")
        os.chmod(locked, 0)
        with self.assertRaises(RuntimeError) as ctx:
            wa.archive(self.wt)
        self.assertIn("incomplete", str(ctx.exception))
        self.assertIsNone(wa.load_receipt(self.wt))

    def test_f2_cache_scan_budget_refuses_receipt(self) -> None:
        (self.wt / "package.json").write_text("{}\n")
        nm = self.wt / "node_modules"
        nm.mkdir()
        for i in range(20):
            (nm / f"f{i}.js").write_text("x")
        old = wa.CACHE_SCAN_BUDGET
        wa.CACHE_SCAN_BUDGET = 5
        try:
            with self.assertRaises(RuntimeError) as ctx:
                wa.archive(self.wt)
        finally:
            wa.CACHE_SCAN_BUDGET = old
        self.assertIn("node_modules", str(ctx.exception))
        self.assertIsNone(wa.load_receipt(self.wt))
        # --include-caches is the documented way through.
        receipt = wa.archive(self.wt, include_caches=True)
        self.assertIn("node_modules/f3.js", {f["path"] for f in receipt["files"]})

    def test_f3_dest_inside_worktree_is_refused(self) -> None:
        with self.assertRaises(RuntimeError):
            wa.archive(self.wt, dest=self.wt / "backups")
        self.assertIsNone(wa.load_receipt(self.wt))

    def test_f4_receipt_rejected_after_ignored_file_changes(self) -> None:
        wa.archive(self.wt)
        self.allowed(self.run_guard(f"git worktree remove --force {self.wt}"))
        (self.wt / ".build-loop" / "notes.md").write_text("design notes, edited later\n")
        reason = self.denied(self.run_guard(f"git worktree remove --force {self.wt}"))
        self.assertIn("changed since", reason)

    def test_f4_receipt_rejected_after_new_untracked_file(self) -> None:
        wa.archive(self.wt)
        (self.wt / "new-notes.txt").write_text("n\n")
        self.denied(self.run_guard(f"git worktree remove --force {self.wt}"))

    def test_f5_same_basename_archives_never_collide(self) -> None:
        other = self.add_wt(self.tmp / "elsewhere" / "wt")
        (other / "x.txt").write_text("other\n")
        dest = self.tmp / "archives"
        r1 = wa.archive(self.wt, dest=dest)
        r2 = wa.archive(other, dest=dest)
        self.assertNotEqual(r1["archive_path"], r2["archive_path"])
        self.allowed(self.run_guard(f"git worktree remove {self.wt}"))

    def test_f5_truncated_archive_invalidates_receipt(self) -> None:
        r = wa.archive(self.wt)
        Path(r["archive_path"]).write_bytes(b"truncated")
        reason = self.denied(self.run_guard(f"git worktree remove {self.wt}"))
        self.assertIn("archive", reason)

    def test_f12_tracked_names_with_spaces_and_arrows_are_archived(self) -> None:
        for name in (" leading.txt", "left -> right.txt"):
            (self.wt / name).write_text("v1\n")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-q", "-m", "odd names")
        for name in (" leading.txt", "left -> right.txt"):
            (self.wt / name).write_text("v2\n")
        paths = {f["path"] for f in wa.archive(self.wt)["files"]}
        self.assertIn(" leading.txt", paths)
        self.assertIn("left -> right.txt", paths)

    def test_f13_staged_and_unstaged_states_are_both_preserved(self) -> None:
        (self.wt / "README.md").write_text("version A\n")
        git(self.wt, "add", "README.md")
        (self.wt / "README.md").write_text("version B\n")
        r = wa.archive(self.wt)
        with tarfile.open(r["archive_path"]) as tar:
            staged = tar.extractfile("__worktree_archive__/staged.patch").read().decode()
            unstaged = tar.extractfile("__worktree_archive__/unstaged.patch").read().decode()
        self.assertIn("+version A", staged)
        self.assertIn("+version B", unstaged)


class GuardFindings(Base):
    def test_f3_rm_of_worktree_plus_its_archive_dir_is_denied(self) -> None:
        dest = self.tmp / "archives"
        wa.archive(self.wt, dest=dest)
        self.allowed(self.run_guard(f"rm -rf {self.wt}"))
        reason = self.denied(self.run_guard(f"rm -rf {self.wt} {dest}"))
        self.assertIn("archive", reason)

    def test_f6_quoted_or_escaped_command_names_reach_the_guard(self) -> None:
        for cmd in (f'"rm" -rf {self.wt}', f"r\\m -rf {self.wt}",
                    f'git worktree re"move" --force {self.wt}'):
            with self.subTest(cmd=cmd):
                self.denied(self.run_dispatch(cmd))

    def test_f7_git_suffix_shorthand_is_resolved(self) -> None:
        self.denied(self.run_guard(f"git -C {self.repo} worktree remove --force wt"))

    def test_f8_subshell_cd_does_not_leak(self) -> None:
        self.denied(self.run_guard("(cd /); rm -rf wt", cwd=self.trees))

    def test_f8_cd_double_dash_is_an_option(self) -> None:
        self.denied(self.run_guard(f"cd -- {self.trees} && rm -rf wt", cwd=self.repo))

    def test_f8_unknown_cwd_with_matching_worktree_is_denied(self) -> None:
        self.denied(self.run_guard('cd "$SOMEWHERE" && rm -rf wt', cwd=self.repo))
        self.allowed(self.run_guard('cd "$SOMEWHERE" && rm -rf not-a-worktree', cwd=self.repo))

    def test_f9_deep_and_cache_named_parents_are_found(self) -> None:
        deep = self.add_wt(self.tmp / "t2" / "a" / "b" / "c" / "d" / "deepwt")
        self.assertTrue(deep.exists())
        self.denied(self.run_guard(f"rm -rf {self.tmp / 't2'}"))
        self.add_wt(self.tmp / "t3" / "build" / "bwt")
        self.denied(self.run_guard(f"rm -rf {self.tmp / 't3'}"))

    def test_f9_scan_budget_exhaustion_denies(self) -> None:
        big = self.tmp / "big"
        for i in range(30):
            (big / f"d{i}").mkdir(parents=True)
        reason = self.denied(self.run_guard(f"rm -rf {big}",
                                            env={"BUILD_LOOP_WT_GUARD_SCAN_BUDGET": "10"}))
        self.assertIn("could not prove", reason)

    def test_f10_wrappers_and_indirect_deleters(self) -> None:
        for cmd in (f"env -u X rm -rf {self.wt}", f"nice -n 5 rm -rf {self.wt}",
                    f"sudo -u root rm -rf {self.wt}", f"timeout 5 rm -rf {self.wt}",
                    f"command rm -rf {self.wt}", f"time -p rm -rf {self.wt}",
                    f"printf '%s\\n' {self.wt} | xargs rm -rf",
                    f"find {self.wt} -depth -delete",
                    f"find {self.wt} -exec rm -rf {{}} +"):
            with self.subTest(cmd=cmd):
                self.denied(self.run_guard(cmd))

    def test_f11_malformed_receipt_never_erases_other_denials(self) -> None:
        b = self.add_wt(self.trees / "b")
        wa.archive(b)
        rp = wa.receipt_path_for(b)
        for bad in ("invalid", "NaN"):
            with self.subTest(bad=bad):
                data = json.loads(rp.read_text())
                data["created_at_epoch"] = bad if bad == "invalid" else float("nan")
                rp.write_text(json.dumps(data))
                reason = self.denied(self.run_guard(f"rm -rf {self.wt} {b}"))
                self.assertIn(str(self.wt), reason)
                self.assertIn(str(b), reason)

    def test_f14_heredoc_body_and_quoted_glob_are_not_removals(self) -> None:
        self.allowed(self.run_guard(f"cat > /dev/null <<'EOF'\nrm -rf {self.wt}\nEOF\n"))
        self.allowed(self.run_guard(f"rm -rf '{self.trees}/*'"))

    def test_f14_heredoc_fed_to_a_shell_is_judged(self) -> None:
        self.denied(self.run_guard(f"bash <<'EOF'\nrm -rf {self.wt}\nEOF\n"))

    def test_f14_symlink_operand_is_the_link_itself(self) -> None:
        link = self.tmp / "wt-link"
        link.symlink_to(self.wt)
        self.allowed(self.run_guard(f"rm -rf {link}"))
        self.denied(self.run_guard(f"rm -rf {link}/"))  # trailing slash follows the link

    def test_f15_budget_exhaustion_denies_only_established_removals(self) -> None:
        wa.archive(self.wt)
        env = {"BUILD_LOOP_WT_GUARD_BUDGET_S": "0"}
        reason = self.denied(self.run_guard(f"git worktree remove {self.wt}", env=env))
        self.assertIn("time budget", reason)
        other = self.tmp / "other"
        other.mkdir()
        self.allowed(self.run_guard(f"rm -rf {other}", env=env))


if __name__ == "__main__":
    unittest.main()
