# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/check_runtime_memory_tracking.py."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "check_runtime_memory_tracking.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_runtime_memory_tracking", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


guard = _load_module()


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run(repo: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *argv],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@example.com"], root)
    _git(["config", "user.name", "Test"], root)
    return root


def test_all_mode_blocks_tracked_runtime_memory(repo: Path) -> None:
    target = repo / ".episodic" / "events.jsonl"
    target.parent.mkdir()
    target.write_text("{}\n")
    _git(["add", "-A"], repo)

    result = _run(repo, "--all")

    assert result.returncode == 1
    assert ".episodic/events.jsonl" in result.stderr


def test_all_mode_allows_public_plugin_metadata_dirs(repo: Path) -> None:
    for rel in [".claude-plugin/plugin.json", ".codex-plugin/plugin.json"]:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"name": "ok"}\n')
    _git(["add", "-A"], repo)

    result = _run(repo, "--all")

    assert result.returncode == 0, result.stderr


def test_staged_mode_blocks_new_runtime_memory(repo: Path) -> None:
    target = repo / ".build-loop" / "state.json"
    target.parent.mkdir()
    target.write_text("{}\n")
    _git(["add", "-A"], repo)

    result = _run(repo)

    assert result.returncode == 1
    assert ".build-loop/state.json" in result.stderr


def test_all_mode_blocks_repo_local_rally_point_state(repo: Path) -> None:
    target = repo / ".agent-rally-point" / "apps" / "example" / "changes.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n")
    _git(["add", "-A"], repo)

    result = _run(repo, "--all")

    assert result.returncode == 1
    assert ".agent-rally-point/apps/example/changes.jsonl" in result.stderr


def test_staged_deletion_is_allowed(repo: Path) -> None:
    target = repo / ".semantic" / "TAXONOMY.md"
    target.parent.mkdir()
    target.write_text("# taxonomy\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "seed", "--no-verify"], repo)
    _git(["rm", "--cached", "-r", ".semantic"], repo)

    result = _run(repo)

    assert result.returncode == 0, result.stderr


def test_not_a_git_repo_exits_2(tmp_path: Path) -> None:
    loose = tmp_path / "loose"
    loose.mkdir()

    result = _run(loose, "--all")

    assert result.returncode == 2


def test_path_match_uses_exact_segments() -> None:
    assert guard._is_blocked_path("foo/.episodic/bar.md")
    assert not guard._is_blocked_path(".episodic-plugin/bar.md")


def test_codex_hooks_json_allowed_but_other_codex_runtime_blocked() -> None:
    # Distributable config under .codex/ is intentionally tracked...
    assert not guard._is_blocked_path(".codex/hooks.json")
    # ...but real .codex/ runtime state is still blocked.
    assert guard._is_blocked_path(".codex/memories/MEMORY.md")
    assert guard._is_blocked_path(".codex/state.json")
