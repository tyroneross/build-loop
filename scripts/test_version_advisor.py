#!/usr/bin/env python3
"""Focused tests for the declared release-bump advisor."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import version_advisor as advisor


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "build-loop", "version": "1.2.3"}) + "\n"
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "release 1.2.3")
    return tmp_path


def test_declared_bump_defaults_to_patch_and_accepts_explicit_kinds():
    assert advisor.declared_bump_kind("") == "patch"
    assert advisor.declared_bump_kind("bump: MINOR\n") == "minor"
    assert advisor.declared_bump_kind("notes\nbump: major\n") == "major"
    assert advisor.declared_bump_kind("bump: enormous") == "patch"


def test_breaking_commits_are_surfaced_without_changing_declared_kind():
    messages = ["feat!: remove legacy API", "fix: ordinary", "docs: BREAKING CHANGE details"]
    assert advisor.breaking_commits(messages) == [messages[0], messages[2]]
    assert advisor.bump_version("1.2.3", "patch") == "1.2.4"


def test_cli_reports_declared_minor_and_breaking_commits(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "change.txt").write_text("x\n")
    _git(repo, "add", "change.txt")
    _git(repo, "commit", "-qm", "feat!: remove legacy API")
    (repo / ".build-loop").mkdir()
    (repo / ".build-loop" / "release-pending.md").write_text("Ready\nbump: minor\n")

    proc = subprocess.run(
        [sys.executable, str(Path(advisor.__file__)), "--workdir", str(repo)],
        capture_output=True, text=True, check=True,
    )
    result = json.loads(proc.stdout)
    assert result["state"] == "suggest"
    assert result["bump_kind"] == "minor"
    assert result["suggested_version"] == "1.3.0"
    assert result["breaking_commits"] == ["feat!: remove legacy API"]
