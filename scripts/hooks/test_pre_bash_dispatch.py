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
