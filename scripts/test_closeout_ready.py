#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/closeout_ready.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import closeout_ready  # noqa: E402


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


def _make_run_worktree(
    repo: Path,
    short: str,
    *,
    extra_commit: bool = True,
) -> tuple[Path, str, str]:
    run_id = f"bl-20260914T000000Z-test-{short}"
    branch = f"bl/run-{short}"
    path = repo / ".build-loop" / "worktrees" / f"run-{short}"
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(path), "main")
    if extra_commit:
        (path / f"{short}.txt").write_text(f"work-{short}\n")
        _git(path, "add", f"{short}.txt")
        _git(path, "commit", "-m", f"work {short}")
    return path, branch, run_id


def _write_state(
    repo: Path,
    run_id: str,
    branch: str,
    path: Path,
    *,
    extra_runs: list[dict] | None = None,
) -> None:
    execution = {
        "build_loop_id": run_id,
        "run_worktree_branch": branch,
        "run_worktree_path": str(path.resolve()),
        "deletion_point": {
            "phase": "D",
            "trigger": "review-g-pass+learn-complete",
            "may_change": True,
        },
    }
    row = {
        "run_id": run_id,
        "outcome": "pass",
        "createdRefs": [{
            "kind": "worktree",
            "branch": branch,
            "path": str(path.resolve()),
            "status": "open",
        }],
    }
    state = {"execution": execution, "runs": [row] + (extra_runs or [])}
    bl = repo / ".build-loop"
    bl.mkdir(exist_ok=True)
    (bl / "state.json").write_text(json.dumps(state, indent=2))


def test_inventory_only_does_not_merge(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "invonly")
    _write_state(repo, run_id, branch, path)

    result = closeout_ready.closeout_ready(repo, run_id=run_id)

    assert result["dry_run"] is True
    assert result["merged"] == []
    assert result["closed"] == []
    assert result["deletion_point"]["phase"] == "D"
    assert result["deletion_point"]["may_change"] is True
    ready = [item for item in result["open_items"] if item["disposition"] == "ready"]
    assert ready, result["open_items"]
    assert ready[0]["branch"] == branch
    assert (repo / "README.md").read_text() == "seed\n"
    assert path.is_dir()
    assert _git(repo, "rev-parse", "--verify", f"refs/heads/{branch}").returncode == 0


def test_owner_released_ff_merges_and_closes(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "ffclose")
    _write_state(repo, run_id, branch, path)

    result = closeout_ready.closeout_ready(
        repo, run_id=run_id, owner_released=True
    )

    assert result["errors"] == [], result
    assert result["merged"]
    assert result["merged"][0]["mode"] == "ff"
    assert (repo / "ffclose.txt").read_text() == "work-ffclose\n"
    assert not path.exists()
    verify = _git(repo, "rev-parse", "--verify", f"refs/heads/{branch}", check=False)
    assert verify.returncode != 0
    assert result["closed"]


def test_uncommitted_tracked_changes_block_merge(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "dirty")
    (path / "dirty.txt").write_text("uncommitted\n")
    _git(path, "add", "dirty.txt")
    _write_state(repo, run_id, branch, path)

    result = closeout_ready.closeout_ready(
        repo, run_id=run_id, owner_released=True
    )

    assert result["merged"] == []
    blocked = [item for item in result["open_items"] if item["disposition"] == "blocked"]
    assert blocked
    assert "uncommitted" in blocked[0]["reason"]
    assert path.is_dir()


def test_unmerged_leftover_is_inventoried_not_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    this_path, this_branch, this_run = _make_run_worktree(repo, "current")
    left_path, left_branch, left_run = _make_run_worktree(repo, "leftover")
    _write_state(
        repo,
        this_run,
        this_branch,
        this_path,
        extra_runs=[{
            "run_id": left_run,
            "createdRefs": [{
                "kind": "worktree",
                "branch": left_branch,
                "path": str(left_path.resolve()),
                "status": "open",
            }],
        }],
    )
    # Age is irrelevant; leftover is unmerged so it must stay.
    os.utime(left_path, (time.time() - 10_000, time.time() - 10_000))

    result = closeout_ready.closeout_ready(
        repo, run_id=this_run, owner_released=True
    )

    leftover = [
        item
        for item in result["open_items"]
        if item.get("branch") == left_branch
    ]
    assert leftover, result["open_items"]
    assert leftover[0]["disposition"] == "preserve-only"
    assert left_path.is_dir()


def test_writes_open_items_sidecar(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "side")
    _write_state(repo, run_id, branch, path)

    result = closeout_ready.closeout_ready(repo, run_id=run_id)
    sidecar = Path(result["inventory_path"])
    assert sidecar.is_file()
    payload = json.loads(sidecar.read_text())
    assert payload["run_id"] == run_id
    assert payload["open_items"]


def test_named_build_loop_worktree_is_inventoried(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "namedinv", extra_commit=False)
    named = repo / ".build-loop" / "worktrees" / "npm-release-test"
    _git(repo, "worktree", "add", "-b", "bl/npm-release-test", str(named), "main")
    _write_state(repo, run_id, branch, path)

    result = closeout_ready.closeout_ready(repo, run_id=run_id)
    named_items = [
        item for item in result["open_items"] if item.get("branch") == "bl/npm-release-test"
    ]
    assert named_items
    assert named_items[0]["disposition"] == "preserve-only"
    assert "without run-*" in named_items[0]["reason"]


def test_plan_verify_warns_when_worktree_lacks_deletion_point(tmp_path: Path) -> None:
    import plan_verify  # noqa: WPS433

    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\nProvision an isolation worktree for the implementer.\n")
    findings = [
        row
        for row in plan_verify.run_all(plan, tmp_path)
        if row["rule_id"] == "deletion-point-declared"
    ]
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARN"

    plan.write_text(
        "# Plan\nProvision an isolation worktree.\n"
        "deletion_point: phase-d after Review-G + Learn (may_change: true)\n"
    )
    findings = [
        row
        for row in plan_verify.run_all(plan, tmp_path)
        if row["rule_id"] == "deletion-point-declared"
    ]
    assert findings == []
