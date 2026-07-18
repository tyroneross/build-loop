# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/verify_worktree_target.py.

Covers the "worktree targets the wrong repo" mechanism: an isolation
worktree/cwd must resolve to the SAME repo as the intended target — either
because it IS the intended repo (same toplevel), or because it is a
legitimate `git worktree` of the intended repo (different toplevel, same
git-common-dir). A different repo entirely (different common-dir, different
origin) is a mismatch that must emit a `git worktree add` self-correction
command targeting the intended repo.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import verify_worktree_target as vwt

SCRIPT_PATH = Path(__file__).resolve().parent / "verify_worktree_target.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path, origin: str | None = None) -> None:
    """Initialise a minimal git repo with a first commit and deterministic identity."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(path), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path), check=True, capture_output=True,
    )
    (path / "README.md").write_text("test repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(path), check=True, capture_output=True,
    )
    if origin:
        subprocess.run(
            ["git", "remote", "add", "origin", origin],
            cwd=str(path), check=True, capture_output=True,
        )


def _run_cli(*args: str) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
    )
    return result.returncode, json.loads(result.stdout)


# ---------------------------------------------------------------------------
# normalize_origin
# ---------------------------------------------------------------------------


def test_normalize_origin_ssh_form() -> None:
    assert vwt.normalize_origin("git@github.com:owner/repo.git") == "owner/repo"


def test_normalize_origin_https_form() -> None:
    assert vwt.normalize_origin("https://github.com/owner/repo.git") == "owner/repo"


def test_normalize_origin_https_no_dotgit_suffix() -> None:
    assert vwt.normalize_origin("https://github.com/Owner/Repo") == "owner/repo"


def test_normalize_origin_none_when_absent() -> None:
    assert vwt.normalize_origin(None) is None
    assert vwt.normalize_origin("") is None


# ---------------------------------------------------------------------------
# Case 1: intended == actual (same toplevel) -> match, exit 0
# ---------------------------------------------------------------------------


def test_same_toplevel_matches(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    result = vwt.verify(str(repo), str(repo), None)
    assert result["match"] is True
    assert result["correct_provision_cmd"] is None
    assert result["intended_toplevel"] == result["actual_toplevel"]


def test_same_toplevel_cli_exit_0(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    rc, out = _run_cli("--intended-workdir", str(repo), "--actual-workdir", str(repo))
    assert rc == 0
    assert out["match"] is True


# ---------------------------------------------------------------------------
# Case 2: actual is a real `git worktree` of the intended repo -> match, exit 0
# ---------------------------------------------------------------------------


def test_legit_worktree_of_intended_repo_matches(tmp_path: Path) -> None:
    intended = tmp_path / "intended-repo"
    _init_git_repo(intended)

    wt_path = tmp_path / "some-worktree-location"
    subprocess.run(
        ["git", "worktree", "add", "-b", "wt-branch", str(wt_path), "main"],
        cwd=str(intended), check=True, capture_output=True,
    )

    result = vwt.verify(str(intended), str(wt_path), None)
    assert result["match"] is True
    assert result["correct_provision_cmd"] is None
    # Different toplevels but same repo identity.
    assert result["intended_toplevel"] != result["actual_toplevel"]
    assert "common-dir" in result["reason"]


def test_legit_worktree_cli_exit_0(tmp_path: Path) -> None:
    intended = tmp_path / "intended-repo"
    _init_git_repo(intended)

    wt_path = tmp_path / "some-worktree-location"
    subprocess.run(
        ["git", "worktree", "add", "-b", "wt-branch", str(wt_path), "main"],
        cwd=str(intended), check=True, capture_output=True,
    )

    rc, out = _run_cli("--intended-workdir", str(intended), "--actual-workdir", str(wt_path))
    assert rc == 0
    assert out["match"] is True


# ---------------------------------------------------------------------------
# Case 3: actual is a DIFFERENT repo -> mismatch, exit 1, correct_provision_cmd
# references the INTENDED repo's toplevel.
# ---------------------------------------------------------------------------


def test_different_repo_mismatches(tmp_path: Path) -> None:
    intended = tmp_path / "intended-repo"
    _init_git_repo(intended, origin="git@github.com:acme/intended.git")

    actual = tmp_path / "session-repo"
    _init_git_repo(actual, origin="git@github.com:acme/session.git")

    result = vwt.verify(str(intended), str(actual), "my-fix")
    assert result["match"] is False
    assert result["intended_repo"] == "acme/intended"
    assert result["actual_repo"] == "acme/session"
    assert result["correct_provision_cmd"] is not None
    intended_toplevel = str(intended.resolve())
    assert intended_toplevel in result["correct_provision_cmd"]
    assert "worktree add" in result["correct_provision_cmd"]
    assert "-b bl/my-fix" in result["correct_provision_cmd"]


def test_different_repo_no_origin_still_mismatches(tmp_path: Path) -> None:
    """Two unrelated repos with no origin configured must not false-match."""
    intended = tmp_path / "intended-repo"
    _init_git_repo(intended)

    actual = tmp_path / "session-repo"
    _init_git_repo(actual)

    result = vwt.verify(str(intended), str(actual), None)
    assert result["match"] is False
    assert result["correct_provision_cmd"] is not None


def test_different_repo_cli_exit_1(tmp_path: Path) -> None:
    intended = tmp_path / "intended-repo"
    _init_git_repo(intended, origin="git@github.com:acme/intended.git")

    actual = tmp_path / "session-repo"
    _init_git_repo(actual, origin="git@github.com:acme/session.git")

    rc, out = _run_cli(
        "--intended-workdir", str(intended),
        "--actual-workdir", str(actual),
        "--slug", "my-fix",
    )
    assert rc == 1
    assert out["match"] is False
    assert str(intended.resolve()) in out["correct_provision_cmd"]


# ---------------------------------------------------------------------------
# Case 4: non-git path -> exit 2
# ---------------------------------------------------------------------------


def test_non_git_intended_path_errors(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    actual = tmp_path / "actual-repo"
    _init_git_repo(actual)

    result = vwt.verify(str(not_a_repo), str(actual), None)
    assert result["match"] is False
    assert "error" in result
    assert "intended-workdir" in result["error"]


def test_non_git_actual_path_errors(tmp_path: Path) -> None:
    intended = tmp_path / "intended-repo"
    _init_git_repo(intended)
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    result = vwt.verify(str(intended), str(not_a_repo), None)
    assert result["match"] is False
    assert "error" in result
    assert "actual-workdir" in result["error"]


def test_non_git_path_cli_exit_2(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    actual = tmp_path / "actual-repo"
    _init_git_repo(actual)

    rc, out = _run_cli("--intended-workdir", str(not_a_repo), "--actual-workdir", str(actual))
    assert rc == 2
    assert out["match"] is False
    assert "error" in out


# ---------------------------------------------------------------------------
# decide_match / resolve_git_identity — direct unit coverage
# ---------------------------------------------------------------------------


def test_resolve_git_identity_none_for_non_repo(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    assert vwt.resolve_git_identity(str(not_a_repo)) is None


def test_decide_match_same_common_dir() -> None:
    a = {"common_dir": "/repo/.git", "origin_norm": None}
    b = {"common_dir": "/repo/.git", "origin_norm": None}
    match, reason = vwt.decide_match(a, b)
    assert match is True
    assert "common-dir" in reason


def test_decide_match_same_origin_different_common_dir() -> None:
    a = {"common_dir": "/repo-a/.git", "origin_norm": "owner/repo"}
    b = {"common_dir": "/repo-b/.git", "origin_norm": "owner/repo"}
    match, reason = vwt.decide_match(a, b)
    assert match is True
    assert "origin" in reason


def test_decide_match_no_shared_signal_mismatches() -> None:
    a = {"common_dir": "/repo-a/.git", "origin_norm": "owner/repo-a"}
    b = {"common_dir": "/repo-b/.git", "origin_norm": "owner/repo-b"}
    match, reason = vwt.decide_match(a, b)
    assert match is False


# ---------------------------------------------------------------------------
# build_provision_cmd
# ---------------------------------------------------------------------------


def test_build_provision_cmd_uses_worktree_guard_canonicals(tmp_path: Path) -> None:
    intended = tmp_path / "intended-repo"
    cmd = vwt.build_provision_cmd(str(intended), "My Fix!")
    assert cmd.startswith(f"git -C {intended} worktree add ")
    assert ".build-loop/worktrees/my-fix" in cmd
    assert cmd.endswith("-b bl/my-fix")
