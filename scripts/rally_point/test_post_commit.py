# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Integration test for the post-commit capture path.

A real commit in a temp repo (with the hook installed) writes exactly
one `commit` record + bumps revision; a dependency-manifest commit also
writes a `dep-change`. The hook is fire-and-forget (exit 0, never fails
the commit).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import channel_paths as ap  # noqa: E402
import changes as ch  # noqa: E402
import install_git_hook as igh  # noqa: E402
import revision as rev  # noqa: E402


def _git(args, cwd, **env):
    e = None
    if env:
        import os
        e = {**os.environ, **env}
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=e)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    apps = tmp_path / "apps"
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", str(apps))
    r = tmp_path / "depapp"
    r.mkdir()
    _git(["init", "-q"], r)
    _git(["config", "user.email", "t@e.com"], r)
    _git(["config", "user.name", "t"], r)
    # The pre-commit guard fails closed without a denylist; provide one
    # so the install-and-commit flow under test is not blocked by it.
    (r / ".private-slugs").write_text("nonexistent-private-slug\n")
    igh.install(r)
    return r


def _channel(repo: Path) -> Path:
    return ap.app_channel_dir(ap.app_slug(cwd=repo))


def test_plain_commit_writes_one_record(repo: Path, monkeypatch):
    monkeypatch.setenv("APP_PULSE_TOOL", "codex")
    monkeypatch.setenv("APP_PULSE_RUN_ID", "run-42")
    (repo / "src.py").write_text("print(1)\n")
    _git(["add", "."], repo)
    _git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "code"], repo)
    chan = _channel(repo)
    # hook is backgrounded — poll briefly
    for _ in range(50):
        recs, _o = ch.read_changes_since(chan, 0)
        if recs:
            break
        time.sleep(0.1)
    recs, _o = ch.read_changes_since(chan, 0)
    commits = [r for r in recs if r["kind"] == "commit"]
    assert len(commits) == 1
    assert commits[0]["tool"] == "codex" and commits[0]["run_id"] == "run-42"
    assert rev.read_revision(chan) >= 1
    assert not any(r["kind"] == "dep-change" for r in recs)


def test_manifest_commit_also_writes_dep_change(repo: Path):
    (repo / "package.json").write_text('{"name":"x"}\n')
    _git(["add", "."], repo)
    _git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "deps"], repo)
    chan = _channel(repo)
    for _ in range(50):
        recs, _o = ch.read_changes_since(chan, 0)
        if any(r["kind"] == "dep-change" for r in recs):
            break
        time.sleep(0.1)
    recs, _o = ch.read_changes_since(chan, 0)
    kinds = [r["kind"] for r in recs]
    assert "commit" in kinds and "dep-change" in kinds


def test_hook_never_fails_commit(repo: Path, monkeypatch):
    # point apps root at an unwritable location; commit must still succeed
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", "/proc/cannot/write/here")
    (repo / "a.txt").write_text("x")
    _git(["add", "."], repo)
    # check=True would raise if the hook returned non-zero
    _git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "ok"], repo)
