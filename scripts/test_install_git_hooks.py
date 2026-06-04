#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/install_git_hooks.py + end-to-end hook behaviour.

Covers (mandated by the build-loop SELF-MOD SAFETY GATE for any new script):

- ``--install`` is idempotent (re-running doesn't break the installed hook).
- ``--uninstall`` removes a build-loop hook but preserves a foreign hook.
- ``--install`` refuses to overwrite a foreign hook unless ``--force``.
- ``--install --force`` backs up + replaces a foreign hook; ``--uninstall``
  restores the backup.
- ``--status`` correctly reports installed / not-installed / foreign.
- End-to-end: a real ``git push`` to a protected branch IS blocked when the
  marker is set and ALLOWED when not.  This is the integration contract — we
  do NOT mock the hook layer.
- End-to-end: ``BUILDLOOP_PUSH_HOLD_BYPASS=1`` allows an otherwise-blocked
  push.
- End-to-end: a hook internal error (broken push_hold import) fails OPEN
  (exit 0) — the user is never wedged.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "install_git_hooks.py"
PUSH_HOLD_SCRIPT = REPO_ROOT / "scripts" / "push_hold.py"
DEPLOYMENT_POLICY_SCRIPT = REPO_ROOT / "scripts" / "deployment_policy.py"
HOOK_SOURCE = REPO_ROOT / "hooks" / "git" / "pre-push"


def _run(*args: str, env_extra: dict[str, str] | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(args, capture_output=True, text=True, env=env, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def _make_bare_remote(parent: Path) -> Path:
    remote = parent / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    return remote


def _make_work_repo(parent: Path, remote: Path) -> Path:
    """Build a workdir that mirrors the build-loop repo enough for the hook
    to import push_hold + deployment_policy, with a real .git and a remote."""
    workdir = parent / "workrepo"
    workdir.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(workdir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workdir), "remote", "add", "origin", str(remote)], check=True, capture_output=True)
    # Identity (so commits succeed in CI / sandboxes without global config).
    subprocess.run(["git", "-C", str(workdir), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(workdir), "config", "user.name", "T"], check=True)

    # Copy the three files the hook depends on, plus the hook source itself.
    scripts_dir = workdir / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(PUSH_HOLD_SCRIPT, scripts_dir / "push_hold.py")
    shutil.copy2(DEPLOYMENT_POLICY_SCRIPT, scripts_dir / "deployment_policy.py")
    shutil.copy2(SCRIPT, scripts_dir / "install_git_hooks.py")
    hooks_src_dir = workdir / "hooks" / "git"
    hooks_src_dir.mkdir(parents=True)
    shutil.copy2(HOOK_SOURCE, hooks_src_dir / "pre-push")

    # Initial commit so we have something to push.
    (workdir / "README.md").write_text("test\n")
    subprocess.run(["git", "-C", str(workdir), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workdir), "commit", "-m", "init"], check=True, capture_output=True)
    return workdir


