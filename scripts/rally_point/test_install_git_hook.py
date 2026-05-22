"""Tests for scripts/rally_point/install_git_hook.py — idempotent installer.

  - only installs inside a git repo
  - idempotent (re-run = no dup)
  - chains an existing post-commit (never clobbers unrelated content)
  - marker-guarded
  - installs the private-slug-guard pre-commit alongside post-commit
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import install_git_hook as igh  # noqa: E402


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(["init", "-q"], r)
    return r


def test_refuses_outside_git(tmp_path: Path):
    assert igh.install(tmp_path / "loose") is False


def test_install_fresh(repo: Path):
    assert igh.install(repo) is True
    hook = repo / ".git" / "hooks" / "post-commit"
    assert hook.exists() and igh.MARKER in hook.read_text()
    import os
    assert os.access(hook, os.X_OK)


def test_idempotent(repo: Path):
    igh.install(repo)
    first = (repo / ".git" / "hooks" / "post-commit").read_text()
    igh.install(repo)
    second = (repo / ".git" / "hooks" / "post-commit").read_text()
    assert first == second
    assert second.count(igh.MARKER) == 1


def test_chains_existing_hook(repo: Path):
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho preexisting-unrelated-hook\n")
    hook.chmod(0o755)
    assert igh.install(repo) is True
    body = hook.read_text()
    assert "preexisting-unrelated-hook" in body  # never clobbered
    assert igh.MARKER in body  # ours appended/chained


def test_migrates_legacy_app_pulse_segment(repo: Path):
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f"{igh.LEGACY_MARKER}\n"
        "APP_PULSE_CAPTURE=.git/hooks/.app-pulse-capture.py\n"
        f"{igh.LEGACY_MARKER_END}\n"
    )
    hook.chmod(0o755)
    assert igh.install(repo) is True
    body = hook.read_text()
    assert igh.MARKER in body
    assert igh.LEGACY_MARKER not in body
    assert ".rally-point-capture.py" in body


def test_installs_pre_commit_guard(repo: Path):
    import os
    assert igh.install(repo) is True
    hook = repo / ".git" / "hooks" / "pre-commit"
    assert hook.exists() and igh.PRE_MARKER in hook.read_text()
    assert os.access(hook, os.X_OK)
    assert (repo / ".git" / "hooks" / ".private-slug-check.py").exists()


def test_pre_commit_idempotent(repo: Path):
    igh.install(repo)
    first = (repo / ".git" / "hooks" / "pre-commit").read_text()
    igh.install(repo)
    second = (repo / ".git" / "hooks" / "pre-commit").read_text()
    assert first == second
    assert second.count(igh.PRE_MARKER) == 1


def test_pre_commit_chains_existing_hook(repo: Path):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho preexisting-precommit-hook\n")
    hook.chmod(0o755)
    assert igh.install(repo) is True
    body = hook.read_text()
    assert "preexisting-precommit-hook" in body  # never clobbered
    assert igh.PRE_MARKER in body  # ours appended/chained
