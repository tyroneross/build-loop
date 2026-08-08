#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/audit_git.py.

The convicting test (`test_checkout_dashdash_cannot_destroy_uncommitted_work`)
reproduces the observed incident: `independent-auditor` ran
`git checkout -- website/public/styles.css` mid-audit in a sibling repo and
destroyed an implementer's uncommitted work. Every test here builds a real
temporary git repo via subprocess so the allow/refuse paths exercise actual
git, not a mock.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Make scripts/ importable
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import audit_git  # noqa: E402

# Isolate from the developer machine's global/system git config (e.g. a
# `tag.gpgsign=true` in ~/.gitconfig would otherwise make even a bare
# `git tag x` demand a signing key). Applies to our own _git() helper AND
# to the git subprocess audit_git.py spawns, since both inherit os.environ.
os.environ.setdefault("GIT_CONFIG_GLOBAL", "/dev/null")
os.environ.setdefault("GIT_CONFIG_NOSYSTEM", "1")

_AUDIT_GIT_PY = str(_SCRIPTS / "audit_git.py")
_AGENT_DEF = _SCRIPTS.parent / "agents" / "independent-auditor.md"

_GIT_AVAILABLE = shutil.which("git") is not None


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _run_audit_git(repo: Path, *git_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _AUDIT_GIT_PY, "--repo", str(repo), "--", *git_args],
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(_GIT_AVAILABLE, "git binary not available")
class TestCheckoutIncidentConviction(unittest.TestCase):
    """The literal incident: `git checkout -- <file>` must be refused, and
    the front door must be the reason the uncommitted work survives."""

    def test_checkout_dashdash_cannot_destroy_uncommitted_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = _make_repo(tmp_path)

            styles = repo / "styles.css"
            styles.write_text("body { color: black; }\n")
            _git(repo, "add", "styles.css")
            _git(repo, "commit", "-m", "initial styles")

            # The implementer's uncommitted work-in-progress.
            uncommitted = "body { color: black; }\n/* WIP: dark mode */\n"
            styles.write_text(uncommitted)

            # --- The conviction: route through the front door. ---
            result = _run_audit_git(repo, "checkout", "--", "styles.css")

            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("checkout", result.stderr.lower())
            self.assertEqual(
                styles.read_text(),
                uncommitted,
                "audit_git.py must refuse `checkout --` and leave uncommitted work intact",
            )

            # --- The contrast: prove the front door is what saved it. ---
            bare = subprocess.run(
                ["git", "-C", str(repo), "checkout", "--", "styles.css"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(bare.returncode, 0)
            self.assertNotEqual(
                styles.read_text(),
                uncommitted,
                "bare `git checkout --` must actually destroy the modification "
                "(this is the contrast that proves the test is real, not vacuous)",
            )

            # Re-apply the modification; it's throwaway (tmpdir gets wiped).
            styles.write_text(uncommitted)


@unittest.skipUnless(_GIT_AVAILABLE, "git binary not available")
class TestReadonlySubcommandsPassThrough(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        (self.repo / "a.txt").write_text("first\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "first commit")
        (self.repo / "a.txt").write_text("second\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "second commit")

    def test_log(self) -> None:
        result = _run_audit_git(self.repo, "log", "--oneline", "-5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("second commit", result.stdout)

    def test_diff(self) -> None:
        result = _run_audit_git(self.repo, "diff", "HEAD~1..HEAD")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a.txt", result.stdout)

    def test_show(self) -> None:
        result = _run_audit_git(self.repo, "show", "HEAD:a.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "second\n")

    def test_status(self) -> None:
        result = _run_audit_git(self.repo, "status", "--short")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rev_parse(self) -> None:
        result = _run_audit_git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.strip()), 40)


@unittest.skipUnless(_GIT_AVAILABLE, "git binary not available")
class TestWriteFormsOfAllowlistedSubcommandsRefused(unittest.TestCase):
    """Some allowlisted subcommands (branch, tag, config, stash, remote,
    worktree, symbolic-ref) have write modes; those must still be refused."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        (self.repo / "a.txt").write_text("hello\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "initial")
        _git(self.repo, "branch", "x")
        _git(self.repo, "tag", "x")

    def test_branch_delete_refused(self) -> None:
        result = _run_audit_git(self.repo, "branch", "-D", "x")
        self.assertEqual(result.returncode, 2)
        branches = _git(self.repo, "branch", "--list", "x").stdout.strip()
        self.assertNotEqual(branches, "", "branch must survive the refused delete")

    def test_tag_delete_refused(self) -> None:
        result = _run_audit_git(self.repo, "tag", "-d", "x")
        self.assertEqual(result.returncode, 2)
        tags = _git(self.repo, "tag", "--list", "x").stdout.strip()
        self.assertNotEqual(tags, "", "tag must survive the refused delete")

    def test_config_write_refused(self) -> None:
        result = _run_audit_git(self.repo, "config", "user.name", "evil")
        self.assertEqual(result.returncode, 2)
        name = _git(self.repo, "config", "--get", "user.name").stdout.strip()
        self.assertEqual(name, "Test")

    def test_stash_push_refused(self) -> None:
        result = _run_audit_git(self.repo, "stash", "push")
        self.assertEqual(result.returncode, 2)

    def test_remote_add_refused(self) -> None:
        result = _run_audit_git(self.repo, "remote", "add", "o", "u")
        self.assertEqual(result.returncode, 2)

    def test_worktree_add_refused(self) -> None:
        result = _run_audit_git(self.repo, "worktree", "add", str(Path(self._tmp.name) / "wt"))
        self.assertEqual(result.returncode, 2)

    def test_symbolic_ref_write_refused(self) -> None:
        result = _run_audit_git(self.repo, "symbolic-ref", "HEAD", "refs/heads/x")
        self.assertEqual(result.returncode, 2)


@unittest.skipUnless(_GIT_AVAILABLE, "git binary not available")
class TestUnknownSubcommandRefusedByDefault(unittest.TestCase):
    """Allowlist, not deny-list: an unrecognized subcommand is refused, and
    so is a real mutating subcommand nobody explicitly denied."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        (self.repo / "a.txt").write_text("hello\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "initial")

    def test_made_up_subcommand_refused(self) -> None:
        result = _run_audit_git(self.repo, "frobnicate")
        self.assertEqual(result.returncode, 2)

    def test_restore_refused(self) -> None:
        # `restore` is the case that proves this is an allowlist, not a
        # deny-list that only thought of `checkout`.
        result = _run_audit_git(self.repo, "restore", "--staged", "a.txt")
        self.assertEqual(result.returncode, 2)


class TestShellStringAsSubcommandRefused(unittest.TestCase):
    """A whole shell command line passed as ONE argv element is refused.

    The refusal survives; only its reason code changed when the blanket
    metacharacter scan was dropped (see audit_git.py docstring: git runs
    with shell=False, so metacharacters are inert, and the scan was
    blocking legitimate reads like `log --pretty=format:'%H|%s'`).
    """

    def test_shell_metacharacter_in_single_argv_element_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)  # doesn't need to be a git repo; classify() is pure
            result = subprocess.run(
                [sys.executable, _AUDIT_GIT_PY, "--repo", str(repo), "--",
                 "log; rm -rf /tmp/x"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("shell", result.stderr.lower())

    def test_classify_direct(self) -> None:
        verdict = audit_git.classify(["log; rm -rf /tmp/x"])
        self.assertFalse(verdict["allowed"])
        self.assertEqual(verdict["reason_code"], "shell_string_as_subcommand")


@unittest.skipUnless(_GIT_AVAILABLE, "git binary not available")
class TestMutatingFlagsOnReadonlySubcommands(unittest.TestCase):
    """F1: refuse-by-default was applied at SUBCOMMAND granularity only, so
    a flag that writes a file or spawns a process rode in on an allowlisted
    read-only subcommand. `diff --output=<file>` truncates <file> — the
    exact capability (destroying an implementer's uncommitted work) that
    audit_git.py exists to remove."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        (self.repo / "a.txt").write_text("first\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "initial")
        (self.repo / "a.txt").write_text("second\n")

    def _victim(self, name: str = "victim.txt") -> tuple[Path, bytes]:
        """An implementer's uncommitted work sitting in the working tree."""
        victim = self.repo / name
        content = b"PRECIOUS UNCOMMITTED WORK\n"
        victim.write_bytes(content)
        return victim, content

    def test_output_flag_cannot_truncate_a_file(self) -> None:
        # `--output=<file>` is accepted by diff, log AND show (git 2.50.1);
        # it truncates-then-writes, so it is a working-tree destroyer.
        for args in (
            ("diff", "--output=victim.txt", "HEAD"),
            ("log", "--output=victim.txt"),
            ("show", "--output=victim.txt", "HEAD"),
            # separated form: `--output <file>` — not a single `--flag=` token
            ("diff", "--output", "victim.txt", "HEAD"),
        ):
            with self.subTest(args=args):
                victim, content = self._victim()
                result = _run_audit_git(self.repo, *args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(
                    victim.read_bytes(),
                    content,
                    f"`git {' '.join(args)}` must not touch victim.txt",
                )

    def test_output_flag_refusal_is_the_reason_the_file_survives(self) -> None:
        # Contrast that proves the test is not vacuous: bare git really does
        # truncate the file. Throwaway tmpdir repo, never the live checkout.
        victim, content = self._victim()
        bare = subprocess.run(
            ["git", "-C", str(self.repo), "diff", "--output=victim.txt", "HEAD"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(bare.returncode, 0, bare.stderr)
        self.assertNotEqual(
            victim.read_bytes(),
            content,
            "bare `git diff --output=<file>` must actually destroy the file's "
            "contents (the contrast that convicts the front door)",
        )

    def test_denied_flag_cannot_hide_behind_a_value_taking_option(self) -> None:
        # `git log -e --output=victim` / `git diff -f --output=victim` are
        # PARSE ERRORS (exit 128/129) — and git truncates the output file
        # before failing. A non-zero git exit is not a safe outcome, so the
        # scan must not skip the token after -e/-f on these subcommands.
        # (It does skip after `grep -e`, where git consumes it as a pattern.)
        for args in (
            ("log", "-e", "--output=victim.txt"),
            ("log", "-f", "--output=victim.txt"),
            ("diff", "-f", "--output=victim.txt", "HEAD"),
            ("diff", "-L", "--output=victim.txt", "HEAD"),
            ("show", "-e", "--output=victim.txt", "HEAD"),
        ):
            with self.subTest(args=args):
                victim, content = self._victim()
                result = _run_audit_git(self.repo, *args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(victim.read_bytes(), content)

    def test_grep_open_files_in_pager_is_refused(self) -> None:
        # `git grep -O<cmd>` / `--open-files-in-pager=<cmd>` runs <cmd>.
        # No shell metacharacter is involved, so no metacharacter filter
        # could ever have caught it.
        for flag_form in ("-O{cmd}", "--open-files-in-pager={cmd}"):
            with self.subTest(flag_form=flag_form):
                sentinel = Path(self._tmp.name) / f"pwned-{abs(hash(flag_form))}"
                self.assertFalse(sentinel.exists())
                result = _run_audit_git(
                    self.repo,
                    "grep",
                    flag_form.format(cmd=f"touch {sentinel}"),
                    "first",
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse(
                    sentinel.exists(),
                    f"`git grep {flag_form}` executed a command through the front door",
                )

    def test_bare_pager_and_ext_diff_flags_refused(self) -> None:
        # Bare `-O` / `--open-files-in-pager` still spawn core.pager;
        # `--ext-diff`/`--textconv` run gitattributes-configured drivers.
        for args in (
            ("grep", "-O", "first"),
            ("grep", "--open-files-in-pager", "first"),
            ("diff", "--ext-diff", "HEAD"),
            ("log", "--textconv"),
            ("log", "--help"),
        ):
            with self.subTest(args=args):
                result = _run_audit_git(self.repo, *args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_pre_subcommand_global_options_refused(self) -> None:
        # `-c` injects config (core.pager / alias.x=!sh reach execution);
        # `--git-dir`/`-C`/`--work-tree` escape the --repo confinement;
        # `--config-env` is the same class as `-c`.
        sentinel = Path(self._tmp.name) / "global-opt-pwned"
        for args in (
            ("-c", f"core.pager=touch {sentinel}", "log"),
            ("-c", "alias.z=!sh", "z"),
            ("--git-dir=/tmp/other", "log"),
            ("--exec-path=/tmp", "log"),
            ("--config-env=core.pager=EVIL", "log"),
            ("-C", "/tmp", "log"),
            ("--paginate", "log"),
        ):
            with self.subTest(args=args):
                result = _run_audit_git(self.repo, *args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(sentinel.exists())

    def test_classify_reason_code_is_specific(self) -> None:
        self.assertEqual(
            audit_git.classify(["diff", "--output=/tmp/x", "HEAD"])["reason_code"],
            "mutating_flag_on_readonly_subcommand",
        )
        self.assertEqual(
            audit_git.classify(["-c", "core.pager=x", "log"])["reason_code"],
            "pre_subcommand_global_option",
        )


@unittest.skipUnless(_GIT_AVAILABLE, "git binary not available")
class TestBranchAndRemoteResidualHoles(unittest.TestCase):
    """F14: `_branch` only checked write flags when an operand was present,
    and `_remote` only inspected rest[0]."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        (self.repo / "a.txt").write_text("hello\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "initial")

    def test_branch_flag_only_write_forms_refused(self) -> None:
        # No operand -> the old `operands and ...` guard never fired.
        for args in (
            ("branch", "--set-upstream-to=origin/main"),
            ("branch", "--unset-upstream"),
            ("branch", "--edit-description"),
            ("branch", "--create-reflog"),
            ("branch", "--track"),
        ):
            with self.subTest(args=args):
                result = _run_audit_git(self.repo, *args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        # ...and nothing reached repo config.
        upstream = _git(
            self.repo, "config", "--get", "branch.main.merge", check=False
        ).stdout.strip()
        self.assertEqual(upstream, "", "branch upstream config must be unset")

    def test_remote_mutating_verb_after_read_flag_refused(self) -> None:
        # `git remote -v add o u` really does add the remote: git skips the
        # -v and runs the verb. rest[0] == "-v" short-circuited the check.
        for args in (
            ("remote", "-v", "add", "o", "u"),
            ("remote", "-v", "set-url", "o", "u"),
            ("remote", "-v", "prune", "o"),
            ("remote", "--verbose", "rename", "o", "p"),
        ):
            with self.subTest(args=args):
                result = _run_audit_git(self.repo, *args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        remotes = _git(self.repo, "remote").stdout.strip()
        self.assertEqual(remotes, "", "no remote may have been created")


@unittest.skipUnless(_GIT_AVAILABLE, "git binary not available")
class TestGateDoesNotOverBlockLegitimateReads(unittest.TestCase):
    """Regression guard against the noisy-gate failure mode: a gate that
    refuses the auditor's real reads is a gate that gets switched off.
    Each case must exit 0 AND return real output."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _make_repo(Path(self._tmp.name))
        (self.repo / "a.txt").write_text("first line\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "first commit")
        (self.repo / "a.txt").write_text("second line\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "second commit")
        _git(self.repo, "remote", "add", "origin", "https://example.invalid/r.git")

    def _ok(self, *args: str) -> str:
        result = _run_audit_git(self.repo, *args)
        self.assertEqual(
            result.returncode, 0, f"`git {' '.join(args)}` refused: {result.stderr}"
        )
        return result.stdout

    def test_core_reads_still_allowed(self) -> None:
        self.assertIn("second commit", self._ok("log", "--oneline", "-5"))
        self.assertEqual(len(self._ok("log", "--pretty=format:%H", "-1").strip()), 40)
        self.assertIn("a.txt", self._ok("diff", "HEAD~1", "HEAD"))
        self.assertEqual(self._ok("show", "HEAD:a.txt"), "second line\n")
        self.assertIn("second", self._ok("grep", "second"))
        self._ok("status", "--short")
        self.assertEqual(len(self._ok("rev-parse", "HEAD").strip()), 40)
        self.assertIn("main", self._ok("branch", "--list"))
        self.assertIn("origin", self._ok("remote", "-v"))
        self.assertEqual(self._ok("config", "--get", "user.email").strip(), "test@example.com")

    def test_flag_lookalikes_not_caught_by_the_scan(self) -> None:
        # Identity matching, not substring: these SHARE a prefix with a
        # denied flag but are read-only.
        self._ok("log", "-1", "--output-indicator-new=X")
        self._ok("rev-parse", "--output-object-format=sha1")
        self._ok("log", "-1", "--no-ext-diff")
        self._ok("diff", "--no-textconv", "HEAD")
        self._ok("ls-files", "-o")          # --others, not an output flag
        self._ok("log", "-1", "-c")         # combined merge diff, not global -c
        self._ok("diff", "-C", "HEAD")      # find-copies, not global -C

    def test_metacharacter_bearing_reads_now_allowed(self) -> None:
        # These were refused by the old blanket metacharacter filter even
        # though shell=False makes the characters inert.
        self.assertIn("|", self._ok("log", "-1", "--pretty=format:%H|%s"))
        self._ok("log", "--grep=first|second")

    def test_searching_for_a_dash_o_string_is_allowed(self) -> None:
        # `-Ofast` is a compiler flag people really do grep for. It is the
        # VALUE of -e, which git consumes as data and never re-parses as an
        # option — so the -O scan must skip it.
        (self.repo / "flags.txt").write_text("CFLAGS = -Ofast\n")
        _git(self.repo, "add", "flags.txt")
        _git(self.repo, "commit", "-m", "add flags")
        self.assertIn("-Ofast", self._ok("grep", "-e", "-Ofast"))

    def test_branch_read_flag_spellings_allowed(self) -> None:
        self._ok("branch", "-av")
        self._ok("branch", "--sort=committerdate")
        self._ok("branch", "--format=%(refname:short)")
        self._ok("branch", "--show-current")


class TestAgentDefinitionNamesTheFrontDoor(unittest.TestCase):
    """Co-located regression guard: the agent body must instruct routing
    through audit_git.py. EXPECTED TO FAIL until the orchestrator wires
    agents/independent-auditor.md — do not skip or weaken this test."""

    def test_agent_definition_mentions_audit_git(self) -> None:
        self.assertTrue(_AGENT_DEF.exists(), f"{_AGENT_DEF} not found")
        body = _AGENT_DEF.read_text()
        self.assertIn(
            "audit_git.py",
            body,
            "agents/independent-auditor.md must instruct the agent to route git "
            "calls through scripts/audit_git.py instead of bare `git`",
        )


class TestClassifyIsPure(unittest.TestCase):
    """classify() never touches the filesystem or subprocess — sanity check
    that the allow path doesn't accidentally execute anything."""

    def test_allow_does_not_execute(self) -> None:
        verdict = audit_git.classify(["log", "--oneline", "-5"])
        self.assertTrue(verdict["allowed"])
        self.assertEqual(verdict["subcommand"], "log")

    def test_no_subcommand_refused(self) -> None:
        verdict = audit_git.classify([])
        self.assertFalse(verdict["allowed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
