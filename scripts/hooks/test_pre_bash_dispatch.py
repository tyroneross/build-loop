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
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