class TestInstallerIdempotence(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.parent = Path(self._td.name)
        self.workdir = self.parent / "repo"
        self.workdir.mkdir()
        subprocess.run(["git", "init", str(self.workdir)], check=True, capture_output=True)
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        shutil.copy2(SCRIPT, scripts_dir / "install_git_hooks.py")
        hooks_src_dir = self.workdir / "hooks" / "git"
        hooks_src_dir.mkdir(parents=True)
        shutil.copy2(HOOK_SOURCE, hooks_src_dir / "pre-push")

    def tearDown(self):
        self._td.cleanup()

    def test_install_then_install_idempotent(self):
        rc1, out1, _ = _run(sys.executable, str(SCRIPT), "--install", "--repo", str(self.workdir), "--json")
        self.assertEqual(rc1, 0, out1)
        body1 = json.loads(out1)
        self.assertTrue(body1["results"][0]["installed"])
        installed_path = Path(body1["results"][0]["path"])
        self.assertTrue(installed_path.exists())
        self.assertTrue(installed_path.stat().st_mode & 0o111)

        # Second install: idempotent — still reports installed=True, no error.
        rc2, out2, _ = _run(sys.executable, str(SCRIPT), "--install", "--repo", str(self.workdir), "--json")
        self.assertEqual(rc2, 0, out2)
        body2 = json.loads(out2)
        self.assertTrue(body2["results"][0]["installed"])

    def test_status(self):
        rc, out, _ = _run(sys.executable, str(SCRIPT), "--status", "--repo", str(self.workdir), "--json")
        self.assertEqual(rc, 0)
        body = json.loads(out)
        self.assertFalse(body["results"][0]["installed"])

        _run(sys.executable, str(SCRIPT), "--install", "--repo", str(self.workdir))
        rc, out, _ = _run(sys.executable, str(SCRIPT), "--status", "--repo", str(self.workdir), "--json")
        self.assertEqual(rc, 0)
        body = json.loads(out)
        self.assertTrue(body["results"][0]["installed"])

    def test_install_refuses_foreign_without_force(self):
        hooks_dir = self.workdir / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        foreign = hooks_dir / "pre-push"
        foreign.write_text("#!/bin/sh\necho 'foreign hook'\n")
        foreign.chmod(0o755)

        rc, out, _ = _run(sys.executable, str(SCRIPT), "--install", "--repo", str(self.workdir), "--json")
        self.assertEqual(rc, 1, out)
        body = json.loads(out)
        self.assertTrue(body["results"][0]["skipped"])
        self.assertIn("foreign", body["results"][0]["reason"])
        self.assertEqual(foreign.read_text(), "#!/bin/sh\necho 'foreign hook'\n")

    def test_install_force_backs_up_foreign_uninstall_restores(self):
        hooks_dir = self.workdir / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        foreign = hooks_dir / "pre-push"
        foreign_body = "#!/bin/sh\necho 'foreign hook'\n"
        foreign.write_text(foreign_body)
        foreign.chmod(0o755)

        rc, out, _ = _run(sys.executable, str(SCRIPT), "--install", "--repo", str(self.workdir), "--force", "--json")
        self.assertEqual(rc, 0, out)
        backup = hooks_dir / "pre-push.pre-buildloop.bak"
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(), foreign_body)
        installed = (hooks_dir / "pre-push").read_text()
        self.assertIn("build-loop", installed)

        rc, out, _ = _run(sys.executable, str(SCRIPT), "--uninstall", "--repo", str(self.workdir), "--json")
        self.assertEqual(rc, 0, out)
        body = json.loads(out)
        self.assertTrue(body["results"][0]["removed"])
        self.assertTrue(body["results"][0]["restored_backup"])
        self.assertEqual((hooks_dir / "pre-push").read_text(), foreign_body)

    def test_uninstall_when_not_installed(self):
        rc, out, _ = _run(sys.executable, str(SCRIPT), "--uninstall", "--repo", str(self.workdir), "--json")
        self.assertEqual(rc, 0)
        body = json.loads(out)
        self.assertFalse(body["results"][0]["removed"])


class TestHookEndToEnd(unittest.TestCase):
    """Drive a real ``git push`` through the installed hook."""

    def setUp(self):
        self._td = TemporaryDirectory()
        self.parent = Path(self._td.name)
        self.remote = _make_bare_remote(self.parent)
        self.workdir = _make_work_repo(self.parent, self.remote)
        # Install the hook.
        rc, out, err = _run(sys.executable, str(SCRIPT), "--install", "--repo", str(self.workdir), "--json")
        self.assertEqual(rc, 0, f"install failed: {out}\n{err}")

    def tearDown(self):
        self._td.cleanup()

    def _push(self, branch="main", env_extra=None) -> tuple[int, str, str]:
        return _run(
            "git", "-C", str(self.workdir), "push", "origin", branch,
            env_extra=env_extra,
        )

    def test_no_hold_allows_push_to_main(self):
        """The whole-point regression guard — no hold + push to main MUST work."""
        rc, out, err = self._push("main")
        self.assertEqual(rc, 0, f"out={out}\nerr={err}")

    def test_hold_blocks_push_to_main(self):
        # Set marker.
        rc, out, _ = _run(
            sys.executable, str(PUSH_HOLD_SCRIPT),
            "--workdir", str(self.workdir),
            "--set", "--reason", "briefed: do-not-push", "--source", "orchestrator",
            "--json",
        )
        self.assertEqual(rc, 0, out)
        rc, out, err = self._push("main")
        self.assertNotEqual(rc, 0, f"hook failed to block: out={out}\nerr={err}")
        # Block message visible.
        self.assertIn("PUSH HOLD", err)
        self.assertIn("do-not-push", err)

    def test_hold_allows_push_to_non_protected_branch(self):
        rc, out, _ = _run(
            sys.executable, str(PUSH_HOLD_SCRIPT),
            "--workdir", str(self.workdir),
            "--set", "--reason", "briefed: do-not-push", "--json",
        )
        self.assertEqual(rc, 0, out)
        # Create a non-protected branch and push it.
        subprocess.run(["git", "-C", str(self.workdir), "checkout", "-b", "feature/x"], check=True, capture_output=True)
        rc, out, err = self._push("feature/x")
        self.assertEqual(rc, 0, f"out={out}\nerr={err}")

    def test_bypass_env_overrides_hold(self):
        rc, out, _ = _run(
            sys.executable, str(PUSH_HOLD_SCRIPT),
            "--workdir", str(self.workdir),
            "--set", "--reason", "briefed: do-not-push", "--json",
        )
        self.assertEqual(rc, 0, out)
        rc, out, err = self._push("main", env_extra={"BUILDLOOP_PUSH_HOLD_BYPASS": "1"})
        self.assertEqual(rc, 0, f"out={out}\nerr={err}")
        # Bypass is logged in audit-log.md.
        log = self.workdir / ".build-loop" / "audit-log.md"
        self.assertTrue(log.exists())
        self.assertIn("BYPASS", log.read_text())

    def test_hook_internal_error_fails_open(self):
        """Break the push_hold import → hook MUST allow the push (fail-open)."""
        # Replace push_hold.py with a syntax-error file.
        ph = self.workdir / "scripts" / "push_hold.py"
        ph.write_text("def evaluate_push(\n  not valid python\n")
        rc, out, err = self._push("main")
        self.assertEqual(rc, 0, f"hook failed-closed on internal error: out={out}\nerr={err}")
        # The stderr should carry our "internal error — allowing push" diagnostic.
        self.assertIn("internal error", err)

    def test_state_blocking_verdict_blocks_push(self):
        """End-to-end signal #2 — unresolved blocking verdict in state.json."""
        from datetime import datetime, timezone, timedelta
        recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        (self.workdir / ".build-loop").mkdir(exist_ok=True)
        state = {
            "runs": [
                {
                    "run_id": "run_end_to_end",
                    "created_at": recent_ts,
                    "judge_decisions": [
                        {
                            "verdict": "suggest_correction",
                            "judge": "independent-auditor",
                            "finding_ids": ["f-e2e-1"],
                        }
                    ],
                }
            ]
        }
        (self.workdir / ".build-loop" / "state.json").write_text(json.dumps(state))
        rc, out, err = self._push("main")
        self.assertNotEqual(rc, 0, f"state-signal failed to block: out={out}\nerr={err}")
        self.assertIn("suggest_correction", err)


