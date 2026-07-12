# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Safety contract tests for the report-only worktree reaper."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PKG = _HERE.parent
_SCRIPTS = _PKG.parent
_REPO = _SCRIPTS.parent
for _d in (_REPO, _SCRIPTS, _PKG):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from worktree_reaper.reaper import reap_worktrees  # noqa: E402


def _git(workdir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
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
    unmerged: bool = False,
) -> tuple[Path, str, str]:
    run_id = f"bl-20260711T000000Z-test-{short}"
    branch = f"bl/run-{short}"
    path = repo / ".build-loop" / "worktrees" / f"run-{short}"
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(path), "main")
    if unmerged:
        (path / "work.txt").write_text(f"work-{short}\n")
        _git(path, "add", "work.txt")
        _git(path, "commit", "-m", f"work {short}")
    return path, branch, run_id


def _age_folder(path: Path, hours: float = 24) -> None:
    timestamp = time.time() - (hours * 3600)
    os.utime(path, (timestamp, timestamp))


def _write_state(
    repo: Path,
    run_id: str,
    branch: str,
    path: Path,
    *,
    active: bool = False,
    duplicate: bool = False,
) -> None:
    execution = {
        "build_loop_id": run_id,
        "run_worktree_branch": branch,
        "run_worktree_path": str(path.resolve()),
    }
    row = {
        "run_id": run_id,
        "createdRefs": [{
            "kind": "worktree",
            "branch": branch,
            "path": str(path.resolve()),
            "status": "open",
        }],
    }
    state = {
        "execution": execution if active else {},
        "historicalExecutions": [] if active else [execution],
        "runs": [row],
    }
    if duplicate:
        state["runs"].append(
            {
                "run_id": run_id + "-other",
                "createdRefs": [{
                    "branch": branch,
                    "path": str(path.resolve()),
                    "status": "open",
                }],
            }
        )
    bl = repo / ".build-loop"
    bl.mkdir(exist_ok=True)
    (bl / "state.json").write_text(json.dumps(state, indent=2))


def test_default_is_report_only_and_non_destructive(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "111111")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    result = reap_worktrees(repo)

    assert result.dry_run is True
    assert result.candidates == [{"path": str(path), "branch": branch, "run_id": run_id}]
    assert result.bundled_and_removed == []
    assert path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0
    assert not (repo / ".build-loop" / "bundles").exists()


def test_act_without_owner_release_remains_report_only(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "222222")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    result = reap_worktrees(repo, dry_run=False, act=True, owner_released=False)

    assert result.dry_run is True
    assert any("owner-released" in row["reason"] for row in result.errors)
    assert path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0


def test_explicit_owner_released_act_delegates_to_strict_collapse(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "333333")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    result = reap_worktrees(
        repo,
        dry_run=False,
        act=True,
        owner_released=True,
    )

    assert result.errors == []
    assert len(result.bundled_and_removed) == 1
    finalized = result.bundled_and_removed[0]
    assert Path(finalized["bundle"]).is_file()
    assert Path(finalized["receipt"]).is_file()
    assert not path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode != 0


def test_active_worktree_is_never_delegated(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "444444")
    _age_folder(path)
    _write_state(repo, run_id, branch, path, active=True)

    result = reap_worktrees(
        repo,
        dry_run=False,
        act=True,
        owner_released=True,
    )

    assert any(row["branch"] == branch for row in result.skipped_active)
    assert result.bundled_and_removed == []
    assert path.exists()


def test_young_and_unmerged_worktrees_are_preserved(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    young, young_branch, young_run = _make_run_worktree(repo, "555555")
    _write_state(repo, young_run, young_branch, young)
    result = reap_worktrees(repo)
    assert any(row["path"] == str(young) for row in result.skipped_too_young)
    assert young.exists()

    # Use a separate repo because state attribution deliberately names one run.
    repo2 = _make_repo(tmp_path / "second")
    unmerged, branch, run_id = _make_run_worktree(repo2, "666666", unmerged=True)
    _age_folder(unmerged)
    _write_state(repo2, run_id, branch, unmerged)
    result2 = reap_worktrees(
        repo2,
        dry_run=False,
        act=True,
        owner_released=True,
    )
    assert any(row["branch"] == branch for row in result2.skipped_unmerged)
    assert unmerged.exists()
    assert _git(repo2, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0


def test_orphan_and_ambiguous_candidates_are_preserved(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    orphan = repo / ".build-loop" / "worktrees" / "run-orphan"
    orphan.mkdir(parents=True)
    (orphan / "data.txt").write_text("unknown\n")
    _age_folder(orphan)
    (repo / ".build-loop" / "state.json").write_text(json.dumps({"runs": []}))

    orphan_result = reap_worktrees(
        repo,
        dry_run=False,
        act=True,
        owner_released=True,
    )
    assert any(row["path"] == str(orphan) for row in orphan_result.skipped_unattributed)
    assert orphan.exists()
    assert orphan_result.removed_orphan == []

    path, branch, run_id = _make_run_worktree(repo, "777777")
    _age_folder(path)
    _write_state(repo, run_id, branch, path, duplicate=True)
    ambiguous = reap_worktrees(
        repo,
        dry_run=False,
        act=True,
        owner_released=True,
    )
    assert any(
        row.get("branch") == branch and "ambiguous" in row["reason"]
        for row in ambiguous.skipped_unattributed
    )
    assert path.exists()


def test_pathless_or_mismatched_attribution_is_never_delegated(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "787878")
    _age_folder(path)
    state = {
        "execution": {},
        "runs": [{
            "run_id": run_id,
            "createdRefs": [{"branch": branch, "status": "open"}],
        }],
    }
    (repo / ".build-loop/state.json").write_text(json.dumps(state, indent=2))

    pathless = reap_worktrees(
        repo,
        dry_run=False,
        act=True,
        owner_released=True,
    )

    assert pathless.bundled_and_removed == []
    assert any(
        row.get("branch") == branch and "no unique durable run attribution" in row["reason"]
        for row in pathless.skipped_unattributed
    )
    assert path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0


def test_non_run_prefixed_folder_is_ignored(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    other = repo / ".build-loop" / "worktrees" / "dispatch-1"
    other.mkdir(parents=True)
    _age_folder(other)
    (repo / ".build-loop" / "state.json").write_text(json.dumps({"runs": []}))

    result = reap_worktrees(repo)

    assert any(row["path"] == str(other) for row in result.skipped_not_run)
    assert other.exists()


def test_both_cli_entry_modes_default_to_report_only(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "888888")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    commands = [
        [sys.executable, "-m", "scripts.worktree_reaper"],
        [sys.executable, str(_PKG / "__main__.py")],
    ]
    for command in commands:
        proc = subprocess.run(
            [
                *command,
                "--workdir",
                str(repo),
                "--min-age-hours",
                "0",
                "--json",
            ],
            cwd=_REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["dry_run"] is True
        assert payload["candidates"][0]["branch"] == branch
        assert path.exists()


def test_cli_act_requires_owner_release(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "999999")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.worktree_reaper",
            "--workdir",
            str(repo),
            "--act",
            "--json",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 1
    assert path.exists()
    assert "owner-released" in proc.stdout
