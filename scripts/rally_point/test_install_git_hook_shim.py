# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rollout tests for the runtime-memory guard shim (Codex review 2026-08-28).

The review's concrete defect: a pre-existing installed shim pinned to a
pruned plugin-cache path crashed with FileNotFoundError and bricked every
commit; the template fix alone only helps FUTURE installs. These tests pin
the corrected contract: install() places a LOCAL checker copy under
.git/hooks and the shim (a) enforces through that copy, (b) warns and passes
when the copy is missing rather than crashing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_git_hook as igh  # noqa: E402


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@example.com"], root)
    _git(["config", "user.name", "Test"], root)
    (root / "README.md").write_text("x\n")
    _git(["add", "README.md"], root)
    _git(["commit", "-q", "-m", "init", "--no-verify"], root)
    return root


def _shim_and_checker(repo: Path) -> tuple[Path, Path]:
    hooks = repo / ".git" / "hooks"
    return (hooks / ".runtime-memory-tracking-check.py",
            hooks / ".runtime-memory-checker.py")


def _run_shim(shim: Path, repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(shim)], cwd=str(repo),
                          capture_output=True, text=True)


def test_install_places_local_checker_and_pins_shim(repo: Path) -> None:
    assert igh.install(repo)
    shim, local = _shim_and_checker(repo)
    assert shim.exists() and local.exists()
    assert str(local) in shim.read_text()


def test_shim_enforces_through_local_copy(repo: Path) -> None:
    igh.install(repo)
    shim, _ = _shim_and_checker(repo)
    planted = repo / ".build-loop" / "new.md"
    planted.parent.mkdir()
    planted.write_text("planted\n")
    _git(["add", "-f", str(planted)], repo)
    result = _run_shim(shim, repo)
    assert result.returncode == 1
    assert ".build-loop/new.md" in result.stderr


def test_shim_warns_and_passes_when_checker_vanishes(repo: Path) -> None:
    igh.install(repo)
    shim, local = _shim_and_checker(repo)
    local.unlink()
    result = _run_shim(shim, repo)
    assert result.returncode == 0
    assert "checker missing" in result.stderr
