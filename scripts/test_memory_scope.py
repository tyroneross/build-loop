#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the throwaway-workdir memory sandbox contract in _paths.py.

Covers the named failure it earns its place against: a benchmark run inside
a scratch workspace (`data/local/bench-runs/<run>/<attempt>/ws`, a git repo
with no remote) wrote decisions/retrospectives into the user's canonical
memory store under `projects/ws/`, polluting it. Every memory writer resolves
its destination through `_paths.memory_store_root()`, so that function is
the single enforcement point under test here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _paths  # noqa: E402
import memory_writer  # noqa: E402


def git_init(path: Path, remote: str | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), check=True)
    if remote:
        subprocess.run(
            ["git", "remote", "add", "origin", remote], cwd=str(path), check=True
        )


def write_lesson(wd: Path) -> None:
    memory_writer.main([
        "--scope", "project",
        "--project", "demo-proj",
        "write",
        "--name", "n",
        "--description", "d",
        "--type", "lesson",
        "--run-id", "r1",
        "--workdir", str(wd),
        "--host", "claude_code",
        "--body", "A complete lesson body.",
    ])


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    for env in (*_paths.MEMORY_STORE_OVERRIDE_ENVS, _paths.MEMORY_DISABLE_ENV):
        monkeypatch.delenv(env, raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("BUILD_LOOP_MEMORY_AUTOCOMMIT", "0")
    _paths.clear_memory_scope_cache()
    yield
    _paths.clear_memory_scope_cache()


def test_temp_dir_skips_canonical(tmp_path, monkeypatch):
    wd = tmp_path / "run1" / "attempt1" / "ws"
    git_init(wd)
    monkeypatch.chdir(wd)

    root = _paths.memory_store_root()
    assert root == wd / ".build-loop" / "memory-sandbox"

    write_lesson(wd)

    canonical = Path(_paths.os.path.expanduser(_paths.NEUTRAL_MEMORY_STORE_ROOT))
    assert not canonical.exists()

    lesson_files = list((root / "projects" / "demo-proj" / "lessons").glob("*.md"))
    assert len(lesson_files) == 1

    skip_log = root / "skipped.jsonl"
    assert skip_log.exists()
    entry = json.loads(skip_log.read_text().splitlines()[0])
    assert entry["reason"].startswith("temp-dir:")

    assert (root / ".gitignore").read_text() == "*\n"


def test_real_repo_with_remote_writes_canonical(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "_temp_roots", lambda: ())
    wd = tmp_path / "myapp"
    git_init(wd, remote="git@example.com:me/myapp.git")
    monkeypatch.chdir(wd)

    scope = _paths.memory_scope()
    assert scope["mode"] == "canonical"

    write_lesson(wd)

    canonical_root = Path(_paths.os.path.expanduser(_paths.NEUTRAL_MEMORY_STORE_ROOT))
    lesson_files = list(
        (canonical_root / "projects" / "demo-proj" / "lessons").glob("*.md")
    )
    assert len(lesson_files) == 1


def test_env_opt_out_disables_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "_temp_roots", lambda: ())
    wd = tmp_path / "myapp2"
    git_init(wd, remote="git@example.com:me/myapp2.git")
    monkeypatch.chdir(wd)
    monkeypatch.setenv(_paths.MEMORY_DISABLE_ENV, "1")

    scope = _paths.memory_scope()
    assert scope["mode"] == "sandbox"
    assert scope["reason"] == _paths.MEMORY_DISABLE_ENV

    canonical_root = Path(_paths.os.path.expanduser(_paths.NEUTRAL_MEMORY_STORE_ROOT))
    assert not canonical_root.exists()


def test_explicit_store_root_override_wins_from_throwaway(tmp_path, monkeypatch):
    wd = tmp_path / "run2" / "attempt1" / "ws"
    git_init(wd)
    monkeypatch.chdir(wd)
    override = tmp_path / "override"
    monkeypatch.setenv("BUILD_LOOP_MEMORY_STORE_ROOT", str(override))

    scope = _paths.memory_scope()
    assert scope["mode"] == "override"
    assert scope["root"] == override

    write_lesson(wd)

    lesson_files = list((override / "projects" / "demo-proj" / "lessons").glob("*.md"))
    assert len(lesson_files) == 1
    assert not (wd / ".build-loop" / "memory-sandbox").exists()


def test_generic_slug_no_remote_is_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "_temp_roots", lambda: ())
    wd = tmp_path / "ws"
    git_init(wd)
    monkeypatch.chdir(wd)

    reason = _paths.throwaway_workdir_reason(wd)
    assert reason == "generic-slug-no-remote:ws"


def test_generic_slug_with_remote_is_canonical(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "_temp_roots", lambda: ())
    wd = tmp_path / "ws"
    git_init(wd, remote="git@example.com:me/ws.git")
    monkeypatch.chdir(wd)

    assert _paths.throwaway_workdir_reason(wd) is None


def test_bench_runs_path_is_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "_temp_roots", lambda: ())
    wd = tmp_path / "lab" / "data" / "local" / "bench-runs" / "r1" / "a1" / "proj"
    git_init(wd, remote="git@example.com:me/proj.git")

    assert _paths.throwaway_workdir_reason(wd) == "bench-runs"


