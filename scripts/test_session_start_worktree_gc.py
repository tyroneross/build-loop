#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for hooks/session-start-worktree-gc.sh — the cross-run worktree reaper.

Intent.md A5: ACT mode removes merged+clean stale worktrees/branches (bundle-first,
merged-only) so cross-run residue self-heals; never touches dirty/locked/unmerged.
Regression guarded: ACT must be ON by default (it shipped dormant behind an
opt-in flag, so collapse silently never fired — build-loop-memory North Star).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parent.parent / "hooks" / "session-start-worktree-gc.sh"


def _git(repo: Path, *args: str, **kw) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": str(repo),  # isolate from user gitconfig
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, env={**_osenv(), **env}, **kw,
    ).stdout


def _osenv() -> dict:
    import os
    return dict(os.environ)


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", name, "-q")


def _run_hook(repo: Path, act: str | None) -> str:
    import os
    env = {**_osenv(), "CLAUDE_PROJECT_DIR": str(repo)}
    if act is not None:
        env["BUILDLOOP_GC_ACT"] = act
    subprocess.run(["bash", str(_HOOK)], env=env, capture_output=True, text=True)
    return (repo / ".build-loop" / "worktree-gc-last.txt").read_text()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "main-repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _commit(r, "base")

    # merged + clean worktree -> ACT should remove it
    _git(r, "branch", "merged-clean")
    _git(r, "worktree", "add", "-q", str(tmp_path / "wt-merged"), "merged-clean")
    # (branch tip == main tip -> ancestor of main -> "merged")

    # unmerged worktree -> never removed
    _git(r, "worktree", "add", "-q", "-b", "unmerged", str(tmp_path / "wt-unmerged"))
    _commit(tmp_path / "wt-unmerged", "ahead")  # diverges from main

    # merged but dirty worktree -> never removed
    _git(r, "branch", "merged-dirty")
    _git(r, "worktree", "add", "-q", str(tmp_path / "wt-dirty"), "merged-dirty")
    (tmp_path / "wt-dirty" / "scratch").write_text("uncommitted")
    return r


def _worktree_paths(repo: Path) -> str:
    return _git(repo, "worktree", "list")


def test_act_default_on_removes_merged_clean(repo: Path):
    # No BUILDLOOP_GC_ACT set -> ACT must be ON by default (the regression).
    report = _run_hook(repo, act=None)
    wts = _worktree_paths(repo)
    assert "wt-merged" not in wts, "merged+clean worktree should be removed by default"
    assert "merged-clean" not in _git(repo, "branch"), "merged branch should be deleted"
    assert "Auto-removed" in report
    bundles = list((repo / ".build-loop" / "bundles").glob("gc-*.bundle"))
    assert bundles, "a reversibility bundle must be created before removal"


def test_unmerged_and_dirty_are_never_touched(repo: Path):
    _run_hook(repo, act="1")
    wts = _worktree_paths(repo)
    branches = _git(repo, "branch")
    assert "wt-unmerged" in wts and "unmerged" in branches, "unmerged worktree kept"
    assert "wt-dirty" in wts and "merged-dirty" in branches, "dirty worktree kept"


def test_opt_out_is_report_only(repo: Path):
    report = _run_hook(repo, act="0")
    assert "wt-merged" in _worktree_paths(repo), "ACT=0 must not remove anything"
    assert "Auto-removed" not in report
    assert "REPORT-ONLY" in report


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
