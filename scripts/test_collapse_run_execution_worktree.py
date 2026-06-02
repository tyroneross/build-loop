# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``collapse_run``'s integration with the run-entry isolation worktree.

The per-run worktree provisioned at Phase 1 Assess preamble lives on
``state.execution.run_worktree_path`` (not ``runs[N].createdRefs[]``, because
no runs entry exists yet at that point). Closeout must still pick it up so the
run worktree is bundled-then-removed like any other ref.
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

import collapse_run  # noqa: E402


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


def _make_run_worktree(repo: Path, build_loop_id: str) -> tuple[Path, str]:
    """Provision a run worktree the same way build_loop_id does, but inline."""
    short = build_loop_id.rsplit("-", 1)[-1]
    branch = f"bl/run-{short}"
    path = repo / ".build-loop" / "worktrees" / f"run-{short}"
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(path), "main")
    return path, branch


def _write_state_with_execution(
    repo: Path,
    build_loop_id: str,
    wt_path: Path,
    wt_branch: str,
    run_entry: dict | None = None,
) -> None:
    bl_dir = repo / ".build-loop"
    bl_dir.mkdir(exist_ok=True)
    state = {
        "execution": {
            "build_loop_id": build_loop_id,
            "run_worktree_path": str(wt_path.resolve()),
            "run_worktree_branch": wt_branch,
        },
        "runs": [run_entry] if run_entry else [],
    }
    (bl_dir / "state.json").write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Merged run-worktree → deleted by collapse
# ---------------------------------------------------------------------------

def test_collapse_deletes_merged_run_worktree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    bli_id = "bl-20260601T000000Z-test-000777"
    wt_path, wt_branch = _make_run_worktree(repo, bli_id)

    # Land work on the run worktree, then merge to main so it reads as MERGED.
    work_file = wt_path / "work.txt"
    work_file.write_text("isolated work\n")
    _git(wt_path, "add", "work.txt")
    _git(wt_path, "commit", "-m", "isolated work")
    _git(repo, "merge", "--no-ff", wt_branch, "-m", f"merge {wt_branch}")

    _write_state_with_execution(
        repo,
        bli_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": "run_001", "build_loop_id": bli_id, "createdRefs": []},
    )

    result = collapse_run.collapse(repo, run_id="latest")

    deleted_branches = [d["branch"] for d in result["deleted"]]
    assert wt_branch in deleted_branches, (
        f"execution-block run worktree branch {wt_branch} not deleted; result={result}"
    )
    # Worktree folder removed.
    assert not wt_path.exists()
    # Bundle was created.
    assert result["bundle_path"] is not None


# ---------------------------------------------------------------------------
# Unmerged run-worktree → surfaced (worktree folder removed, branch kept)
# ---------------------------------------------------------------------------

def test_collapse_surfaces_unmerged_run_worktree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    bli_id = "bl-20260601T010000Z-test-000888"
    wt_path, wt_branch = _make_run_worktree(repo, bli_id)

    # Land work but DON'T merge.
    (wt_path / "work.txt").write_text("orphan work\n")
    _git(wt_path, "add", "work.txt")
    _git(wt_path, "commit", "-m", "orphan work")

    _write_state_with_execution(
        repo,
        bli_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": "run_002", "build_loop_id": bli_id, "createdRefs": []},
    )

    result = collapse_run.collapse(repo, run_id="latest")

    surfaced = [s["branch"] for s in result["surfaced_unmerged"]]
    assert wt_branch in surfaced, f"expected {wt_branch} surfaced; result={result}"
    # Worktree folder removed; branch ref preserved.
    assert not wt_path.exists()
    assert _git(repo, "branch", "--list", wt_branch).stdout.strip(), (
        "unmerged branch must be preserved"
    )


# ---------------------------------------------------------------------------
# Mismatched build_loop_id → leave execution worktree alone (different run)
# ---------------------------------------------------------------------------

def test_collapse_skips_execution_worktree_when_bli_mismatch(tmp_path: Path) -> None:
    """If state.execution.build_loop_id != run.build_loop_id, the execution
    worktree belongs to a DIFFERENT (still-active) run — do not collapse it."""
    repo = _make_repo(tmp_path)
    bli_id_active = "bl-20260601T020000Z-test-000111"
    wt_path, wt_branch = _make_run_worktree(repo, bli_id_active)

    # state.execution points at the active build_loop_id; runs[0] is a
    # completed-but-different run.
    state = {
        "execution": {
            "build_loop_id": bli_id_active,
            "run_worktree_path": str(wt_path.resolve()),
            "run_worktree_branch": wt_branch,
        },
        "runs": [
            {
                "run_id": "run_old",
                "build_loop_id": "bl-20260530T120000Z-test-000999",
                "createdRefs": [],
            },
        ],
    }
    (repo / ".build-loop").mkdir(exist_ok=True)
    (repo / ".build-loop" / "state.json").write_text(json.dumps(state, indent=2))

    result = collapse_run.collapse(repo, run_id="latest")

    # Nothing touched — the only ref source for run_old was empty createdRefs[].
    assert result["deleted"] == []
    assert result["kept_for_review"] == []
    assert result["surfaced_unmerged"] == []
    # The active run's worktree is intact.
    assert wt_path.exists()
    assert _git(repo, "branch", "--list", wt_branch).stdout.strip(), (
        "active run's branch must remain"
    )
