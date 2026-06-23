#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/worktree_isolation_lint.py.

The fail->pass demonstration is the core of the regression artifact:
  * An orphan-poller-style launchd job whose WorkingDirectory is a LIVE git
    checkout MUST produce a BLOCKER (proves old behavior fails).
  * The same job, once isolation is declared (worktree cwd, env var, or
    notify-only program), MUST pass (proves new behavior passes).

A real temporary git repo is created via subprocess so the
`git rev-parse --is-inside-work-tree` check exercises actual git.
"""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

# Make scripts/ importable.
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import worktree_isolation_lint as lint  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def _make_git_checkout(root: Path) -> Path:
    """Create a real git working tree at ``root`` and return it."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _write_plist(
    path: Path,
    *,
    label: str,
    program: str,
    working_directory: str | None = None,
    run_at_load: bool = True,
    keep_alive: bool | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    body: dict = {
        "Label": label,
        "ProgramArguments": [program],
        "RunAtLoad": run_at_load,
    }
    if keep_alive is not None:
        body["KeepAlive"] = keep_alive
    if working_directory is not None:
        body["WorkingDirectory"] = working_directory
    if env is not None:
        body["EnvironmentVariables"] = env
    with path.open("wb") as fh:
        plistlib.dump(body, fh)
    return path


@pytest.fixture()
def orphan_poller_program() -> str:
    # The real culprit's program path shape (an autonomy poller, not notify-only).
    return "/Users/x/.build-loop/apps/build-loop/watchers/codex_autonomy_poller.py"


# ---------------------------------------------------------------------------
# The fail -> pass demonstration (deliverable #1)
# ---------------------------------------------------------------------------
def test_orphan_poller_in_live_checkout_fails(tmp_path, orphan_poller_program):
    """OLD BEHAVIOR FAILS: poller cwd == live checkout → BLOCKER."""
    live_checkout = _make_git_checkout(tmp_path / "build-loop")
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    _write_plist(
        la_dir / "com.x.codex-autonomy-poller.build-loop.plist",
        label="com.x.codex-autonomy-poller.build-loop",
        program=orphan_poller_program,
        working_directory=str(live_checkout),
    )

    result = lint.run_lint(workdir=tmp_path, launch_agents_dir=la_dir)

    assert result["ok"] is False
    assert result["blocker_count"] == 1
    f = result["findings"][0]
    assert f["rule"] == "background-committer-in-live-checkout"
    assert f["severity"] == "BLOCKER"


def test_isolation_declared_via_worktree_cwd_passes(tmp_path, orphan_poller_program):
    """NEW BEHAVIOR PASSES: cwd is a dedicated worktree → no finding."""
    # A worktree path under build-loop.worktrees/ — matches the deliverable shape.
    worktree = _make_git_checkout(tmp_path / "build-loop.worktrees" / "codex-autonomy")
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    _write_plist(
        la_dir / "poller.plist",
        label="com.x.codex-autonomy-poller.build-loop",
        program=orphan_poller_program,
        working_directory=str(worktree),
    )

    result = lint.run_lint(workdir=tmp_path, launch_agents_dir=la_dir)

    assert result["ok"] is True
    assert result["blocker_count"] == 0


def test_isolation_declared_via_env_var_passes(tmp_path, orphan_poller_program):
    """NEW BEHAVIOR PASSES: explicit BUILD_LOOP_WORKTREE_ISOLATED=1 → no finding."""
    live_checkout = _make_git_checkout(tmp_path / "build-loop")
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    _write_plist(
        la_dir / "poller.plist",
        label="com.x.codex-autonomy-poller.build-loop",
        program=orphan_poller_program,
        working_directory=str(live_checkout),
        env={lint.ISOLATION_ENV_KEY: "1"},
    )

    result = lint.run_lint(workdir=tmp_path, launch_agents_dir=la_dir)

    assert result["ok"] is True
    assert result["blocker_count"] == 0


# ---------------------------------------------------------------------------
# Exemptions that must NOT fire (avoid false positives)
# ---------------------------------------------------------------------------
def test_notify_only_watcher_in_live_checkout_passes(tmp_path):
    """A notify-only rally watcher never commits → exempt even in a live checkout."""
    live_checkout = _make_git_checkout(tmp_path / "build-loop")
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    _write_plist(
        la_dir / "rally-watcher.plist",
        label="com.x.agent-rally-watcher",
        program="/repo/scripts/agent_rally_watcher/watch.py",
        working_directory=str(live_checkout),
    )

    result = lint.run_lint(workdir=tmp_path, launch_agents_dir=la_dir)

    assert result["ok"] is True


