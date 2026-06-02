# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``scripts/worktree_reaper``.

Acceptance criteria (from SPEC-run-worktree-isolation.md):
  AC-R1: A leaked run worktree older than --min-age-hours is bundled then
         removed; the bundle survives.
  AC-R2: An active run's worktree (state.execution.run_worktree_branch) is
         NEVER reaped.
  AC-R3: A young worktree (< min-age-hours) is skipped.
  AC-R4: --dry-run performs no destructive actions.
  AC-R5: Idempotent — running twice in a row is a no-op the second time.
  AC-R6: An orphan folder (no backing branch) is removed without bundling.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_PKG = _HERE.parent
_SCRIPTS = _PKG.parent
for _d in (_SCRIPTS, _PKG):
    sd = str(_d)
    if sd not in sys.path:
        sys.path.insert(0, sd)

from worktree_reaper.reaper import reap_worktrees  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
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


def _make_run_worktree(repo: Path, short: str, with_commit: bool = True) -> tuple[Path, str]:
    branch = f"bl/run-{short}"
    path = repo / ".build-loop" / "worktrees" / f"run-{short}"
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(path), "main")
    if with_commit:
        (path / "work.txt").write_text(f"work-{short}\n")
        _git(path, "add", "work.txt")
        _git(path, "commit", "-m", f"work {short}")
    return path, branch


def _age_folder(path: Path, hours: float) -> None:
    """Backdate a folder's mtime by ``hours`` hours."""
    t = time.time() - (hours * 3600.0)
    os.utime(path, (t, t))


def _write_state_with_active(repo: Path, active_branch: str | None) -> None:
    bl = repo / ".build-loop"
    bl.mkdir(exist_ok=True)
    state = {"execution": {"build_loop_id": "bl-active", "run_worktree_branch": active_branch}}
    (bl / "state.json").write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# AC-R1 — leaked run worktree gets bundled and removed
# ---------------------------------------------------------------------------

def test_leaked_run_worktree_is_bundled_and_removed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt_path, branch = _make_run_worktree(repo, "111111")
    _age_folder(wt_path, hours=24)
    # No state.json execution block -> nothing active.

    result = reap_worktrees(repo)

    assert any(e["branch"] == branch for e in result.bundled_and_removed), (
        f"expected {branch} reaped; result={result.to_dict()}"
    )
    assert not wt_path.exists(), "worktree folder must be removed"
    # Bundle present and non-empty.
    bundles = list((repo / ".build-loop" / "bundles").glob("reaped-*.bundle"))
    assert bundles, "bundle file must exist"
    assert all(b.stat().st_size > 0 for b in bundles)
    # Branch ref deleted.
    assert not _git(repo, "branch", "--list", branch, check=False).stdout.strip()


# ---------------------------------------------------------------------------
# AC-R2 — active run's worktree is NEVER reaped
# ---------------------------------------------------------------------------

def test_active_run_worktree_is_skipped(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt_path, branch = _make_run_worktree(repo, "222222")
    _age_folder(wt_path, hours=24)  # old enough to be reapable...
    _write_state_with_active(repo, active_branch=branch)  # ...but it's active.

    result = reap_worktrees(repo)

    assert result.bundled_and_removed == []
    assert any(e["branch"] == branch for e in result.skipped_active), (
        f"expected {branch} in skipped_active; result={result.to_dict()}"
    )
    assert wt_path.exists(), "active worktree must remain"


# ---------------------------------------------------------------------------
# AC-R3 — young worktree is skipped
# ---------------------------------------------------------------------------

def test_young_worktree_is_skipped(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt_path, branch = _make_run_worktree(repo, "333333")
    # No aging — folder is fresh (mtime ≈ now).

    result = reap_worktrees(repo, min_age_hours=2.0)

    assert result.bundled_and_removed == []
    assert any(e["path"] == str(wt_path) for e in result.skipped_too_young), (
        f"expected {wt_path} in skipped_too_young; result={result.to_dict()}"
    )
    assert wt_path.exists()


# ---------------------------------------------------------------------------
# AC-R4 — dry-run is non-destructive
# ---------------------------------------------------------------------------

def test_dry_run_makes_no_changes(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt_path, branch = _make_run_worktree(repo, "444444")
    _age_folder(wt_path, hours=24)

    result = reap_worktrees(repo, dry_run=True)

    assert result.dry_run is True
    assert any(e["branch"] == branch for e in result.bundled_and_removed)
    # Nothing actually changed.
    assert wt_path.exists()
    assert _git(repo, "branch", "--list", branch).stdout.strip()
    assert not (repo / ".build-loop" / "bundles").exists() or not list(
        (repo / ".build-loop" / "bundles").glob("reaped-*.bundle")
    )


# ---------------------------------------------------------------------------
# AC-R5 — idempotent
# ---------------------------------------------------------------------------

def test_idempotent_second_run_is_noop(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt_path, branch = _make_run_worktree(repo, "555555")
    _age_folder(wt_path, hours=24)

    first = reap_worktrees(repo)
    assert first.bundled_and_removed, "first pass must reap"

    second = reap_worktrees(repo)
    assert second.bundled_and_removed == []
    assert second.removed_orphan == []
    assert second.errors == []


# ---------------------------------------------------------------------------
# AC-R6 — orphan folder (no backing branch) is removed without bundling
# ---------------------------------------------------------------------------

def test_orphan_folder_without_branch_is_removed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    # Create a folder that LOOKS like a leak but has no git linkage.
    orphan = repo / ".build-loop" / "worktrees" / "run-666666"
    orphan.mkdir(parents=True)
    (orphan / "stray.txt").write_text("not a real worktree\n")
    _age_folder(orphan, hours=24)
    # Defensively assert: the corresponding branch does not exist.
    assert not _git(repo, "branch", "--list", "bl/run-666666", check=False).stdout.strip()

    result = reap_worktrees(repo)

    # Either bundled_and_removed (if `git worktree remove` happened) or
    # removed_orphan — both are acceptable terminal states; the folder MUST be gone.
    classified = (
        any(e["path"] == str(orphan) for e in result.removed_orphan)
        or any(e["path"] == str(orphan) for e in result.bundled_and_removed)
    )
    assert classified, f"orphan folder not classified; result={result.to_dict()}"
    assert not orphan.exists(), "orphan folder must be gone"
    # And critically: no bundle was made for a no-ref orphan (no work to save).
    bundles = list((repo / ".build-loop" / "bundles").glob("reaped-*.bundle"))
    assert not bundles, "no bundle should be created for an orphan with no branch"


# ---------------------------------------------------------------------------
# Bonus: non-run-prefixed siblings under worktrees/ are left alone
# ---------------------------------------------------------------------------

def test_non_run_prefixed_folders_are_skipped(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    # E.g. a dispatch worktree from collapse_run that uses a different naming
    # convention; should not be touched by the run-worktree reaper.
    other = repo / ".build-loop" / "worktrees" / "dispatch-chunk-7"
    other.mkdir(parents=True)
    _age_folder(other, hours=48)

    result = reap_worktrees(repo)

    assert any(e["path"] == str(other) for e in result.skipped_not_run)
    assert other.exists()
