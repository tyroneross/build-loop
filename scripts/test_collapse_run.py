#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/collapse_run.py.

Each test builds a real temporary git repo via subprocess so the
`git merge-base --is-ancestor` and `git worktree` calls exercise actual git.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Make scripts/ importable
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import collapse_run  # noqa: E402


# ---------------------------------------------------------------------------
# Repo factory helpers
# ---------------------------------------------------------------------------

def _git(workdir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _make_repo(tmp_path: Path) -> Path:
    """Create a bare-minimum git repo with one commit on main."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    # Initial commit
    (repo / "README.md").write_text("hello")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    return repo


def _make_branch(repo: Path, branch: str, merge_to_main: bool = False) -> str:
    """Create a branch with one extra commit. Return its SHA. Optionally merge it."""
    _git(repo, "checkout", "-b", branch)
    (repo / f"{branch}.txt").write_text(branch)
    _git(repo, "add", f"{branch}.txt")
    _git(repo, "commit", "-m", f"work on {branch}")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "main")
    if merge_to_main:
        _git(repo, "merge", "--no-ff", branch, "-m", f"merge {branch}")
    return sha


def _make_worktree(repo: Path, branch: str) -> Path:
    """Add a worktree for branch. Returns the worktree path."""
    wt_path = repo.parent / f"wt-{branch}"
    _git(repo, "worktree", "add", str(wt_path), branch)
    return wt_path


def _write_state(repo: Path, run: dict) -> None:
    bl_dir = repo / ".build-loop"
    bl_dir.mkdir(exist_ok=True)
    state_path = bl_dir / "state.json"
    state_path.write_text(json.dumps({"runs": [run]}, indent=2))


def _read_state(repo: Path) -> dict:
    return json.loads((repo / ".build-loop" / "state.json").read_text())


def _ledger_ref(repo: Path, branch: str) -> dict:
    state = _read_state(repo)
    refs = state["runs"][0].get("createdRefs", [])
    for ref in refs:
        if ref.get("branch") == branch:
            return ref
    raise AssertionError(f"missing ledger ref for {branch}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMergedBranch:
    """Merged branch (+ its worktree) should be deleted."""

    def test_merged_branch_deleted(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _make_branch(repo, "feat-merged", merge_to_main=True)
        wt = _make_worktree(repo, "feat-merged") if False else None  # no worktree here

        _write_state(repo, {
            "run_id": "run_001",
            "createdRefs": [{"branch": "feat-merged", "path": None, "review_hold": False}],
        })

        result = collapse_run.collapse(repo, run_id="latest")

        assert len(result["deleted"]) == 1
        assert result["deleted"][0]["branch"] == "feat-merged"
        assert result["kept_for_review"] == []
        assert result["surfaced_unmerged"] == []

        # Branch should no longer exist
        branches = _git(repo, "branch", "--list", "feat-merged").stdout.strip()
        assert branches == ""
        ref = _ledger_ref(repo, "feat-merged")
        assert ref["status"] == "closed"
        assert ref["closed_ts"]
        assert "merged into main" in ref["close_reason"]

    def test_merged_branch_with_worktree_deleted(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _make_branch(repo, "feat-wt", merge_to_main=True)
        # We do NOT create an actual worktree here (branch is on main); just
        # record a fake path — the script should handle a missing worktree gracefully.
        fake_wt = str(tmp_path / "wt-feat-wt-nonexistent")

        _write_state(repo, {
            "run_id": "run_wt_merged",
            "createdRefs": [{"branch": "feat-wt", "path": fake_wt, "review_hold": False}],
        })

        result = collapse_run.collapse(repo, run_id="latest")

        assert any(d["branch"] == "feat-wt" for d in result["deleted"])
        assert result["kept_for_review"] == []
        assert _ledger_ref(repo, "feat-wt")["status"] == "closed"

    def test_merged_with_real_worktree(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _make_branch(repo, "feat-realwt", merge_to_main=True)
        # We can't add a worktree for a branch that's checked-out in another
        # worktree if it's already been merged; use a fresh branch instead.
        # Create a branch that's merged but still has a live worktree.
        _make_branch(repo, "feat-live-wt", merge_to_main=True)
        wt = _make_worktree(repo, "feat-live-wt")
        assert wt.exists()

        _write_state(repo, {
            "run_id": "run_live_wt",
            "createdRefs": [{"branch": "feat-live-wt", "path": str(wt), "review_hold": False}],
        })

        result = collapse_run.collapse(repo, run_id="latest")

        assert any(d["branch"] == "feat-live-wt" for d in result["deleted"])
        assert not wt.exists(), "worktree folder should have been removed"
        ref = _ledger_ref(repo, "feat-live-wt")
        assert ref["status"] == "closed"
        assert ref["closed_ts"]


class TestUnmergedReviewHold:
    """Unmerged + review_hold → branch kept, worktree folder removed."""

    def test_keeps_branch_removes_worktree(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _make_branch(repo, "risky-branch")  # not merged
        wt = _make_worktree(repo, "risky-branch")

        _write_state(repo, {
            "run_id": "run_risky",
            "riskyBranches": [{"branch": "risky-branch", "path": str(wt), "summary": "risky work"}],
        })

        result = collapse_run.collapse(repo, run_id="latest")

        assert result["deleted"] == []
        assert len(result["kept_for_review"]) == 1
        assert result["kept_for_review"][0]["branch"] == "risky-branch"
        assert result["surfaced_unmerged"] == []

        # Branch still exists
        branches = _git(repo, "branch", "--list", "risky-branch").stdout.strip()
        assert branches != ""

        # Worktree folder removed
        assert not wt.exists(), "worktree folder should be removed for review_hold branch"
        ref = _ledger_ref(repo, "risky-branch")
        assert ref["status"] == "kept_for_review"
        assert ref["closed_ts"] is None
        assert "review_hold" in ref["close_reason"]


class TestUnmergedNoReviewHold:
    """Unmerged + review_hold false → branch surfaced, worktree folder removed."""

    def test_surfaces_branch_removes_worktree(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _make_branch(repo, "dispatch-wt")  # not merged
        wt = _make_worktree(repo, "dispatch-wt")

        _write_state(repo, {
            "run_id": "run_dispatch",
            "dispatchedWorktrees": [{"branch": "dispatch-wt", "path": str(wt), "dispatch_ts": "2026-01-01T00:00:00Z"}],
        })

        result = collapse_run.collapse(repo, run_id="latest")

        assert result["deleted"] == []
        assert result["kept_for_review"] == []
        assert len(result["surfaced_unmerged"]) == 1
        assert result["surfaced_unmerged"][0]["branch"] == "dispatch-wt"

        # Branch still exists
        branches = _git(repo, "branch", "--list", "dispatch-wt").stdout.strip()
        assert branches != ""

        # Worktree folder removed
        assert not wt.exists(), "worktree folder should be removed even for surfaced branch"
        ref = _ledger_ref(repo, "dispatch-wt")
        assert ref["status"] == "surfaced_unmerged"
        assert ref["closed_ts"] is None
        assert "operator disposition" in ref["close_reason"]


class TestBundle:
    """Bundle file is created under .build-loop/bundles/ when refs exist."""

    def test_bundle_created(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _make_branch(repo, "feat-bundle", merge_to_main=True)

        _write_state(repo, {
            "run_id": "run_bundle",
            "createdRefs": [{"branch": "feat-bundle", "path": None, "review_hold": False}],
        })

        result = collapse_run.collapse(repo, run_id="latest")

        assert result["bundle_path"] is not None
        assert Path(result["bundle_path"]).exists()
        assert ".build-loop/bundles/" in result["bundle_path"]

    def test_no_bundle_when_no_refs(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)

        _write_state(repo, {"run_id": "run_empty"})

        result = collapse_run.collapse(repo, run_id="latest")

        assert result["bundle_path"] is None


class TestIdempotent:
    """Second run with already-deleted refs is a clean no-op."""

    def test_idempotent_second_run(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _make_branch(repo, "feat-idem", merge_to_main=True)

        _write_state(repo, {
            "run_id": "run_idem",
            "createdRefs": [{"branch": "feat-idem", "path": None, "review_hold": False}],
        })

        result1 = collapse_run.collapse(repo, run_id="latest")
        assert len(result1["deleted"]) == 1
        assert result1["errors"] == []

        # Second run: closed ledger rows are skipped, so closeout is idempotent.
        result2 = collapse_run.collapse(repo, run_id="latest")
        assert result2["deleted"] == []
        assert result2["errors"] == []


class TestFailSoft:
    """A ref pointing at a non-existent branch is caught into errors[], exit 0."""

    def test_nonexistent_branch_is_error_not_crash(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)

        _write_state(repo, {
            "run_id": "run_ghost",
            "createdRefs": [{"branch": "ghost-branch-does-not-exist", "path": None, "review_hold": False}],
        })

        # Must not raise; exit code handled by main(), here we call collapse() directly
        result = collapse_run.collapse(repo, run_id="latest")

        assert len(result["errors"]) == 1
        assert "ghost-branch-does-not-exist" in result["errors"][0]
        assert result["deleted"] == []
        assert _ledger_ref(repo, "ghost-branch-does-not-exist")["status"] == "error"


class TestDryRun:
    """--dry-run performs no deletions."""

    def test_dry_run_no_deletions(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _make_branch(repo, "feat-dry", merge_to_main=True)
        _make_branch(repo, "risky-dry")  # not merged

        _write_state(repo, {
            "run_id": "run_dry",
            "createdRefs": [
                {"branch": "feat-dry", "path": None, "review_hold": False},
                {"branch": "risky-dry", "path": None, "review_hold": True},
            ],
        })

        result = collapse_run.collapse(repo, run_id="latest", dry_run=True)

        assert result["dry_run"] is True
        # feat-dry would be deleted
        assert any(d["branch"] == "feat-dry" for d in result["deleted"])
        # risky-dry would be kept
        assert any(d["branch"] == "risky-dry" for d in result["kept_for_review"])

        # Nothing actually deleted
        feat_branches = _git(repo, "branch", "--list", "feat-dry").stdout.strip()
        risky_branches = _git(repo, "branch", "--list", "risky-dry").stdout.strip()
        assert feat_branches != "", "dry-run must not delete feat-dry"
        assert risky_branches != "", "dry-run must not delete risky-dry"

        # No bundle created in dry-run
        assert result["bundle_path"] is None
        assert _read_state(repo)["runs"][0]["createdRefs"][0].get("status") is None


class TestMainNeverDeleted:
    """main is never deleted even if somehow listed."""

    def test_main_skipped(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)

        _write_state(repo, {
            "run_id": "run_main_guard",
            "createdRefs": [{"branch": "main", "path": None, "review_hold": False}],
        })

        result = collapse_run.collapse(repo, run_id="latest")

        # main listed in errors as skipped, not deleted
        assert result["deleted"] == []
        assert any("main" in e for e in result["errors"])

        # main still exists
        branches = _git(repo, "branch", "--list", "main").stdout.strip()
        assert branches != ""


class TestCLI:
    """CLI integration: --json flag produces valid JSON on stdout."""

    def test_cli_json_output(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _make_branch(repo, "feat-cli", merge_to_main=True)

        _write_state(repo, {
            "run_id": "run_cli",
            "createdRefs": [{"branch": "feat-cli", "path": None, "review_hold": False}],
        })

        r = subprocess.run(
            [sys.executable, str(_SCRIPTS / "collapse_run.py"),
             "--workdir", str(repo), "--run-id", "latest", "--json"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["run_id"] == "run_cli"
        assert isinstance(data["deleted"], list)

    def test_cli_missing_state_exits_1(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        # No state.json

        r = subprocess.run(
            [sys.executable, str(_SCRIPTS / "collapse_run.py"),
             "--workdir", str(repo)],
            capture_output=True, text=True,
        )
        assert r.returncode == 1
        assert "state.json" in r.stderr
