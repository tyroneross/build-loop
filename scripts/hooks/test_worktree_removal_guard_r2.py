#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the 16 findings of the round-2 review of e0f5b605.

Named test_r2_fN_*. Each reproduced its finding against e0f5b605 before the
fix. Zero deps. Run: python3 scripts/hooks/test_worktree_removal_guard_r2.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "scripts"))

import worktree_archive as wa  # noqa: E402
from test_worktree_removal_guard_review import Base, git  # noqa: E402


class R2(Base):
    def receipted(self) -> dict:
        return wa.archive(self.wt)

    # backstop / detection
    def test_r2_f10_control_structures(self) -> None:
        for cmd in (f"if true; then rm -rf {self.wt}; fi",
                    f"for x in 1; do rm -rf {self.wt}; done",
                    f"while false; do :; done; case a in a) rm -rf {self.wt};; esac"):
            with self.subTest(cmd=cmd):
                self.denied(self.run_guard(cmd))

    def test_r2_f11_brace_expansion(self) -> None:
        self.denied(self.run_guard(f"rm -rf {self.trees}/{{wt,unused}}"))

    def test_r2_f12_env_chdir(self) -> None:
        self.denied(self.run_guard(f"env -C {self.trees} rm -rf wt"))

    def test_r2_f13_explicit_xargs_operand(self) -> None:
        self.denied(self.run_guard(f"printf '%s\\n' unused | xargs rm -rf {self.wt}"))

    def test_r2_f14_quoted_heredoc_lookalike_hides_nothing(self) -> None:
        self.denied(self.run_guard(f"echo '<<EOF'\nrm -rf {self.wt}\n"))

    # backstop-only: constructs the precise parser deliberately does not model
    def test_r2_backstop_command_substitution(self) -> None:
        self.denied(self.run_guard(f"echo $(rm -rf {self.wt})"))
        self.denied(self.run_guard(f"x=`rm -rf {self.wt}`"))

    def test_r2_backstop_unparseable_command(self) -> None:
        self.denied(self.run_guard(f'rm -rf {self.wt}; echo "unterminated'))

    def test_r2_herestring_fed_to_shell(self) -> None:
        self.denied(self.run_guard(f'bash <<< "rm -rf {self.wt}"'))

    # write-before-remove
    def test_r2_f1_write_into_worktree_before_removal(self) -> None:
        self.receipted()
        self.allowed(self.run_guard(f"rm -rf {self.wt}"))
        for cmd in (f"printf x > {self.wt}/new.txt; rm -rf {self.wt}",
                    f"echo x | tee {self.wt}/n.txt && git worktree remove {self.wt}",
                    f"cp /etc/hosts {self.wt}/ && rm -rf {self.wt}",
                    f"touch {self.wt}/a; rm -rf {self.wt}"):
            with self.subTest(cmd=cmd):
                reason = self.denied(self.run_guard(cmd))
                self.assertIn("writes into", reason)
        self.allowed(self.run_guard(f"cd {self.wt} && make 2>&1; cd / && rm -rf {self.wt}"))

    # archive file operands
    def test_r2_f2_archive_file_operand(self) -> None:
        r = self.receipted()
        ap = r["archive_path"]
        for cmd in (f"rm -rf {self.wt} {ap}", f"rm {ap}; git worktree remove --force {self.wt}",
                    f"if true; then rm -f {ap}; rm -rf {self.wt}; fi"):
            with self.subTest(cmd=cmd):
                reason = self.denied(self.run_guard(cmd))
                self.assertIn("archive", reason)

    # submodules
    def test_r2_f3_dirty_submodule_contents_are_archived(self) -> None:
        sub_src = self.tmp / "subsrc"
        sub_src.mkdir()
        git(sub_src, "init", "-q", "-b", "main")
        git(sub_src, "config", "user.email", "t@example.com")
        git(sub_src, "config", "user.name", "Test")
        (sub_src / "f.txt").write_text("v1\n")
        git(sub_src, "add", ".")
        git(sub_src, "commit", "-q", "-m", "s")
        subprocess.run(["git", "-C", str(self.wt), "-c", "protocol.file.allow=always", "submodule", "add",
                        "-q", str(sub_src), "sub"], check=True, capture_output=True)
        git(self.wt, "commit", "-q", "-m", "add sub")
        (self.wt / "sub" / "f.txt").write_text("local edit\n")
        (self.wt / "sub" / "local.txt").write_text("untracked in sub\n")
        r = wa.archive(self.wt)
        paths = {f["path"] for f in r["files"]}
        self.assertIn("sub/f.txt", paths)
        self.assertIn("sub/local.txt", paths)
        self.allowed(self.run_guard(f"rm -rf {self.wt}"))
        (self.wt / "sub" / "local.txt").write_text("changed after archive\n")
        self.denied(self.run_guard(f"rm -rf {self.wt}"))

    # hidden tracked edits + index freshness + archive-time race
    def test_r2_f4_assume_unchanged_edit_is_archived(self) -> None:
        git(self.wt, "update-index", "--assume-unchanged", "README.md")
        (self.wt / "README.md").write_text("hidden local edit\n")
        paths = {f["path"] for f in wa.archive(self.wt)["files"]}
        self.assertIn("README.md", paths)

    def test_r2_f5_index_only_edit_voids_receipt(self) -> None:
        (self.wt / "README.md").write_text("A\n")
        git(self.wt, "add", "README.md")
        (self.wt / "README.md").write_text("B\n")
        wa.archive(self.wt)
        self.allowed(self.run_guard(f"rm -rf {self.wt}"))
        blob = subprocess.run(["git", "-C", str(self.wt), "hash-object", "-w", "--stdin"], input="C\n",
                              capture_output=True, text=True, check=True).stdout.strip()
        git(self.wt, "update-index", "--cacheinfo", f"100644,{blob},README.md")
        self.denied(self.run_guard(f"rm -rf {self.wt}"))

    def test_r2_f6_archive_time_race_refuses(self) -> None:
        orig = wa._tar_files

        def racing(*a, **k):
            orig(*a, **k)
            (self.wt / ".build-loop" / "notes.md").write_text("edited during tar, longer\n")

        wa._tar_files = racing
        try:
            with self.assertRaises(RuntimeError) as ctx:
                wa.archive(self.wt)
        finally:
            wa._tar_files = orig
        self.assertIn("changed while", str(ctx.exception))
        self.assertIsNone(wa.load_receipt(self.wt))

    # verification completeness + timeouts
    def test_r2_f8_unreadable_dir_after_archive_denies(self) -> None:
        self.receipted()
        locked = self.wt / ".build-loop" / "locked"
        locked.mkdir()
        (locked / "note").write_text("x")
        os.chmod(locked, 0)
        reason = self.denied(self.run_guard(f"rm -rf {self.wt}"))
        self.assertIn("incomplete", reason)

    def test_r2_f9_git_error_after_established_removal_denies(self) -> None:
        self.receipted()
        env = {"PATH": "/nonexistent"}  # git cannot run; python still can via sys.executable
        reason = self.denied(self.run_guard(f"git worktree remove --force {self.wt}", env=env))
        self.assertIn(str(self.wt), reason)

    def test_r2_f9_fingerprint_past_deadline_is_timeout(self) -> None:
        self.receipted()
        orig = wa.fingerprint

        def slow(*a, **k):
            time.sleep(0.4)
            return orig(*a, **k)

        wa.fingerprint = slow
        try:
            verdict, _ = wa.receipt_verdict(self.wt, deadline=time.monotonic() + 0.25)
        finally:
            wa.fingerprint = orig
        self.assertEqual(verdict, "timeout")

    # false denials
    def test_r2_f15_verified_cache_target_skips_the_scan(self) -> None:
        (self.wt / "package.json").write_text("{}\n")
        nm = self.wt / "node_modules"
        for i in range(30):
            (nm / f"d{i}").mkdir(parents=True)
        self.allowed(self.run_guard(f"rm -rf {nm}", env={"BUILD_LOOP_WT_GUARD_SCAN_BUDGET": "10"}))

    def test_r2_f16_nonrecursive_find_exec_rm_is_not_removal(self) -> None:
        self.allowed(self.run_guard(f"find {self.wt} -type f -exec rm {{}} \\;"))
        self.allowed(self.run_guard(f"find {self.wt} -exec rm {{}} \\; -print"))
        self.allowed(self.run_guard(f"rm {self.wt}/README.md"))

    def test_r2_new_cache_dir_after_archive_keeps_receipt_valid(self) -> None:
        # Found by measurement on a real worktree: importing a module created
        # __pycache__/ and voided the receipt when caches were fingerprinted.
        self.receipted()
        (self.wt / "pkg" / "__pycache__").mkdir(parents=True)
        (self.wt / "pkg" / "__pycache__" / "m.pyc").write_bytes(b"\0")
        self.allowed(self.run_guard(f"rm -rf {self.wt}"))

    # the backstop stays quiet on confident allows and on data
    def test_r2_backstop_quiet_on_confident_allows_and_data(self) -> None:
        (self.wt / "package.json").write_text("{}\n")
        (self.wt / "node_modules").mkdir()
        name = self.wt.name
        for cmd in (f"git -C {self.wt} status && rm -rf {self.wt}/node_modules",
                    f'git commit -m "drop {name} after rm -rf of build"',
                    f"echo 'rm -rf {name}'",
                    "if [ -d build ]; then rm -rf build; fi",
                    f"rm -rf {self.tmp}/elsewhere && ls {name}",
                    "for f in *.log; do rm -f \"$f\"; done"):
            with self.subTest(cmd=cmd):
                self.allowed(self.run_guard(cmd, cwd=self.trees))


