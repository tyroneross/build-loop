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
    wt_root = r / ".build-loop" / "worktrees"
    wt_root.mkdir(parents=True)

    # merged + clean worktree -> ACT should remove it
    _git(r, "branch", "merged-clean")
    _git(r, "worktree", "add", "-q", str(wt_root / "wt-merged"), "merged-clean")
    # (branch tip == main tip -> ancestor of main -> "merged")

    # unmerged worktree -> never removed
    _git(r, "worktree", "add", "-q", "-b", "unmerged", str(wt_root / "wt-unmerged"))
    _commit(wt_root / "wt-unmerged", "ahead")  # diverges from main

    # merged but dirty worktree -> never removed
    _git(r, "branch", "merged-dirty")
    _git(r, "worktree", "add", "-q", str(wt_root / "wt-dirty"), "merged-dirty")
    (wt_root / "wt-dirty" / "scratch").write_text("uncommitted")

    # rally-owned worktree under .rally/worktrees/ -> must NEVER be removed,
    # even though it matches the merged+clean+unlocked profile that would
    # otherwise trigger ACT. Regression: prior behavior reaped these out from
    # under live rally agents, causing posix_spawn ENOENT in every hook.
    rally_root = r / ".rally" / "worktrees"
    rally_root.mkdir(parents=True)
    _git(r, "branch", "rally/test-agent-01")
    _git(r, "worktree", "add", "-q", str(rally_root / "test-agent-01"), "rally/test-agent-01")
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


def test_rally_worktrees_are_never_removed(repo: Path):
    """Regression: build-loop's GC must NEVER reap worktrees under
    .rally/worktrees/, even though `rally run` creates merged+clean+unlocked
    worktrees that match the ACT removal profile. Reaping them deletes the
    cwd of a live agent process; every subsequent hook then fails with
    posix_spawn ENOENT. Rally owns its own worktree lifecycle.

    This run uses default ACT (BUILDLOOP_GC_ACT unset → ON), which is the
    surface that fired the original bug. We assert two invariants in one
    run to prove the exclusion is TARGETED, not a kill-switch:
      1. The rally worktree + branch survive.
      2. A normal merged+clean build-loop worktree is still removed.
    """
    report = _run_hook(repo, act=None)
    wts = _worktree_paths(repo)
    branches = _git(repo, "branch")

    # Invariant 1: rally worktree preserved.
    assert "test-agent-01" in wts, "rally worktree must be preserved by GC"
    assert "rally/test-agent-01" in branches, "rally branch must be preserved by GC"
    assert "SKIP:rally-owned" in report, "hook must mark the rally candidate as skipped"

    # Invariant 2: normal merged+clean worktree is still removed (no kill-switch).
    assert "wt-merged" not in wts, "non-rally merged+clean worktree must still be removed"
    assert "merged-clean" not in branches, "non-rally merged branch must still be deleted"

    # Sanity: report's Auto-removed section exists for non-rally removals,
    # and contains the non-rally worktree path but never a rally path.
    assert "Auto-removed" in report, "report should still document non-rally removals"
    auto_removed_section = report.split("## Auto-removed", 1)[1]
    assert "wt-merged" in auto_removed_section, (
        "non-rally merged worktree must appear in Auto-removed"
    )
    assert ".rally/worktrees/" not in auto_removed_section, (
        f"GC must never log a .rally/worktrees/* path under Auto-removed; got: {auto_removed_section!r}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
