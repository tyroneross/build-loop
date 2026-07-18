# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Coverage unions multi-branch/worktree advances and respects the checkpoint
(acceptance #2). Uses real throwaway git repos."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from post_push_retro import coverage  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")


def _commit(repo: Path, fname: str, content: str = "x") -> str:
    (repo / fname).parent.mkdir(parents=True, exist_ok=True)
    (repo / fname).write_text(content, encoding="utf-8")
    _git(repo, "add", fname)
    _git(repo, "commit", "-q", "-m", f"add {fname}")
    return _git(repo, "rev-parse", "HEAD")


def test_first_run_bounded_and_covers_head(tmp_path):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")
    _commit(repo, "b.py")
    ckpt = coverage.read_checkpoint(repo)
    cov = coverage.compute_coverage(repo, ckpt, cap=50)
    assert cov["commit_count"] >= 2
    assert "a.py" in cov["files_changed"] and "b.py" in cov["files_changed"]
    assert cov["repos_touched"] == 1


def test_checkpoint_prevents_double_cover(tmp_path):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")
    cov1 = coverage.compute_coverage(repo, coverage.read_checkpoint(repo))
    coverage.update_checkpoint_from_coverage(repo, cov1)
    # nothing new since checkpoint => empty coverage
    cov2 = coverage.compute_coverage(repo, coverage.read_checkpoint(repo))
    assert cov2["commit_count"] == 0
    # one new commit => exactly that commit is covered
    _commit(repo, "c.py")
    cov3 = coverage.compute_coverage(repo, coverage.read_checkpoint(repo))
    assert cov3["commit_count"] == 1
    assert "c.py" in cov3["files_changed"]
    assert "a.py" not in cov3["files_changed"]


def test_unions_across_branches(tmp_path):
    repo = tmp_path / "r"
    _init(repo)
    base = _commit(repo, "a.py")
    coverage.update_checkpoint_from_coverage(
        repo, coverage.compute_coverage(repo, coverage.read_checkpoint(repo)))
    # advance main
    _commit(repo, "b.py")
    # advance a second branch off base
    _git(repo, "checkout", "-q", "-b", "feature", base)
    _commit(repo, "feat.py")
    _git(repo, "checkout", "-q", "main")
    cov = coverage.compute_coverage(repo, coverage.read_checkpoint(repo))
    files = set(cov["files_changed"])
    assert "b.py" in files and "feat.py" in files  # union across both branches
    assert "feature" in cov["branches_advanced"]


def test_worktrees_of_same_repo_count_as_one_repo(tmp_path):
    # plan-critic fix: N worktrees of ONE repo must NOT read as N repos
    # (that would spuriously escalate routine work to substantial).
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")
    _git(repo, "branch", "wt-branch")
    wt = tmp_path / "r-wt"
    _git(repo, "worktree", "add", "-q", str(wt), "wt-branch")
    (wt / "w.py").write_text("y", encoding="utf-8")
    _git(wt, "add", "w.py")
    _git(wt, "commit", "-q", "-m", "wt commit")
    cov = coverage.compute_coverage(repo, coverage.read_checkpoint(repo))
    assert cov["repos_touched"] == 1  # NOT 2


def test_common_dir_shared_across_worktrees(tmp_path):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")
    _git(repo, "branch", "wt-branch")
    wt = tmp_path / "r-wt"
    _git(repo, "worktree", "add", "-q", str(wt), "wt-branch")
    # both checkouts resolve to the SAME per-repo anchor => shared checkpoint.
    assert coverage.git_common_dir(repo) == coverage.git_common_dir(wt)


def test_checkpoint_atomic_write_roundtrip(tmp_path):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")
    cov = coverage.compute_coverage(repo, coverage.read_checkpoint(repo))
    coverage.update_checkpoint_from_coverage(repo, cov)
    ckpt = coverage.read_checkpoint(repo)
    assert "main" in ckpt["branches"]
    assert ckpt["last_retro_at"] is not None


def test_update_checkpoint_never_regresses_other_branches(tmp_path):
    repo = tmp_path / "r"
    _init(repo)
    _commit(repo, "a.py")
    _git(repo, "branch", "other")
    other_tip = _git(repo, "rev-parse", "other")
    # cover + checkpoint main only (simulate coverage of only main)
    cov = coverage.compute_coverage(repo, coverage.read_checkpoint(repo))
    coverage.update_checkpoint_from_coverage(repo, cov)
    ckpt = coverage.read_checkpoint(repo)
    # both refs recorded; a later main-only update must not drop 'other'
    assert ckpt["branches"].get("other") == other_tip