class R3(Base):
    """Round-3 review of 09ec8d27: the two findings it left open."""

    def test_r3_f1_quoted_command_substitution_is_counted(self) -> None:
        for cmd in (f'echo "$(rm -rf {self.wt})"', f'echo "`rm -rf {self.wt}`"',
                    f'x="$(cd / && rm -rf "{self.wt}")"'):
            with self.subTest(cmd=cmd):
                self.assertIn("archive", self.denied(self.run_guard(cmd)))
        for cmd in (f'git commit -m "rm -rf {self.wt.name} later"', 'echo "$(date) rm -rf"',
                    'echo "$(rm -rf node_modules)"'):
            with self.subTest(cmd=cmd):
                self.allowed(self.run_guard(cmd, cwd=self.trees))

    def test_r3_f2_deinitialized_submodule_leftovers_are_archived(self) -> None:
        sub_src = self.tmp / "subsrc"
        sub_src.mkdir()
        git(sub_src, "init", "-q", "-b", "main")
        git(sub_src, "config", "user.email", "t@example.com")
        git(sub_src, "config", "user.name", "Test")
        (sub_src / "f.txt").write_text("v1\n")
        git(sub_src, "add", ".")
        git(sub_src, "commit", "-q", "-m", "s")
        subprocess.run(["git", "-C", str(self.wt), "-c", "protocol.file.allow=always", "submodule", "add",
                        "-q", str(sub_src), "sub"], check=True, capture_output=True)
        git(self.wt, "commit", "-q", "-m", "add sub")
        git(self.wt, "submodule", "deinit", "-q", "-f", "sub")
        (self.wt / "sub" / "notes.txt").write_text("keep\n")
        paths = {f["path"] for f in wa.archive(self.wt)["files"]}
        self.assertIn("sub/notes.txt", paths)
        self.allowed(self.run_guard(f"rm -rf {self.wt}"))
        (self.wt / "sub" / "notes.txt").write_text("changed after archive\n")
        self.denied(self.run_guard(f"rm -rf {self.wt}"))


if __name__ == "__main__":
    unittest.main()
