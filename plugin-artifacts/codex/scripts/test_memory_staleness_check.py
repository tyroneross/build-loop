#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
Tests for memory_staleness_check.py.

Uses a real tmp git repo + tmp memory-root so no mocking of subprocess.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Allow import from scripts/ without install.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import memory_staleness_check as msc
import memory_update_ledger as mul


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(path: Path) -> None:
    """Create a minimal git repo with one initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "Test"], path)
    (path / "README.md").write_text("init\n")
    _git(["add", "README.md"], path)
    _git(["commit", "-m", "init"], path)


_commit_counter: dict[str, int] = {}


def _add_commits(repo: Path, n: int) -> None:
    """Add n dummy commits to the repo with globally unique filenames."""
    key = str(repo)
    start = _commit_counter.get(key, 0)
    for i in range(start, start + n):
        f = repo / f"dummy_{i}.txt"
        f.write_text(f"commit {i}\n")
        _git(["add", str(f.name)], repo)
        _git(["commit", "-m", f"dummy {i}"], repo)
    _commit_counter[key] = start + n


def _head_sha(repo: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _write_milestone(memory_root: Path, slug: str, commit: str) -> None:
    """Write a single-line milestones.jsonl for the given slug."""
    proj_dir = memory_root / "projects" / slug
    proj_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": "2026-01-01T00:00:00Z", "commit": commit, "repo": "test", "summary": "test", "run_id": "run_test"})
    (proj_dir / "milestones.jsonl").write_text(line + "\n")


# ---------------------------------------------------------------------------
# Test: milestone commit == HEAD → not stale, commits_stale 0
# ---------------------------------------------------------------------------

def test_current_memory_not_stale(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    mem = tmp_path / "memory"
    _init_repo(repo)

    sha = _head_sha(repo)
    _write_milestone(mem, "repo", sha)

    result = msc.check(
        workdir=repo,
        slug="repo",
        memory_root=mem,
        commits_threshold=5,
    )

    assert result["stale"] is False
    assert result["commits_stale"] == 0
    assert result["memory_as_of_commit"] == sha


# ---------------------------------------------------------------------------
# Test: milestone commit + 6 later commits → stale (threshold 5)
# ---------------------------------------------------------------------------

def test_stale_after_six_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    mem = tmp_path / "memory"
    _init_repo(repo)

    sha = _head_sha(repo)
    _write_milestone(mem, "repo", sha)

    _add_commits(repo, 6)

    result = msc.check(
        workdir=repo,
        slug="repo",
        memory_root=mem,
        commits_threshold=5,
    )

    assert result["stale"] is True
    assert result["commits_stale"] == 6
    assert result["memory_as_of_commit"] == sha
    assert result["baseline_source"] == "milestones"
    assert "6 commits behind HEAD" in result["message"]


def test_stale_output_exposes_missing_run_commit_and_marker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    mem = tmp_path / "memory"
    _init_repo(repo)
    baseline = _head_sha(repo)
    _write_milestone(mem, "repo", baseline)
    _add_commits(repo, 6)
    candidate = _head_sha(repo)
    state = repo / ".build-loop" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"runs": [{
        "run_id": "bl-run-missing-memory",
        "date": "2026-08-24T07:45:49Z",
        "outcome": "pass",
        "filesTouched": ["lib/brief-data.ts"],
        "branch_closeout": {"status": "complete"},
        "createdRefs": [{"status": "closed", "expected_oid": candidate}],
    }]}))

    result = msc.check(
        workdir=repo,
        slug="repo",
        memory_root=mem,
        commits_threshold=5,
    )

    assert result["latest_run_evidence"] == {
        "run_id": "bl-run-missing-memory",
        "date": "2026-08-24T07:45:49Z",
        "outcome": "pass",
        "commit": candidate,
        "commit_source": "runs[].createdRefs[].expected_oid",
        "run_commit_present": False,
        "branch_closeout_status": "complete",
        "milestone_owed_marker": None,
    }


# ---------------------------------------------------------------------------
# Test: update ledger at HEAD beats older milestone baseline
# ---------------------------------------------------------------------------

def test_update_ledger_refreshes_staleness_baseline(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    mem = tmp_path / "memory"
    _init_repo(repo)

    old_sha = _head_sha(repo)
    _write_milestone(mem, "repo", old_sha)
    _add_commits(repo, 6)
    fresh_sha = _head_sha(repo)

    decision_path = mem / "projects" / "repo" / "decisions" / "0001-test.md"
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text("body\n")
    mul.append_update(
        memory_root=mem,
        project="repo",
        lane="decisions",
        action="write",
        path=decision_path,
        writer="test",
        source_workdir=repo,
        source_commit=fresh_sha,
    )

    result = msc.check(
        workdir=repo,
        slug="repo",
        memory_root=mem,
        commits_threshold=5,
    )

    assert result["stale"] is False
    assert result["commits_stale"] == 0
    assert result["memory_as_of_commit"] == fresh_sha
    assert result["baseline_source"] == "updates_ledger"


def test_fresh_milestone_beats_older_update_ledger(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    mem = tmp_path / "memory"
    _init_repo(repo)

    old_sha = _head_sha(repo)
    decision_path = mem / "projects" / "repo" / "decisions" / "0001-test.md"
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text("body\n")
    mul.append_update(
        memory_root=mem,
        project="repo",
        lane="decisions",
        action="write",
        path=decision_path,
        writer="test",
        source_workdir=repo,
        source_commit=old_sha,
    )
    _add_commits(repo, 6)
    fresh_sha = _head_sha(repo)
    _write_milestone(mem, "repo", fresh_sha)

    result = msc.check(
        workdir=repo,
        slug="repo",
        memory_root=mem,
        commits_threshold=5,
    )

    assert result["stale"] is False
    assert result["commits_stale"] == 0
    assert result["memory_as_of_commit"] == fresh_sha
    assert result["baseline_source"] == "milestones"


def test_unmerged_descendant_milestone_cannot_mask_reachable_staleness(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    mem = tmp_path / "memory"
    _init_repo(repo)
    old_main = _head_sha(repo)
    decision_path = mem / "projects" / "repo" / "decisions" / "0001-test.md"
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text("body\n")
    mul.append_update(
        memory_root=mem,
        project="repo",
        lane="decisions",
        action="write",
        path=decision_path,
        writer="test",
        source_workdir=repo,
        source_commit=old_main,
    )

    _git(["checkout", "-b", "feature"], repo)
    _add_commits(repo, 1)
    feature_commit = _head_sha(repo)
    _git(["checkout", "main"], repo)
    _add_commits(repo, 1)
    _write_milestone(mem, "repo", feature_commit)

    result = msc.check(
        workdir=repo,
        slug="repo",
        memory_root=mem,
        commits_threshold=5,
    )

    assert result["baseline_source"] == "updates_ledger"
    assert result["memory_as_of_commit"] == old_main
    assert result["commits_stale"] == 1
    excluded = next(row for row in result["baseline_candidates"] if row["source"] == "milestones")
    assert excluded["reachable_from_head"] is False
    assert excluded["excluded_reason"] == "candidate is not an ancestor of HEAD"


# ---------------------------------------------------------------------------
# Test: no milestones.jsonl → not stale, reason "no milestone baseline"
# ---------------------------------------------------------------------------

def test_no_milestones_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    mem = tmp_path / "memory"
    _init_repo(repo)
    # do NOT write milestones.jsonl

    result = msc.check(
        workdir=repo,
        slug="repo",
        memory_root=mem,
        commits_threshold=5,
    )

    assert result["stale"] is False
    assert "no milestone baseline" in result["reason"]
    assert result["memory_as_of_commit"] is None


# ---------------------------------------------------------------------------
# Test: non-git workdir → fail-soft exit 0
# ---------------------------------------------------------------------------

def test_non_git_workdir(tmp_path: Path) -> None:
    non_git = tmp_path / "not_a_repo"
    non_git.mkdir()
    mem = tmp_path / "memory"

    result = msc.check(
        workdir=non_git,
        slug="not_a_repo",
        memory_root=mem,
        commits_threshold=5,
    )

    assert result["stale"] is False
    assert "not a git repository" in result["reason"]


# ---------------------------------------------------------------------------
# Test: CLI exit code is always 0 (stale case)
# ---------------------------------------------------------------------------

def test_cli_always_exits_zero(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    mem = tmp_path / "memory"
    _init_repo(repo)

    sha = _head_sha(repo)
    _write_milestone(mem, "repo", sha)
    _add_commits(repo, 10)

    r = subprocess.run(
        [
            sys.executable,
            str(HERE / "memory_staleness_check.py"),
            "--workdir", str(repo),
            "--project", "repo",
            "--memory-root", str(mem),
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["stale"] is True
    assert "[MEMORY STALE]" in r.stderr


# ---------------------------------------------------------------------------
# Test: threshold boundary (exactly threshold → stale; one below → not stale)
# ---------------------------------------------------------------------------

def test_threshold_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    mem = tmp_path / "memory"
    _init_repo(repo)

    sha = _head_sha(repo)
    _write_milestone(mem, "repo", sha)

    # 4 commits → threshold 5 → not stale
    _add_commits(repo, 4)
    result = msc.check(workdir=repo, slug="repo", memory_root=mem, commits_threshold=5)
    assert result["stale"] is False
    assert result["commits_stale"] == 4

    # 1 more → exactly 5 → stale
    _add_commits(repo, 1)
    result = msc.check(workdir=repo, slug="repo", memory_root=mem, commits_threshold=5)
    assert result["stale"] is True
    assert result["commits_stale"] == 5
