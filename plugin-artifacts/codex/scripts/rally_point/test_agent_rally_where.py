# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``scripts/agent_rally.py where`` — channel discovery CLI.

The system gap this closes: every fresh agent joining a Rally Point session
mis-inferred the channel dir as repo-local ``.build-loop/rally_point/`` when
it actually lives in a shared Rally Point channel. Native discovery resolves
under ``~/.agent-rally-point/apps/<repo-id>/``; the embedded fallback uses the
same root with a local ``<slug>``. These tests pin the discovery contract.

Coverage:
  - ``where`` (plain text) prints bare channel_dir on stdout (cd-able)
  - ``where --json`` emits {channel_dir, app_slug} envelope
  - non-git cwd exits non-zero with a clear stderr message
  - slug + channel_dir are worktree-independent (D1 parity)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_RALLY = REPO_ROOT / "scripts" / "agent_rally.py"


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
    repo = tmp_path / "discovery-fix"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "f.txt").write_text("x")
    _git(["add", "."], repo)
    _git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"], repo)
    return repo


def _run_where(workdir: Path, *extra: str, env_apps_root: Path | None = None):
    env = os.environ.copy()
    env["BUILD_LOOP_DISABLE_SIBLING_RALLY"] = "1"
    # These tests pin the *internal-fallback* (apps-root) discovery contract.
    # The bridge's documented test-isolation hook short-circuits all canonical
    # sources — including any ``rally`` binary on PATH — so resolution can't be
    # hijacked by a locally-installed rally CLI resolving the temp repo's
    # ``.rally`` (resolved_via=repo-local-rally-cli). Without it the test passes
    # only on a host with no ``rally`` binary (e.g. CI) and fails locally.
    env["BUILD_LOOP_BRIDGE_INTERNAL_ONLY"] = "1"
    if env_apps_root is not None:
        env["BUILD_LOOP_APPS_ROOT"] = str(env_apps_root)
    return subprocess.run(
        [sys.executable, str(AGENT_RALLY), "where",
         "--workdir", str(workdir), *extra],
        capture_output=True,
        text=True,
        env=env,
    )


def test_where_plain_text_prints_bare_path(temp_repo: Path, tmp_path: Path):
    apps_root = tmp_path / "apps-root"
    result = _run_where(temp_repo, env_apps_root=apps_root)
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    # Must be cd-able — bare path, no prefix/JSON
    assert Path(line).is_absolute()
    assert Path(line).parent == apps_root
    assert Path(line).name.startswith("discovery-fix")
    assert "\n" not in line  # single line


def test_where_json_envelope(temp_repo: Path, tmp_path: Path):
    apps_root = tmp_path / "apps-root"
    result = _run_where(temp_repo, "--json", env_apps_root=apps_root)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert {"channel_dir", "app_slug", "resolved_via"} <= set(payload.keys())
    assert payload["app_slug"].startswith("discovery-fix")
    assert Path(payload["channel_dir"]).parent == apps_root
    assert Path(payload["channel_dir"]).name.startswith("discovery-fix")


def test_where_non_git_returns_unscoped_or_internal_error(tmp_path: Path):
    nongit = tmp_path / "loose"
    nongit.mkdir()
    result = _run_where(nongit, "--json")
    if result.returncode != 0:
        assert "not under a git repository" in result.stderr
        return
    payload = json.loads(result.stdout)
    assert payload["app_slug"].startswith("_unscoped")
    assert Path(payload["channel_dir"]).is_absolute()


def test_where_worktree_matches_main(temp_repo: Path, tmp_path: Path):
    """D1 parity: worktree resolves to the SAME channel_dir as the main checkout."""
    apps_root = tmp_path / "apps-root"
    wt = tmp_path / "wt-x"
    _git(["worktree", "add", "-q", str(wt), "HEAD"], temp_repo)

    main_result = _run_where(temp_repo, "--json", env_apps_root=apps_root)
    wt_result = _run_where(wt, "--json", env_apps_root=apps_root)
    assert main_result.returncode == 0 and wt_result.returncode == 0
    assert json.loads(main_result.stdout) == json.loads(wt_result.stdout)