class TestSessionStartAutoInstall(unittest.TestCase):
    """f3: session-start-git-hooks.sh auto-installs the pre-push hook (idempotent)."""

    HOOK_SCRIPT = REPO_ROOT / "hooks" / "session-start-git-hooks.sh"

    def setUp(self):
        self._td = TemporaryDirectory()
        self.parent = Path(self._td.name)
        self.workdir = self.parent / "repo"
        self.workdir.mkdir()
        subprocess.run(["git", "init", str(self.workdir)], check=True, capture_output=True)
        # Mirror the build-loop layout the hook and installer expect.
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        shutil.copy2(SCRIPT, scripts_dir / "install_git_hooks.py")
        hooks_src_dir = self.workdir / "hooks" / "git"
        hooks_src_dir.mkdir(parents=True)
        shutil.copy2(HOOK_SOURCE, hooks_src_dir / "pre-push")

    def tearDown(self):
        self._td.cleanup()

    def test_session_start_script_exists_and_is_executable(self):
        self.assertTrue(self.HOOK_SCRIPT.exists(), "session-start-git-hooks.sh must exist")
        import stat
        mode = self.HOOK_SCRIPT.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "session-start-git-hooks.sh must be executable")

    def test_session_start_installs_pre_push(self):
        """Running session-start-git-hooks.sh installs the pre-push hook."""
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = str(self.workdir)
        env["CLAUDE_PROJECT_DIR"] = str(self.workdir)
        rc, _, err = _run(
            "bash", str(self.HOOK_SCRIPT),
            env_extra={"CLAUDE_PLUGIN_ROOT": str(self.workdir), "CLAUDE_PROJECT_DIR": str(self.workdir)},
        )
        self.assertEqual(rc, 0, f"session-start hook failed: {err}")
        hook_dst = self.workdir / ".git" / "hooks" / "pre-push"
        self.assertTrue(hook_dst.exists(), "pre-push hook must be installed after session-start")
        self.assertTrue(hook_dst.stat().st_mode & 0o111, "installed hook must be executable")

    def test_session_start_is_idempotent(self):
        """Running session-start-git-hooks.sh twice must not corrupt the hook."""
        for _ in range(2):
            _run(
                "bash", str(self.HOOK_SCRIPT),
                env_extra={"CLAUDE_PLUGIN_ROOT": str(self.workdir), "CLAUDE_PROJECT_DIR": str(self.workdir)},
            )
        hook_dst = self.workdir / ".git" / "hooks" / "pre-push"
        self.assertTrue(hook_dst.exists())
        content = hook_dst.read_text(encoding="utf-8")
        self.assertIn("build-loop", content)

    def test_session_start_outside_git_repo_is_noop(self):
        """Running in a non-git directory must exit 0 silently (not fail)."""
        non_git = self.parent / "not_a_repo"
        non_git.mkdir()
        rc, _, _ = _run(
            "bash", str(self.HOOK_SCRIPT),
            env_extra={"CLAUDE_PLUGIN_ROOT": str(self.workdir), "CLAUDE_PROJECT_DIR": str(non_git)},
        )
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
