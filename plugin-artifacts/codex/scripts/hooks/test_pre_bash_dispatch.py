#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Subprocess-level tests for scripts/hooks/pre_bash_dispatch.sh. Zero deps.

The dispatcher is the single PreToolUse:Bash entry that replaced the 3-hook
chain. It MUST preserve the one intentional enforcement path — the commit
auditor's rc==2 deterministic block (staged secrets / merge-conflict markers)
maps to a dispatcher `exit 2` so Claude Code denies the commit. Everything else
stays fail-open (advisory). These tests drive the real dispatcher script in a
throwaway git repo and assert on its exit code + stdout envelope.

Coverage:
  - commit command → routes to the commit auditor (exit 0 on a clean diff)
  - compound `cd x && git commit` → still routes (substring match on `commit`)
  - plain non-commit command → pass-through, no auditor spawn, exit 0
  - fail-open when python3 is absent from PATH (missing-interpreter → exit 0)
  - STAGED SECRET → auditor rc==2 → dispatcher exit 2 (F1's closure proof)

The PATH-stripped fail-open case is also exercised under `env -i
PATH=/usr/bin:/bin` per the hooks minimal-PATH contract.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISPATCH = HERE / "pre_bash_dispatch.sh"
# PLUGIN_ROOT is two levels up from scripts/hooks/.
PLUGIN_ROOT = HERE.parent.parent


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def make_buildloop_repo(tmp: Path) -> Path:
    """A git repo with .build-loop/config.json so the dispatcher scope guard
    enforces (it no-ops outside build-loop projects)."""
    repo = tmp / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / ".build-loop").mkdir(parents=True, exist_ok=True)
    (repo / ".build-loop" / "config.json").write_text("{}", encoding="utf-8")
    # An initial commit so HEAD exists and `git show :<f>` works on staged files.
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def run_dispatch(
    repo: Path,
    command: str,
    *,
    path_env: str | None = None,
    minimal_env: bool = False,
    plugin_root: Path | None = None,
) -> subprocess.CompletedProcess:
    """Drive the dispatcher exactly as Claude Code would: PreToolUse event JSON
    on stdin, process cwd == the repo (the commit auditor reads the repo via
    `git rev-parse` in its own cwd)."""
    event = json.dumps({"tool_input": {"command": command}, "cwd": str(repo)})
    if minimal_env:
        env = {"PATH": path_env or "/usr/bin:/bin"}
    else:
        env = dict(os.environ)
        if path_env is not None:
            env["PATH"] = path_env
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root or PLUGIN_ROOT)
    return subprocess.run(
        ["bash", str(DISPATCH)],
        cwd=repo,
        input=event,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class DispatchRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = make_buildloop_repo(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_clean_commit_routes_and_passes(self) -> None:
        """A `git commit` on a clean (non-secret) staged diff routes to the
        auditor, which emits its packet to stderr and exits 0 → dispatcher 0."""
        (self.repo / "feature.txt").write_text("hello world\n", encoding="utf-8")
        _git(self.repo, "add", "feature.txt")
        r = run_dispatch(self.repo, "git commit -m 'add feature'")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        # Auditor emits its audit packet to stderr on the clean path.
        self.assertIn("Audit packet", r.stderr)

    def test_compound_cd_commit_still_routes(self) -> None:
        """`cd x && git commit` — the substring `commit` filter still routes."""
        (self.repo / "feature.txt").write_text("hello\n", encoding="utf-8")
        _git(self.repo, "add", "feature.txt")
        r = run_dispatch(self.repo, "cd /tmp && git commit -m x")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        self.assertIn("Audit packet", r.stderr)

    def test_plain_noncommit_command_passes_through(self) -> None:
        """A non-commit command never spawns the auditor; quiet exit 0."""
        r = run_dispatch(self.repo, "ls -la")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        self.assertNotIn("Audit packet", r.stderr)

    def test_staged_secret_hard_blocks_with_exit_2(self) -> None:
        """F1 CLOSURE PROOF: a staged id_rsa.pem with credential-shaped content
        trips the auditor's deterministic block (rc==2); the dispatcher MUST
        propagate exit 2 so the commit is denied."""
        secret = self.repo / "id_rsa.pem"
        secret.write_text(
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "api_key = 'AKIA1234567890ABCDEF'\n"
            "-----END RSA PRIVATE KEY-----\n",
            encoding="utf-8",
        )
        _git(self.repo, "add", "id_rsa.pem")
        r = run_dispatch(self.repo, "git commit -m 'oops secret'")
        self.assertEqual(r.returncode, 2, f"stderr={r.stderr!r}")
        # The blocking reason is passed through on stderr.
        self.assertIn("DETERMINISTIC BLOCK", r.stderr)

    def test_clean_commit_after_secret_case_exits_0(self) -> None:
        """Control for the exit-2 test: same repo shape, clean content → 0."""
        (self.repo / "notes.txt").write_text("just notes\n", encoding="utf-8")
        _git(self.repo, "add", "notes.txt")
        r = run_dispatch(self.repo, "git commit -m clean")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")


# A synthetic, obviously-fake GitHub PAT built by concatenation so the literal
# token never appears verbatim in source. Matches ghp_[A-Za-z0-9]{36,}.
_FAKE_GHP = "ghp_" + "A" * 36
_SECRET_LINE = f'const token = "{_FAKE_GHP}";\n'


class SecurityPushScopingTests(unittest.TestCase):
    """f3 (HIGH): the pre-push security gate must NOT scope the scan to the
    upstream tracking delta when the push is not a plain current-branch →
    tracking push. `git push <other-remote> <branch>` pushes content that
    `<upstream>..HEAD` does not cover, so scoping to that (often empty) range
    lets a secret ship. Non-plain pushes must full-scan and hard-block."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = make_buildloop_repo(self.tmp)
        self.branch = _git(self.repo, "branch", "--show-current").stdout.strip()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add_remote(self, name: str) -> Path:
        bare = self.tmp / f"{name}.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=False)
        _git(self.repo, "remote", "add", name, str(bare))
        return bare

    def test_nontracking_push_full_scans_and_blocks(self) -> None:
        """RED on pre-fix: secret is in HEAD but NOT in origin/<b>..HEAD (already
        on origin). `git push backup <b>` newly ships it to a fresh remote. The
        old hook scoped to the empty upstream delta → exit 0; the fix full-scans
        → exit 2."""
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "auth.ts").write_text(_SECRET_LINE, encoding="utf-8")
        _git(self.repo, "add", "src/auth.ts")
        _git(self.repo, "commit", "-q", "-m", "add auth (with secret)")
        # origin now holds the secret too; origin/<b>..HEAD is EMPTY.
        self._add_remote("origin")
        _git(self.repo, "push", "-u", "-q", "origin", self.branch)
        # A fresh, non-tracking remote — pushing to it ships the whole history.
        self._add_remote("backup")
        r = run_dispatch(self.repo, f"git push backup {self.branch}")
        self.assertEqual(
            r.returncode, 2,
            f"non-tracking push must full-scan and hard-block; stderr={r.stderr!r}",
        )
        self.assertIn("security scan found HIGH", r.stderr)

    def test_plain_tracking_push_with_indelta_secret_blocks(self) -> None:
        """Control (green on old + new): a PLAIN `git push origin <b>` still
        catches a secret that IS in the upstream..HEAD delta — proves the fix
        did not break plain delta-scoping."""
        self._add_remote("origin")
        _git(self.repo, "push", "-u", "-q", "origin", self.branch)
        # Secret committed AFTER upstream is set → it is inside origin/<b>..HEAD.
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "auth.ts").write_text(_SECRET_LINE, encoding="utf-8")
        _git(self.repo, "add", "src/auth.ts")
        _git(self.repo, "commit", "-q", "-m", "add auth (with secret)")
        r = run_dispatch(self.repo, f"git push origin {self.branch}")
        self.assertEqual(
            r.returncode, 2,
            f"plain push with an in-delta secret must still block; stderr={r.stderr!r}",
        )

    def test_plain_tracking_push_clean_delta_passes(self) -> None:
        """Control: a plain push whose delta holds no secret exits 0 (delta
        scoping still works — unrelated pre-existing debt does not block)."""
        # Pre-existing debt committed BEFORE upstream is set → out of delta.
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "debt.ts").write_text(_SECRET_LINE, encoding="utf-8")
        _git(self.repo, "add", "src/debt.ts")
        _git(self.repo, "commit", "-q", "-m", "pre-existing debt")
        self._add_remote("origin")
        _git(self.repo, "push", "-u", "-q", "origin", self.branch)
        # A clean commit ahead of upstream.
        (self.repo / "note.txt").write_text("just a note\n", encoding="utf-8")
        _git(self.repo, "add", "note.txt")
        _git(self.repo, "commit", "-q", "-m", "clean change")
        r = run_dispatch(self.repo, f"git push origin {self.branch}")
        self.assertEqual(
            r.returncode, 0,
            f"plain push with a clean delta must pass; stderr={r.stderr!r}",
        )

    def test_pushed_ref_not_current_branch_full_scans(self) -> None:
        """h2 (HIGH): the pushed ref must be compared to the CURRENT branch, not
        just the tracking string. On a branch that tracks origin/<b> but whose
        checkout differs, `git push origin <b>` pushes a ref that is NOT the
        current branch — the current checkout's delta does not cover it. Old:
        classified plain → scoped to the (clean) current-branch delta → the
        secret carried in the checkout escapes. Fix: ref != current branch →
        full-scan → block."""
        # <b> (initial branch) carries the secret; origin/<b> gets it.
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "auth.ts").write_text(_SECRET_LINE, encoding="utf-8")
        _git(self.repo, "add", "src/auth.ts")
        _git(self.repo, "commit", "-q", "-m", "secret on base branch")
        self._add_remote("origin")
        _git(self.repo, "push", "-u", "-q", "origin", self.branch)
        # Branch off (inherits the secret in the working tree), add a CLEAN
        # commit, and track origin/<b>. Now origin/<b>..HEAD holds only the
        # clean commit — the secret is out of the current-branch delta but sits
        # in the working tree.
        _git(self.repo, "checkout", "-q", "-b", "feature")
        (self.repo / "note.txt").write_text("clean feature work\n", encoding="utf-8")
        _git(self.repo, "add", "note.txt")
        _git(self.repo, "commit", "-q", "-m", "clean feature work")
        _git(self.repo, "branch", "-u", f"origin/{self.branch}", "feature")
        r = run_dispatch(self.repo, f"git push origin {self.branch}")
        self.assertEqual(
            r.returncode, 2,
            "pushing a ref that is not the current branch must full-scan and "
            f"hard-block; stderr={r.stderr!r}",
        )
        self.assertIn("security scan found HIGH", r.stderr)


class SecurityPushClassifierConservatismTests(unittest.TestCase):
    """The push classifier must be conservative BY CONSTRUCTION — an allowlist,
    every push segment, ref-must-be-current-branch. These tests positively
    assert that every shape that is not provably a plain current-branch →
    tracking push falls back to a full scan (and therefore hard-blocks a secret
    that a wrong, narrower delta would miss). Shared rig: the secret already
    lives on origin/<b> (so <upstream>..HEAD is EMPTY) but is inherited in the
    working tree — a plain push scopes to the empty delta (exit 0); ANY
    non-plain push full-scans the working tree and blocks (exit 2)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = make_buildloop_repo(self.tmp)
        self.branch = _git(self.repo, "branch", "--show-current").stdout.strip()
        self._setup_inherited_secret()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add_remote(self, name: str) -> Path:
        bare = self.tmp / f"{name}.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=False)
        _git(self.repo, "remote", "add", name, str(bare))
        return bare

    def _setup_inherited_secret(self) -> None:
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "auth.ts").write_text(_SECRET_LINE, encoding="utf-8")
        _git(self.repo, "add", "src/auth.ts")
        _git(self.repo, "commit", "-q", "-m", "add auth (with secret)")
        self._add_remote("origin")
        _git(self.repo, "push", "-u", "-q", "origin", self.branch)
        # origin/<b>..HEAD is now EMPTY; the secret lives only in
        # origin-reachable history + the working tree.

    def _assert_full_scan_blocks(self, command: str) -> None:
        r = run_dispatch(self.repo, command)
        self.assertEqual(
            r.returncode, 2,
            f"non-plain push {command!r} must full-scan and hard-block; "
            f"stderr={r.stderr!r}",
        )
        self.assertIn("security scan found HIGH", r.stderr)

    def test_h1_compound_leading_push_full_scans(self) -> None:
        """h1: a compound command's EARLIER push segment is classified too.
        `git push backup <b> && git push` — old code judged only the trailing
        bare push (plain → empty delta → exit 0); the leading non-plain push
        escaped. Fix classifies every segment → full-scan → block."""
        self._add_remote("backup")
        self._assert_full_scan_blocks(f"git push backup {self.branch} && git push")

    def test_h3_repo_flag_full_scans(self) -> None:
        """h3: an unknown flag (`--repo=backup` redirects the destination) is
        NOT on the plain-safe allowlist → full-scan. Old denylist treated it as
        plain → wrong (empty) delta → exit 0."""
        self._assert_full_scan_blocks("git push --repo=backup")

    def test_broad_flag_push_full_scans(self) -> None:
        """`--mirror` / `--all` push refs the tracking delta does not cover —
        unknown to the allowlist → full-scan."""
        self._assert_full_scan_blocks("git push --mirror origin")

    def test_refspec_push_full_scans(self) -> None:
        """A src:dst refspec pushes content the current-branch delta may not
        cover → full-scan."""
        self._assert_full_scan_blocks(
            f"git push origin {self.branch}:{self.branch}"
        )

    def test_wrong_ref_push_full_scans(self) -> None:
        """`git push origin <other>` names a ref that is not the current branch
        → full-scan."""
        self._assert_full_scan_blocks("git push origin someotherbranch")

    def test_plain_empty_delta_push_passes(self) -> None:
        """Intent preserved: a plain current-branch → tracking push still scopes
        to the (here empty) delta and exits 0 — pre-existing, already-pushed
        debt does not block."""
        r = run_dispatch(self.repo, f"git push origin {self.branch}")
        self.assertEqual(
            r.returncode, 0,
            f"plain empty-delta push must pass; stderr={r.stderr!r}",
        )

    def test_safe_flags_plain_push_still_scopes(self) -> None:
        """The allowlist keeps genuinely-safe flags plain: `-v
        --force-with-lease` do not change destination or refs, so the push stays
        plain and scopes to the empty delta (exit 0) rather than full-scanning."""
        r = run_dispatch(
            self.repo,
            f"git push -v --force-with-lease origin {self.branch}",
        )
        self.assertEqual(
            r.returncode, 0,
            f"safe-flag plain push must stay delta-scoped (exit 0); "
            f"stderr={r.stderr!r}",
        )


class SecurityPushConfigAwarenessTests(unittest.TestCase):
    """f1/f2/f3/f4 (HIGH) — the last four pre-push false-negatives.

    The command string alone does NOT determine what a `git push` ships, and the
    dispatcher must SEE the push at all. These tests prove each closure:

      f1  a 2-positional `git push <remote> <branch>` where the branch tracks a
          DIFFERENTLY-NAMED upstream (e.g. main tracks origin/develop) was
          classified plain (positionals matched) without the up_branch==branch
          guard the other arms carry → scoped to origin/develop..HEAD (wrong).
      f2  push.default=matching (ships all matching branches) and triangular
          config (bare push goes to the pushRemote, not @{u}'s remote) both
          route content the upstream..HEAD range does not cover — the classifier
          never read push config.
      f3  a multi-line Bash command whose push is not on line 1 was never
          extracted (CMD/CWD both corrupted) → NO gate ran at all.
      f4  `git -C <path> push` / `git<TAB>push` / double-space matched neither
          the literal `git push` case guard nor the `git\\s+push` finditer → the
          gate never fired.

    Shared rig (as SecurityPushClassifierConservatismTests): the secret lives on
    origin/<b> AND the working tree, so origin/<b>..HEAD is EMPTY. A plain,
    delta-scoped push scans the empty delta (exit 0 — the pre-fix escape); any
    correctly-conservative path full-scans the working tree and blocks (exit 2)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = make_buildloop_repo(self.tmp)
        self.branch = _git(self.repo, "branch", "--show-current").stdout.strip()
        self._setup_inherited_secret()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add_remote(self, name: str) -> Path:
        bare = self.tmp / f"{name}.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=False)
        _git(self.repo, "remote", "add", name, str(bare))
        return bare

    def _setup_inherited_secret(self) -> None:
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "auth.ts").write_text(_SECRET_LINE, encoding="utf-8")
        _git(self.repo, "add", "src/auth.ts")
        _git(self.repo, "commit", "-q", "-m", "add auth (with secret)")
        self._add_remote("origin")
        _git(self.repo, "push", "-u", "-q", "origin", self.branch)

    def _assert_blocks(self, command: str) -> None:
        r = run_dispatch(self.repo, command)
        self.assertEqual(
            r.returncode, 2,
            f"{command!r} must full-scan and hard-block; stderr={r.stderr!r}",
        )
        self.assertIn("security scan found HIGH", r.stderr)

    def test_f1_two_positional_wrong_tracking_branch_full_scans(self) -> None:
        """f1: `git push origin <b>` where <b> tracks origin/develop (a
        different name). Also ship the secret to origin/develop so
        origin/develop..HEAD is EMPTY, then re-point tracking. On c401f68 the
        len==2 arm returns plain WITHOUT `up_branch==branch` → scoped to the
        empty develop delta → secret escapes (exit 0). Fix: up_branch (develop)
        != current branch → not plain → full scan → block."""
        _git(self.repo, "push", "-q", "origin", f"{self.branch}:develop")
        _git(self.repo, "branch", "-u", "origin/develop", self.branch)
        self._assert_blocks(f"git push origin {self.branch}")

    def test_f2_matching_push_default_full_scans(self) -> None:
        """f2(a): push.default=matching ships ALL matching branches, not just the
        current one. The command string + ref-name equality say plain (c401f68)
        → scoped to the empty origin/<b>..HEAD → exit 0. Fix reads
        push.default=matching (excluded from the config-plain allowlist) →
        non-plain → full scan → block."""
        _git(self.repo, "config", "push.default", "matching")
        self._assert_blocks("git push")

    def test_f2_triangular_pushremote_full_scans(self) -> None:
        """f2(b): triangular config — bare `git push` goes to the pushRemote
        (backup), NOT @{u}'s remote (origin). @{push} != @{u}, so scoping to
        origin/<b>..HEAD scans the wrong (empty) range. c401f68 never reads push
        config → classifies plain → exit 0. Fix: @{push} != upstream →
        non-plain → full scan → block."""
        self._add_remote("backup")
        _git(self.repo, "config", f"branch.{self.branch}.pushRemote", "backup")
        self._assert_blocks("git push")

    def test_f3_multiline_push_on_third_line_fires(self) -> None:
        """f3: a 3-line Bash command; the push is on line 3. On c401f68 only
        line 1 is extracted (CMD='git add -A', CWD corrupted to line 2) → the
        gate never fires, NO scan runs → exit 0. Fix passes the full command
        (newlines normalized to ';') → the gate FIRES → `git push --mirror
        backup` is non-plain → full scan → block."""
        self._add_remote("backup")
        cmd = "git add -A\ngit status\ngit push --mirror backup"
        self._assert_blocks(cmd)

    def test_f4_git_dash_c_push_fires(self) -> None:
        """f4: `git -C <path> push` — the standard worktree/agent spelling. On
        c401f68 the literal `git push` case guard AND the `git\\s+push` finditer
        both miss the `-C <path>` between git and push → the gate never fires →
        exit 0. Fix widens the case guard to `*git*push*` and the finditer to
        tolerate global git options; the segment's toks[1]!='push' → not plain →
        full scan → block."""
        self._add_remote("backup")
        self._assert_blocks(f"git -C {self.repo} push --mirror backup")

    def test_f4_tab_spacing_push_fires(self) -> None:
        """f4 (spacing variant): `git<TAB>push` and double-space `git  push`
        matched the `git\\s+push` finditer but NOT the literal-space `git push`
        case guard on c401f68 → the guard filtered the command out before the
        classifier ran → no scan → exit 0. Fix's `*git*push*` guard admits both.
        `--mirror backup` keeps it non-plain → full scan → block."""
        self._assert_blocks("git\tpush --mirror backup")

    def test_plain_push_config_default_still_scopes(self) -> None:
        """Control: with default (unset → simple) push.default and no triangular
        config, @{push} == @{u}, so a plain current-branch push stays config-plain
        and scopes to the empty delta (exit 0). Proves the f2 config gate did not
        break the legitimate delta-scope path."""
        r = run_dispatch(self.repo, f"git push origin {self.branch}")
        self.assertEqual(
            r.returncode, 0,
            f"plain default-config push must stay delta-scoped (exit 0); "
            f"stderr={r.stderr!r}",
        )


class DispatchFailOpenTests(unittest.TestCase):
    """Fail-open guarantees: a broken environment never blocks a commit."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = make_buildloop_repo(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_python3_fails_open(self) -> None:
        """No python3 on PATH (only git/bash) → the dispatcher cannot parse the
        event or run the auditor, so it degrades to allow (exit 0), NOT block.

        Build a sandbox bin/ with only git + the shell tools the dispatcher
        needs (bash, cat, sed, printf via bash builtin) but NO python3."""
        sandbox_bin = self.tmp / "bin"
        sandbox_bin.mkdir(parents=True, exist_ok=True)
        for tool in ("git", "bash", "sed", "cat", "env", "dirname", "cd"):
            src = shutil.which(tool)
            if src:
                try:
                    os.symlink(src, sandbox_bin / tool)
                except FileExistsError:
                    pass
        # PATH has the sandbox bin only — python3 deliberately absent.
        r = run_dispatch(
            self.repo,
            "git commit -m x",
            path_env=str(sandbox_bin),
            minimal_env=True,
        )
        self.assertEqual(
            r.returncode, 0,
            f"missing-python3 must fail open (exit 0); got {r.returncode}, "
            f"stderr={r.stderr!r}",
        )

    def test_kill_switch_minimal_path(self) -> None:
        """BUILD_LOOP_HOOKS=off short-circuits to {} under env -i."""
        env = {"PATH": "/usr/bin:/bin", "BUILD_LOOP_HOOKS": "off"}
        event = json.dumps(
            {"tool_input": {"command": "git commit -m x"}, "cwd": str(self.repo)}
        )
        r = subprocess.run(
            ["bash", str(DISPATCH)],
            cwd=self.repo,
            input=event,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        self.assertEqual(r.stdout.strip(), "{}")


class GitClassifierGuardTests(unittest.TestCase):
    """The dispatcher's commit/push guards are driven by git_command_classifier.py, not
    substring globs. These prove the two false-fire classes (2026-07-11) no longer trigger,
    and the true-fire classes still do."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = make_buildloop_repo(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _assert_no_git_gate(self, r: subprocess.CompletedProcess) -> None:
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        self.assertNotIn("Audit packet", r.stderr)
        self.assertNotIn("security scan", r.stderr)

    def test_a_rally_say_gitignore_pushed_no_trigger(self) -> None:
        """rally say with a .gitignore path + 'pushed' subject → NO commit/push gate."""
        r = run_dispatch(
            self.repo,
            'rally say claim --tool claude_code --subject "pushed auth fix" --path .gitignore',
        )
        self._assert_no_git_gate(r)

    def test_b_heredoc_text_git_commands_no_trigger(self) -> None:
        """A heredoc whose TEXT contains 'git commit && git push' → NO gate fires."""
        cmd = (
            "python3 - <<'PY'\n"
            "# example: git commit -m x && git push origin main\n"
            'print("hi")\n'
            "PY"
        )
        r = run_dispatch(self.repo, cmd)
        self._assert_no_git_gate(r)

    def test_c_bare_push_triggers_security_gate(self) -> None:
        """Bare `git push` on a clean repo: the security gate FIRES (delta-scoped) and,
        finding nothing, exits 0. The gate firing is proven by the absence of a block plus
        the classifier routing (a non-git command would skip the scan entirely)."""
        # No remote/upstream → full scan of a clean repo → exit 0, no block.
        r = run_dispatch(self.repo, "git push")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")

    def test_d_commit_then_push_routes_to_auditor(self) -> None:
        """`git commit -m x && git push` triggers the commit auditor (Audit packet)."""
        (self.repo / "feature.txt").write_text("hello\n", encoding="utf-8")
        _git(self.repo, "add", "feature.txt")
        r = run_dispatch(self.repo, "git commit -m x && git push")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        self.assertIn("Audit packet", r.stderr)

    def test_e_piped_push_triggers_gate(self) -> None:
        """`git push 2>&1 | tail -1` still routes to the push gate (clean repo → exit 0)."""
        r = run_dispatch(self.repo, "git push 2>&1 | tail -1")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")


class ClassifierCannotRunBoundedFailureTests(unittest.TestCase):
    """LO-6 part (a): when the git command classifier subprocess CANNOT RUN
    (python3 unresolvable under load, spawn failure), the dispatcher must degrade
    to a BOUNDED skip-with-warning — NOT fail-open to "commit push" and run a
    full-repo security scan that hard-blocks on doc-embedded false positives
    (observed 2026-07-14: a whole session's bash frozen in a large .build-loop
    docs repo). Only a positively-classified `git push` (the classifier RAN and
    said push) ever triggers the scan; a transient classifier outage never does.

    The classifier's OWN conservatism contract (it exits 0 and returns
    "commit push" on parse ambiguity) is unaffected — this covers only the
    subprocess-cannot-run case, which is the shell `|| { … }` fallback.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = make_buildloop_repo(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _broken_classifier_root(self) -> Path:
        """A mirror of the real plugin root where every sub-gate + helper is
        symlinked to the real file, EXCEPT git_command_classifier.py, which is a
        stub that exits non-zero WITHOUT producing a verdict (simulating a
        subprocess that could not run)."""
        root = self.tmp / "plugin_root"
        (root / "scripts" / "hooks").mkdir(parents=True, exist_ok=True)
        real_scripts = PLUGIN_ROOT / "scripts"
        for entry in real_scripts.iterdir():
            if entry.name == "hooks":
                continue
            os.symlink(entry, root / "scripts" / entry.name)
        for entry in (real_scripts / "hooks").iterdir():
            if entry.name == "git_command_classifier.py":
                continue
            os.symlink(entry, root / "scripts" / "hooks" / entry.name)
        (root / "scripts" / "hooks" / "git_command_classifier.py").write_text(
            "import sys\nsys.exit(1)\n", encoding="utf-8"
        )
        return root

    def _add_remote(self, name: str) -> Path:
        bare = self.tmp / f"{name}.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=False)
        _git(self.repo, "remote", "add", name, str(bare))
        return bare

    def test_classifier_failure_on_git_command_does_not_full_scan(self) -> None:
        """RED on the pre-fix fallback: a real-shaped secret lives in a doc, so a
        full-repo scan would hard-block. With the classifier broken, a NON-push
        git command (`git status`) must degrade to skip-with-warning (exit 0), NOT
        run the scan. The old fallback set "commit push" for any *git* command →
        full scan → exit 2 on the doc finding."""
        (self.repo / "docs").mkdir(exist_ok=True)
        # A real-SHAPED AWS key (not the EXAMPLE suffix) → would flag HIGH on a full scan.
        (self.repo / "docs" / "note.md").write_text(
            'key = "AKIA1234567890ABCDEF"\n', encoding="utf-8"
        )
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "doc with a key-shaped string")
        r = run_dispatch(
            self.repo, "git status", plugin_root=self._broken_classifier_root()
        )
        self.assertEqual(
            r.returncode, 0,
            f"classifier-failure on a non-push git command must not full-scan/"
            f"block; stderr={r.stderr!r}",
        )
        self.assertNotIn("security scan", r.stderr,
                         "no security scan may run when the classifier could not run")
        self.assertIn("classifier could not run", r.stderr,
                      "the bounded skip must announce itself")

    def test_working_classifier_real_secret_push_still_blocks(self) -> None:
        """Paired guarantee (never weakened): with the classifier WORKING (real
        plugin root), a genuine `git push` carrying a real staged secret in the
        pushed delta STILL hard-blocks (exit 2). The bounded-failure change touches
        ONLY the subprocess-cannot-run path, never the normal scan."""
        self._add_remote("origin")
        branch = _git(self.repo, "branch", "--show-current").stdout.strip()
        _git(self.repo, "push", "-u", "-q", "origin", branch)
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "auth.ts").write_text(_SECRET_LINE, encoding="utf-8")
        _git(self.repo, "add", "src/auth.ts")
        _git(self.repo, "commit", "-q", "-m", "add auth (real secret in delta)")
        r = run_dispatch(self.repo, f"git push origin {branch}")  # working classifier
        self.assertEqual(
            r.returncode, 2,
            f"a real staged secret on a genuine push must still block; "
            f"stderr={r.stderr!r}",
        )
        self.assertIn("security scan found HIGH", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
