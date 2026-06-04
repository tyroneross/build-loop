# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the run-entry worktree-isolation extension to ``build_loop_id``.

Spec: ``docs/SPEC-run-worktree-isolation.md`` Phase 1a.

Acceptance criteria:
  AC-W1: Fresh ``generate_or_resume(provision_worktree=True)`` creates a real
         git worktree at ``.build-loop/worktrees/run-<short>/`` on branch
         ``bl/run-<short>`` and persists the absolute path + branch to
         ``state.execution``.
  AC-W2: Resume preserves the existing ``run_worktree_path`` verbatim and
         does NOT create a second worktree.
  AC-W3: When provisioning fails (e.g. base branch missing) the call raises
         ``RunWorktreeProvisionError``; state.execution still records the
         build_loop_id so the orchestrator can diagnose.
  AC-W4: Backward-compatible default — ``provision_worktree=False`` (the
         default) leaves the legacy behaviour unchanged: no worktree, no new
         keys on state.execution.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from rally_point import build_loop_id as bli  # noqa: E402


# ---------------------------------------------------------------------------
# Repo factory — a real git repo so worktree_guard.create_guarded_worktree
# can actually run `git worktree add`.
# ---------------------------------------------------------------------------

def _git(workdir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
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
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    return repo


# ---------------------------------------------------------------------------
# AC-W1
# ---------------------------------------------------------------------------

def test_provision_creates_worktree_under_canonical_root(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    exec_block = bli.generate_or_resume(
        repo, tool="claude_code", session_id="s1", provision_worktree=True
    )

    bli_id = exec_block["build_loop_id"]
    short = bli_id.rsplit("-", 1)[-1]
    expected_path = (repo / ".build-loop" / "worktrees" / f"run-{short}").resolve()
    expected_branch = f"bl/run-{short}"

    assert exec_block["run_worktree_path"] == str(expected_path)
    assert exec_block["run_worktree_branch"] == expected_branch
    assert expected_path.is_dir(), "worktree directory not created"

    # State.json persisted.
    state = json.loads((repo / ".build-loop" / "state.json").read_text())
    assert state["execution"]["run_worktree_path"] == str(expected_path)
    assert state["execution"]["run_worktree_branch"] == expected_branch

    # git knows about the worktree.
    wt_list = _git(repo, "worktree", "list").stdout
    assert str(expected_path) in wt_list

    # Branch exists.
    branches = _git(repo, "branch", "--list", expected_branch).stdout.strip()
    assert expected_branch in branches


# ---------------------------------------------------------------------------
# AC-W2 — resume must NOT re-create
# ---------------------------------------------------------------------------

def test_resume_preserves_worktree_path(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    first = bli.generate_or_resume(
        repo, tool="claude_code", session_id="s1", provision_worktree=True
    )
    original_path = first["run_worktree_path"]
    original_branch = first["run_worktree_branch"]

    # Simulate a resume — same workdir, new session, provision_worktree still True.
    second = bli.generate_or_resume(
        repo, tool="codex", session_id="s2", provision_worktree=True
    )

    assert second["run_worktree_path"] == original_path
    assert second["run_worktree_branch"] == original_branch
    assert Path(original_path).is_dir(), "resume must not delete the worktree"
    # No second worktree got added.
    wt_list = _git(repo, "worktree", "list").stdout
    assert wt_list.count(".build-loop/worktrees/run-") == 1


# ---------------------------------------------------------------------------
# AC-W3 — fail-closed when worktree creation fails
# ---------------------------------------------------------------------------

def test_provision_fail_closed_raises(tmp_path: Path) -> None:
    """When base branch does not exist, `git worktree add` fails and we raise.

    State is still persisted with the build_loop_id (so the orchestrator's
    error report can identify which run failed), but never falls back to
    canonical-checkout work.
    """
    repo = _make_repo(tmp_path)

    with pytest.raises(bli.RunWorktreeProvisionError):
        bli.generate_or_resume(
            repo,
            tool="claude_code",
            session_id="s1",
            provision_worktree=True,
            base="does-not-exist",
        )

    state = json.loads((repo / ".build-loop" / "state.json").read_text())
    assert state["execution"]["build_loop_id"].startswith("bl-")
    # No worktree keys when provisioning failed.
    assert "run_worktree_path" not in state["execution"]
    assert "run_worktree_branch" not in state["execution"]


# ---------------------------------------------------------------------------
# AC-W4 — default (False) leaves legacy behaviour unchanged
# ---------------------------------------------------------------------------

def test_default_provision_false_is_legacy_behaviour(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    exec_block = bli.generate_or_resume(
        repo, tool="claude_code", session_id="s1"
    )

    assert "run_worktree_path" not in exec_block
    assert "run_worktree_branch" not in exec_block
    # And no worktree dir was created.
    assert not (repo / ".build-loop" / "worktrees").exists()
