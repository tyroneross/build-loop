#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the permanently report-only SessionStart worktree hook."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parent.parent / "hooks" / "session-start-worktree-gc.sh"


def _osenv() -> dict[str, str]:
    return dict(os.environ)


def _git(repo: Path, *args: str, check: bool = True) -> str:
    env = {
        **_osenv(),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": str(repo),
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
        env=env,
    ).stdout


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", name, "-q")


def _run_hook(repo: Path, act: str | None = None) -> tuple[str, float]:
    env = {
        **_osenv(),
        "CLAUDE_PROJECT_DIR": str(repo),
        "CLAUDE_PLUGIN_ROOT": str(_HOOK.parent.parent),
    }
    if act is not None:
        env["BUILDLOOP_GC_ACT"] = act
    start = time.monotonic()
    proc = subprocess.run(
        ["bash", str(_HOOK)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.monotonic() - start
    assert proc.returncode == 0
    return (repo / ".build-loop" / "worktree-gc-last.txt").read_text(), elapsed


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "main-repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "base")

    path = root / ".build-loop" / "worktrees" / "run-merged"
    path.parent.mkdir(parents=True)
    branch = "bl/run-merged"
    _git(root, "worktree", "add", "-q", "-b", branch, str(path), "main")
    old = time.time() - 24 * 3600
    os.utime(path, (old, old))

    run_id = "bl-20260711T000000Z-test-merged"
    execution = {
        "build_loop_id": run_id,
        "run_worktree_branch": branch,
        "run_worktree_path": str(path.resolve()),
    }
    state = {
        "execution": {},
        "historicalExecutions": [execution],
        "runs": [{
            "run_id": run_id,
            "createdRefs": [{
                "kind": "worktree",
                "branch": branch,
                "path": str(path.resolve()),
                "status": "open",
            }],
        }],
    }
    (root / ".build-loop" / "state.json").write_text(json.dumps(state, indent=2))
    return root


def _assert_candidate_survives(repo: Path) -> None:
    assert (repo / ".build-loop" / "worktrees" / "run-merged").exists()
    assert _git(
        repo,
        "show-ref",
        "--verify",
        "refs/heads/bl/run-merged",
        check=False,
    ).strip()


def test_default_session_start_is_report_only(repo: Path) -> None:
    report, _ = _run_hook(repo)

    _assert_candidate_survives(repo)
    assert "Mode: REPORT-ONLY" in report
    assert '"branch": "bl/run-merged"' in report
    assert not list((repo / ".build-loop" / "bundles").glob("*.bundle"))


def test_legacy_act_environment_variable_is_ignored(repo: Path) -> None:
    report, _ = _run_hook(repo, act="1")

    _assert_candidate_survives(repo)
    assert "BUILDLOOP_GC_ACT=1 ignored" in report
    assert "owner-released finalizer" in report


def test_hook_contains_no_direct_git_mutation_commands() -> None:
    source = _HOOK.read_text()
    forbidden = (
        "git bundle create",
        "git worktree remove",
        "git branch -D",
        "git worktree prune",
        "shutil.rmtree",
    )
    for command in forbidden:
        assert command not in source


def test_hook_does_not_prune_stale_git_metadata(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "base")
    path = root / ".build-loop" / "worktrees" / "run-stale"
    path.parent.mkdir(parents=True)
    _git(root, "worktree", "add", "-q", "-b", "bl/run-stale", str(path), "main")
    shutil.rmtree(path)
    state = {"execution": {}, "runs": []}
    (root / ".build-loop" / "state.json").write_text(json.dumps(state))
    before = _git(root, "worktree", "list", "--porcelain")

    _run_hook(root)

    after = _git(root, "worktree", "list", "--porcelain")
    assert before == after
    assert "run-stale" in after


def test_no_candidate_path_stays_under_two_second_budget(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "base")
    (root / ".build-loop" / "worktrees").mkdir(parents=True)
    (root / ".build-loop" / "state.json").write_text(
        json.dumps({"execution": {}, "runs": []})
    )

    report, elapsed = _run_hook(root)

    assert elapsed < 2.0
    assert "candidates=0" in report
    assert list((root / ".build-loop" / "worktree-gc").glob("*.json"))
