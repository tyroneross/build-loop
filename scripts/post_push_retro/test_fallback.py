# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Fallback never silently skips (acceptance #4): backlog vs Ops routing, and a
durable local witness when the CLI route fails."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from post_push_retro import fallback  # noqa: E402


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["git", "-C", str(repo), "init", "-q", "-b", "main"])
    subprocess.check_call(["git", "-C", str(repo), "config", "user.email", "t@e.com"])
    subprocess.check_call(["git", "-C", str(repo), "config", "user.name", "T"])
    (repo / "x").write_text("1")
    subprocess.check_call(["git", "-C", str(repo), "add", "x"])
    subprocess.check_call(["git", "-C", str(repo), "commit", "-q", "-m", "init"])


def test_build_loop_repo_routes_to_backlog(tmp_path):
    repo = tmp_path / "build-loop"
    _init(repo)
    calls = {}

    def fake_backlog(**kw):
        calls.update(kw)
        return {"filed": True, "id": "bl-123"}

    r = fallback.write(repo, "aaa..bbb", "medium", "Fable down", backlog_fn=fake_backlog)
    assert r["route"] == "backlog"
    assert r["filed"] is True
    assert r["witness"] is None  # CLI succeeded => no local marker needed
    assert "medium" in calls["title"]


def test_other_repo_routes_to_ops(tmp_path):
    repo = tmp_path / "some-app"
    _init(repo)
    seen = {}

    def fake_ops(**kw):
        seen.update(kw)
        return {"filed": True, "task_id": 7}

    r = fallback.write(repo, "aaa..bbb", "substantial", "budget", ops_fn=fake_ops)
    assert r["route"] == "ops"
    assert r["filed"] is True
    assert seen["name"] == "some-app"


def test_cli_failure_writes_local_witness(tmp_path):
    # THE falsifier guard: retro deferred + fallback CLI unavailable must still
    # leave a durable witness (no silent skip, even from a DEVNULL detached job).
    repo = tmp_path / "build-loop"
    _init(repo)

    def broken_backlog(**kw):
        raise FileNotFoundError("backlog.py missing")

    r = fallback.write(repo, "aaa..bbb", "substantial", "Fable down",
                       backlog_fn=broken_backlog)
    assert r["filed"] is False
    assert r["witness"] is not None
    witness = Path(r["witness"])
    assert witness.exists()
    assert "retro-failed-" in witness.name


def test_cli_returns_filed_false_writes_witness(tmp_path):
    repo = tmp_path / "app"
    _init(repo)
    r = fallback.write(repo, "r..r", "medium", "x", ops_fn=lambda **k: {"filed": False})
    assert r["filed"] is False
    assert Path(r["witness"]).exists()


def test_dry_run_files_nothing(tmp_path):
    repo = tmp_path / "build-loop"
    _init(repo)
    r = fallback.write(repo, "a..b", "medium", "x", dry_run=True)
    assert r["filed"] is False
    assert r["route"].startswith("dry-run")
    assert r["witness"] is None


def test_is_build_loop_repo_by_name(tmp_path):
    repo = tmp_path / "build-loop"
    _init(repo)
    assert fallback.is_build_loop_repo(repo) is True


def test_is_build_loop_repo_by_manifest(tmp_path):
    repo = tmp_path / "renamed"
    _init(repo)
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "plugin.json").write_text('{"name": "build-loop"}')
    assert fallback.is_build_loop_repo(repo) is True


def test_non_build_loop_repo(tmp_path):
    repo = tmp_path / "some-app"
    _init(repo)
    assert fallback.is_build_loop_repo(repo) is False
