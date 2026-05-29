# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/rally_point/channel_paths.py — channel path resolver (D1).

Coverage:
  - slug parity: identical canonical slug from a temp `git worktree` and
    the main checkout (the D1 defect — explicit temp-worktree subprocess test)
  - fallback to derive_slug_from_cwd only when NOT in a git repo
  - app_channel_dir resolves under the configured apps root (HOME-scoped by default)
  - traversal-y slug raises (reuse _paths._safe_project_tag)
  - <slug>/workers sub-component path joins (OQ1)
  - lazy-create idempotent; absent root never creates outside root
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import channel_paths as ap  # noqa: E402


def _git(args, cwd):
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.fixture()
def temp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "my-cool-app"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "f.txt").write_text("x")
    _git(["add", "."], repo)
    _git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"], repo)
    return repo


def test_slug_parity_worktree_vs_main(temp_repo: Path, tmp_path: Path):
    """D1: slug is identical from the main checkout and from a git worktree."""
    main_slug = ap.app_slug(cwd=temp_repo)
    wt = tmp_path / "wt-xyz"
    _git(["worktree", "add", "-q", str(wt), "HEAD"], temp_repo)
    wt_slug = ap.app_slug(cwd=wt)
    assert main_slug == wt_slug == "my-cool-app"


def test_channel_dir_parity_worktree_vs_main(
    temp_repo: Path, tmp_path: Path, monkeypatch
):
    """Two worktrees of one repo resolve to the SAME slug AND channel_dir.

    The channel-split defect: a worktree path keyed verbatim by the native
    resolver lands in a different (empty) room. Canonicalization makes the
    slug and the embedded-fallback channel_dir identical from either path.
    """
    monkeypatch.setenv(
        "BUILD_LOOP_APPS_ROOT", str(tmp_path / ".build-loop" / "apps")
    )
    main_slug = ap.app_slug(cwd=temp_repo)
    main_dir = ap.app_channel_dir(main_slug)

    wt = tmp_path / "wt-parity"
    _git(["worktree", "add", "-q", str(wt), "HEAD"], temp_repo)
    wt_slug = ap.app_slug(cwd=wt)
    wt_dir = ap.app_channel_dir(wt_slug)

    assert main_slug == wt_slug == "my-cool-app"
    assert main_dir == wt_dir


def test_canonical_workdir_worktree_collapses_to_main(
    temp_repo: Path, tmp_path: Path
):
    """canonical_workdir maps both the main checkout and a worktree to the
    SAME canonical repo root (so all discovery resolvers get one path)."""
    main_root = ap.canonical_workdir(temp_repo)
    wt = tmp_path / "wt-canon"
    _git(["worktree", "add", "-q", str(wt), "HEAD"], temp_repo)
    wt_root = ap.canonical_workdir(wt)
    assert main_root == wt_root == temp_repo.resolve()


def test_canonical_workdir_non_git_unchanged(tmp_path: Path):
    """Non-git path is returned unchanged (resolved), preserving _unscoped
    behavior downstream — does not break derive_slug_from_cwd."""
    nongit = tmp_path / "loose-canon"
    nongit.mkdir()
    assert ap.canonical_workdir(nongit) == nongit.resolve()
    # And the slug path still yields _unscoped for the same dir.
    assert ap.app_slug(cwd=nongit) == "_unscoped"


def test_subcomponent_workers(temp_repo: Path):
    workers = temp_repo / "workers"
    workers.mkdir()
    assert ap.app_slug(cwd=workers) == "my-cool-app/workers"


def test_fallback_when_not_git(tmp_path: Path):
    """No .git anywhere → falls back to derive_slug_from_cwd → _unscoped."""
    nongit = tmp_path / "loose"
    nongit.mkdir()
    assert ap.app_slug(cwd=nongit) == "_unscoped"


def test_channel_dir_under_home(temp_repo: Path, tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", str(home / ".build-loop" / "apps"))
    d = ap.app_channel_dir(ap.app_slug(cwd=temp_repo))
    assert str(d).startswith(str(home))
    assert d.name == "my-cool-app"
    assert not d.exists()  # resolver never creates implicitly


def test_lazy_create_idempotent(temp_repo: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv(
        "BUILD_LOOP_APPS_ROOT", str(tmp_path / ".build-loop" / "apps")
    )
    slug = ap.app_slug(cwd=temp_repo)
    d1 = ap.ensure_channel_dir(slug)
    d2 = ap.ensure_channel_dir(slug)
    assert d1 == d2 and d1.is_dir()


def test_traversal_slug_rejected():
    with pytest.raises(ValueError):
        ap.app_channel_dir("../../etc")
    with pytest.raises(ValueError):
        ap.app_channel_dir("a/../../b")


def test_channel_subpaths(temp_repo: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv(
        "BUILD_LOOP_APPS_ROOT", str(tmp_path / ".build-loop" / "apps")
    )
    slug = "my-cool-app/workers"
    d = ap.app_channel_dir(slug)
    assert d.parts[-2:] == ("my-cool-app", "workers")