def test_agent_memory_root_alone_is_override(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "_temp_roots", lambda: ())
    wd = tmp_path / "myapp3"
    git_init(wd, remote="git@example.com:me/myapp3.git")
    override = tmp_path / "agent-override"
    monkeypatch.setenv("AGENT_MEMORY_ROOT", str(override))

    scope = _paths.memory_scope(wd)
    assert scope["mode"] == "override"
    assert scope["root"] == override


# ---------------------------------------------------------------------------
# Real-writer-runs-with-cwd-elsewhere regression coverage. The named failure:
# a writer's process cwd is a real, canonical-qualifying repo while its
# ``--workdir``/target argument is a throwaway scratch location — every
# helper below must classify the TARGET, never the process cwd.
# ---------------------------------------------------------------------------


def test_memory_store_root_explicit_workdir_overrides_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "_temp_roots", lambda: ())
    realcwd = tmp_path / "realcwd"
    git_init(realcwd, remote="git@example.com:me/realcwd.git")
    monkeypatch.chdir(realcwd)

    target = tmp_path / "lab" / "data" / "local" / "bench-runs" / "r1" / "a1" / "ws"
    git_init(target)

    root = _paths.memory_store_root(target)
    assert root == target / ".build-loop" / "memory-sandbox"

    # No explicit arg, no override set — the process cwd (a real repo) still
    # resolves canonical, proving the explicit-arg call above isn't just
    # reflecting a global "everything is sandbox now" side effect.
    scope = _paths.memory_scope()
    assert scope["mode"] == "canonical"


def test_set_memory_workdir_affects_zero_arg_helpers(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "_temp_roots", lambda: ())
    realcwd = tmp_path / "realcwd2"
    git_init(realcwd, remote="git@example.com:me/realcwd2.git")
    monkeypatch.chdir(realcwd)

    target = tmp_path / "lab" / "data" / "local" / "bench-runs" / "r2" / "a1" / "ws"
    git_init(target)

    _paths.set_memory_workdir(target)
    try:
        decisions_dir = _paths.project_decisions_dir("ws")
    finally:
        _paths.set_memory_workdir(None)

    assert decisions_dir == (
        target / ".build-loop" / "memory-sandbox" / "projects" / "ws" / "decisions"
    )


def test_retrospective_promote_durable_targets_workdir_not_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "_temp_roots", lambda: ())
    realcwd = tmp_path / "realcwd3"
    git_init(realcwd, remote="git@example.com:me/realcwd3.git")
    monkeypatch.chdir(realcwd)

    target = tmp_path / "lab" / "data" / "local" / "bench-runs" / "r3" / "a1" / "ws"
    git_init(target)

    from retrospective.write import promote_durable

    result = promote_durable(target, "run-x", {"meta": {}})
    assert result["status"] == "ok"
    sandbox_root = target / ".build-loop" / "memory-sandbox"
    assert str(result["durable_path"]).startswith(str(sandbox_root))

    canonical_root = Path(_paths.os.path.expanduser(_paths.NEUTRAL_MEMORY_STORE_ROOT))
    assert not (canonical_root / "projects" / "ws").exists()


def test_memory_writer_cli_uses_workdir_not_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "_temp_roots", lambda: ())
    realcwd = tmp_path / "realcwd4"
    git_init(realcwd, remote="git@example.com:me/realcwd4.git")
    monkeypatch.chdir(realcwd)

    target = tmp_path / "lab" / "data" / "local" / "bench-runs" / "r4" / "a1" / "ws"
    git_init(target)

    memory_writer.main([
        "--scope", "project",
        "--project", "ws",
        "write",
        "--name", "n",
        "--description", "d",
        "--type", "lesson",
        "--run-id", "r1",
        "--workdir", str(target),
        "--host", "claude_code",
        "--body", "A complete lesson body.",
    ])

    sandbox_lessons = target / ".build-loop" / "memory-sandbox" / "projects" / "ws" / "lessons"
    lesson_files = list(sandbox_lessons.glob("*.md"))
    assert len(lesson_files) == 1

    canonical_root = Path(_paths.os.path.expanduser(_paths.NEUTRAL_MEMORY_STORE_ROOT))
    assert not (canonical_root / "projects" / "ws").exists()


def test_backlog_memory_root_respects_repo_workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "_temp_roots", lambda: ())
    monkeypatch.delenv("BUILD_LOOP_MEMORY_DIR", raising=False)

    target = tmp_path / "lab" / "data" / "local" / "bench-runs" / "r5" / "a1" / "ws"
    git_init(target)

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_backlog_under_test_memscope", HERE / "backlog.py"
    )
    assert spec and spec.loader
    bl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bl)

    root = bl.memory_root(target)
    assert root == target / ".build-loop" / "memory-sandbox"

    override = tmp_path / "override-root"
    monkeypatch.setenv("BUILD_LOOP_MEMORY_DIR", str(override))
    assert bl.memory_root(target) == override
