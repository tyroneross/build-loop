#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for append_milestone.py.

Uses a tmp git repo and tmp memory-root to stay fully isolated.
Run with: uv run pytest scripts/test_append_milestone.py -q
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent


def _run(workdir: str, summary: str, memory_root: str, **kwargs) -> dict:
    """Call append_milestone.main() directly and return parsed output."""
    import io
    from contextlib import redirect_stdout

    import append_milestone as am  # local import so sys.path tweak is in effect

    argv = [
        "--workdir", workdir,
        "--summary", summary,
        "--memory-root", memory_root,
        "--json",
    ]
    for k, v in kwargs.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = am.main(argv)
    output = buf.getvalue().strip()
    assert output, f"no output from main(); rc={rc}"
    return json.loads(output)


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit. Returns repo path."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    (repo / "README.md").write_text("hi")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)
    return repo


def _milestones_path(memory_root: Path, slug: str) -> Path:
    parts = slug.split("/")
    return memory_root / "projects" / Path(*parts) / "milestones.jsonl"


def _head_sha(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_appends_one_line(tmp_path):
    """First call appends exactly one line with correct keys and HEAD sha."""
    repo = _make_git_repo(tmp_path)
    mem = tmp_path / "memory"
    expected_sha = _head_sha(repo)

    result = _run(str(repo), "feat: shipped auth", str(mem), project="myrepo")

    assert result["appended"] is True
    assert "path" in result

    log_path = Path(result["path"])
    lines = [l for l in log_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["commit"] == expected_sha
    assert record["summary"] == "feat: shipped auth"
    assert record["repo"] == "myrepo"
    assert "ts" in record
    assert "run_id" in record

    ledger = mem / "indexes" / "updates.jsonl"
    ledger_rows = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
    assert len(ledger_rows) == 1
    assert ledger_rows[0]["project"] == "myrepo"
    assert ledger_rows[0]["lane"] == "milestones"
    assert ledger_rows[0]["action"] == "append"
    assert ledger_rows[0]["source_commit"] == expected_sha


def test_second_new_summary_appends(tmp_path):
    """Second call with different summary appends a second line."""
    repo = _make_git_repo(tmp_path)
    mem = tmp_path / "memory"

    _run(str(repo), "feat: shipped auth", str(mem), project="myrepo")
    result2 = _run(str(repo), "feat: shipped dashboard", str(mem), project="myrepo")

    assert result2["appended"] is True
    log_path = _milestones_path(mem, "myrepo")
    lines = [l for l in log_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 2


def test_idempotent_same_commit_and_summary(tmp_path):
    """Second call with same commit+summary is a no-op (idempotency)."""
    repo = _make_git_repo(tmp_path)
    mem = tmp_path / "memory"

    _run(str(repo), "feat: shipped auth", str(mem), project="myrepo")
    result2 = _run(str(repo), "feat: shipped auth", str(mem), project="myrepo")

    assert result2["appended"] is False
    log_path = _milestones_path(mem, "myrepo")
    lines = [l for l in log_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1  # still just one line
    ledger = mem / "indexes" / "updates.jsonl"
    ledger_rows = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
    assert len(ledger_rows) == 1


def test_creates_project_dir_if_missing(tmp_path):
    """Creates projects/<slug>/ directory tree if it doesn't exist."""
    repo = _make_git_repo(tmp_path)
    mem = tmp_path / "memory"
    slug = "brand-new-project"

    assert not (mem / "projects" / slug).exists()

    result = _run(str(repo), "initial milestone", str(mem), project=slug)

    assert result["appended"] is True
    assert (mem / "projects" / slug / "milestones.jsonl").exists()


def test_non_git_workdir_fail_soft(tmp_path):
    """Non-git workdir returns appended=false with reason, exits 0."""
    non_git = tmp_path / "not-a-repo"
    non_git.mkdir()
    mem = tmp_path / "memory"

    result = _run(str(non_git), "some summary", str(mem), project="proj")

    assert result["appended"] is False
    assert "reason" in result


@pytest.mark.skipif(os.getuid() == 0, reason="root bypasses chmod")
def test_unwritable_root_fail_soft(tmp_path):
    """Unwritable memory-root returns appended=false, exits 0."""
    repo = _make_git_repo(tmp_path)
    mem = tmp_path / "locked-memory"
    mem.mkdir()
    mem.chmod(stat.S_IRUSR | stat.S_IXUSR)  # no write bit

    try:
        result = _run(str(repo), "some summary", str(mem), project="proj")
        assert result["appended"] is False
        assert "reason" in result
    finally:
        mem.chmod(stat.S_IRWXU)  # restore so tmp_path cleanup works