def test_non_autonomy_job_ignored(tmp_path):
    """A plain launchd job (not autonomy/poller/watcher) is out of scope."""
    live_checkout = _make_git_checkout(tmp_path / "build-loop")
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    _write_plist(
        la_dir / "com.apple.something.plist",
        label="com.apple.something",
        program="/usr/bin/true",
        working_directory=str(live_checkout),
    )

    result = lint.run_lint(workdir=tmp_path, launch_agents_dir=la_dir)

    assert result["ok"] is True


def test_cwd_not_a_git_checkout_ignored(tmp_path, orphan_poller_program):
    """A poller cwd that is not a git checkout has no HEAD/index to race."""
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    _write_plist(
        la_dir / "poller.plist",
        label="com.x.codex-autonomy-poller",
        program=orphan_poller_program,
        working_directory=str(plain_dir),
    )

    result = lint.run_lint(workdir=tmp_path, launch_agents_dir=la_dir)

    assert result["ok"] is True


def test_one_shot_job_ignored(tmp_path, orphan_poller_program):
    """A non-persistent job (no RunAtLoad / KeepAlive) is not a standing writer."""
    live_checkout = _make_git_checkout(tmp_path / "build-loop")
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    _write_plist(
        la_dir / "poller.plist",
        label="com.x.codex-autonomy-poller",
        program=orphan_poller_program,
        working_directory=str(live_checkout),
        run_at_load=False,
    )

    result = lint.run_lint(workdir=tmp_path, launch_agents_dir=la_dir)

    assert result["ok"] is True


def test_malformed_plist_fails_open(tmp_path, orphan_poller_program):
    """A malformed plist (real ~/Library/LaunchAgents has them) must not crash the lint."""
    live_checkout = _make_git_checkout(tmp_path / "build-loop")
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    # Write garbage that is not well-formed XML.
    (la_dir / "broken.plist").write_text("<?xml version=1.0?><not well & formed>")
    # And a real offending poller alongside it, to prove we still flag the valid one.
    _write_plist(
        la_dir / "poller.plist",
        label="com.x.codex-autonomy-poller",
        program=orphan_poller_program,
        working_directory=str(live_checkout),
    )

    result = lint.run_lint(workdir=tmp_path, launch_agents_dir=la_dir)

    # Did not crash; still caught the valid offender.
    assert result["blocker_count"] == 1


# ---------------------------------------------------------------------------
# In-repo wake-path contract (deliverable #2 enforcement)
# ---------------------------------------------------------------------------
def test_wake_path_grows_a_commit_fails(tmp_path):
    """If a canonical wake file grows a worktree-less commit, the lint blocks."""
    wake = tmp_path / "scripts" / "wake_scheduler.py"
    wake.parent.mkdir(parents=True)
    wake.write_text(
        'import subprocess\n'
        'subprocess.run(["git", "commit", "-m", "woke and committed"])\n'
    )

    findings = lint.lint_in_repo_wake_path(tmp_path)

    assert any(f["rule"] == "wake-path-grew-a-commit" for f in findings)


def test_wake_path_commit_inside_worktree_passes(tmp_path):
    """A commit guarded by worktree provisioning is fine (notify-only contract intact)."""
    wake = tmp_path / "scripts" / "wake_scheduler.py"
    wake.parent.mkdir(parents=True)
    wake.write_text(
        'import subprocess\n'
        'create_guarded_worktree(...)  # dispatch into a dedicated worktree\n'
        'subprocess.run(["git", "commit", "-m", "in worktree"])\n'
    )

    findings = lint.lint_in_repo_wake_path(tmp_path)

    assert findings == []


def test_real_in_repo_wake_path_is_clean():
    """The shipped wake_scheduler + rally watcher are notify-only → no findings."""
    repo_root = Path(__file__).resolve().parent.parent
    findings = lint.lint_in_repo_wake_path(repo_root)
    assert findings == [], f"shipped wake path is not notify-only: {findings}"


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------
def test_cli_exit_codes(tmp_path, orphan_poller_program):
    live_checkout = _make_git_checkout(tmp_path / "build-loop")
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    _write_plist(
        la_dir / "poller.plist",
        label="com.x.codex-autonomy-poller",
        program=orphan_poller_program,
        working_directory=str(live_checkout),
    )
    rc = lint.main(
        ["--workdir", str(tmp_path), "--launch-agents-dir", str(la_dir), "--json"]
    )
    assert rc == 1

    # Empty LaunchAgents dir → clean → exit 0.
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = lint.main(
        ["--workdir", str(tmp_path), "--launch-agents-dir", str(empty), "--json"]
    )
    assert rc == 0
