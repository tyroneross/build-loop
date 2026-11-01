#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for merged_worktree_sweep.py. Every repo lives under tmp_path and every
git call strips inherited GIT_* variables (a hook's GIT_DIR once made a test
`git init` mark the real repository bare)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import merged_worktree_sweep as sweep_mod  # noqa: E402


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"})
    return env


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BUILD_LOOP_WORKTREE_RECEIPTS_DIR", str(tmp_path / "receipts"))
    stub = tmp_path / "lsof-stub"
    stub.write_text("#!/bin/sh\nprintf 'p1\\nfcwd\\nn/\\n'\n")
    stub.chmod(0o755)
    monkeypatch.setenv("BUILD_LOOP_SWEEP_LSOF", str(stub))
    return _env(tmp_path)


def git(env: dict[str, str], cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], env=env, capture_output=True, text=True, check=check)


def commit(env: dict[str, str], cwd: Path, name: str, body: str | None = None) -> None:
    (cwd / name).write_text(body if body is not None else name + "\n")
    git(env, cwd, "add", name)
    git(env, cwd, "commit", "-q", "-m", name)


@pytest.fixture()
def repo(tmp_path: Path, env: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(env, root, "init", "-q", "-b", "main")
    commit(env, root, "base.txt")
    return root


def add_worktree(env: dict[str, str], repo: Path, name: str, *, merge: bool, commits: int = 1) -> Path:
    path = repo.parent / "elsewhere" / name
    path.parent.mkdir(exist_ok=True)
    git(env, repo, "worktree", "add", "-q", "-b", name, str(path), "main")
    for i in range(commits):
        commit(env, path, f"{name}-{i}.txt")
    if merge:
        git(env, repo, "merge", "-q", "--ff-only", name)
    return path


def run(repo: Path, mode: str = "archive-only", **kw) -> dict:
    kw.setdefault("min_idle_minutes", 0)
    return sweep_mod.sweep(repo, mode=mode, **kw)


def paths(items: list[dict]) -> set[str]:
    return {os.path.realpath(i["path"]) for i in items}


def branch_exists(env: dict[str, str], repo: Path, branch: str) -> bool:
    return git(env, repo, "show-ref", "--verify", "-q", f"refs/heads/{branch}", check=False).returncode == 0


def test_classifies_merged_vs_unmerged_and_dry_run_touches_nothing(repo: Path, env: dict[str, str]) -> None:
    merged = add_worktree(env, repo, "merged", merge=True)
    unmerged = add_worktree(env, repo, "unmerged", merge=False)
    before = git(env, repo, "worktree", "list", "--porcelain").stdout

    r = run(repo, "dry-run")

    assert paths(r["ready_to_remove"]) == {os.path.realpath(merged)}
    assert paths(r["unmerged"]) == {os.path.realpath(unmerged)}
    assert git(env, repo, "worktree", "list", "--porcelain").stdout == before
    assert not (repo / ".build-loop").exists()
    assert not (repo.parent / "repo.worktrees").exists()


def test_primary_checkout_is_never_listed(repo: Path, env: dict[str, str]) -> None:
    add_worktree(env, repo, "merged", merge=True)
    r = run(repo, "dry-run")
    every = [i for v in r.values() if isinstance(v, list) and v and isinstance(v[0], dict) for i in v]
    assert os.path.realpath(repo) not in paths(every)
    assert r["primary"] == os.path.realpath(repo)


def test_locked_worktree_is_skipped(repo: Path, env: dict[str, str]) -> None:
    wt = add_worktree(env, repo, "locked", merge=True)
    git(env, repo, "worktree", "lock", "--reason", "in use", str(wt))

    r = run(repo, "act")

    assert paths(r["skipped_locked"]) == {os.path.realpath(wt)}
    assert r["skipped_locked"][0]["reason"] == "in use"
    assert wt.exists() and branch_exists(env, repo, "locked")


def test_busy_worktree_is_skipped(repo: Path, env: dict[str, str], tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    wt = add_worktree(env, repo, "busy", merge=True)
    (wt / "sub").mkdir()
    stub = tmp_path / "lsof-busy"
    stub.write_text(f"#!/bin/sh\nprintf 'p42\\nfcwd\\nn{wt / 'sub'}\\n'\n")
    stub.chmod(0o755)
    monkeypatch.setenv("BUILD_LOOP_SWEEP_LSOF", str(stub))

    r = run(repo, "act")

    assert paths(r["busy"]) == {os.path.realpath(wt)}
    assert wt.exists() and not r["removed"]


def test_missing_lsof_fails_closed(repo: Path, env: dict[str, str], tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    wt = add_worktree(env, repo, "nolsof", merge=True)
    monkeypatch.setenv("BUILD_LOOP_SWEEP_LSOF", str(tmp_path / "does-not-exist"))

    r = run(repo, "act")

    assert paths(r["unchecked"]) == {os.path.realpath(wt)}
    assert r["unchecked"][0]["reason"] == "lsof unavailable"
    assert wt.exists()


def test_fresh_worktree_without_commits_is_left_alone(repo: Path, env: dict[str, str]) -> None:
    wt = add_worktree(env, repo, "fresh", merge=False, commits=0)
    r = run(repo, "act")
    assert paths(r["fresh"]) == {os.path.realpath(wt)}
    assert wt.exists()


def test_recently_active_worktree_is_left_alone(repo: Path, env: dict[str, str]) -> None:
    wt = add_worktree(env, repo, "recent", merge=True)
    r = run(repo, "act", min_idle_minutes=60)
    assert paths(r["recently_active"]) == {os.path.realpath(wt)}
    assert wt.exists()


def test_patch_saved_and_verified_and_archive_only_keeps_worktree(repo: Path, env: dict[str, str]) -> None:
    wt = add_worktree(env, repo, "dirty", merge=True)
    (wt / "base.txt").write_text("staged edit\n")
    git(env, wt, "add", "base.txt")
    (wt / "dirty-0.txt").write_text("unstaged edit\n")

    r = run(repo, "archive-only")

    assert not r["errors"], r["errors"]
    [rec] = r["ready_to_remove"]
    patch = Path(rec["patch"])
    assert patch.parent == repo / ".build-loop" / "patches"
    text = patch.read_text()
    assert "staged edit" in text and "unstaged edit" in text
    assert git(env, wt, "apply", "--check", "--reverse", str(patch), check=False).returncode == 0
    assert Path(rec["archive_path"]).is_file() and Path(rec["receipt_path"]).is_file()
    assert wt.exists() and branch_exists(env, repo, "dirty")


def test_clean_worktree_writes_no_patch(repo: Path, env: dict[str, str]) -> None:
    add_worktree(env, repo, "clean", merge=True)
    r = run(repo, "archive-only")
    assert r["ready_to_remove"][0]["patch"] is None
    assert not (repo / ".build-loop" / "patches").exists()


def test_records_copied_without_overwriting(repo: Path, env: dict[str, str]) -> None:
    wt = add_worktree(env, repo, "records", merge=True)
    (wt / ".build-loop" / "proposals").mkdir(parents=True)
    (wt / ".build-loop" / "proposals" / "new.md").write_text("from worktree\n")
    (wt / ".build-loop" / "backlog" / "items").mkdir(parents=True)
    (wt / ".build-loop" / "backlog" / "items" / "X.md").write_text("worktree copy\n")
    (wt / ".build-loop" / "scratch.txt").write_text("not a record\n")
    (repo / ".build-loop" / "backlog" / "items").mkdir(parents=True)
    (repo / ".build-loop" / "backlog" / "items" / "X.md").write_text("primary copy\n")

    r = run(repo, "archive-only")

    [rec] = r["ready_to_remove"]
    assert rec["records_copied"] == ["proposals/new.md"]
    assert rec["records_kept_existing"] == ["backlog/items/X.md"]
    assert (repo / ".build-loop" / "proposals" / "new.md").read_text() == "from worktree\n"
    assert (repo / ".build-loop" / "backlog" / "items" / "X.md").read_text() == "primary copy\n"
    assert not (repo / ".build-loop" / "scratch.txt").exists()


def test_act_removes_merged_and_deletes_branch_leaving_unmerged(repo: Path, env: dict[str, str]) -> None:
    merged = add_worktree(env, repo, "merged", merge=True)
    unmerged = add_worktree(env, repo, "unmerged", merge=False)
    (merged / "base.txt").write_text("dirty before removal\n")

    r = run(repo, "act")

    assert not r["errors"], r["errors"]
    [rec] = r["removed"]
    assert rec["removed"] and rec["branch_deleted"]
    assert not merged.exists() and not branch_exists(env, repo, "merged")
    assert Path(rec["archive_path"]).is_file() and Path(rec["patch"]).is_file()
    assert unmerged.exists() and branch_exists(env, repo, "unmerged")
    assert paths(r["unmerged"]) == {os.path.realpath(unmerged)}
    assert str(merged) not in git(env, repo, "worktree", "list", "--porcelain").stdout
    assert isinstance(r["freed_bytes"], int)


def test_base_override_and_missing_base(repo: Path, env: dict[str, str]) -> None:
    git(env, repo, "branch", "release")
    wt = add_worktree(env, repo, "feat", merge=True)
    # merged into main but not into `release` (which predates the merge)
    assert paths(run(repo, "dry-run", base="release")["unmerged"]) == {os.path.realpath(wt)}
    r = run(repo, "dry-run", base="nope")
    assert r["errors"] and not r["ready_to_remove"]


def test_cli_json_and_cap(repo: Path, env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    add_worktree(env, repo, "a", merge=True)
    add_worktree(env, repo, "b", merge=True)
    rc = sweep_mod.main(["--repo", str(repo), "--dry-run", "--json", "--max-worktrees", "1",
                         "--min-idle-minutes", "0"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["inspected"] == 1 and len(out["unchecked"]) == 1
    assert out["unchecked"][0]["reason"] == "inspection cap reached"


def test_parse_porcelain_flags() -> None:
    text = ("worktree /r\nHEAD aaa\nbranch refs/heads/main\n\n"
            "worktree /w\nHEAD bbb\ndetached\nlocked why\n\n"
            "worktree /p\nHEAD ccc\nbranch refs/heads/x\nprunable gitdir file points to non-existent location\n\n"
            "worktree /b\nbare\n")
    e = sweep_mod.parse_porcelain(text)
    assert [x["path"] for x in e] == ["/r", "/w", "/p", "/b"]
    assert e[0]["branch"] == "main" and e[1]["locked"] == "why" and e[1]["detached"]
    assert e[2]["prunable"].startswith("gitdir") and e[3]["bare"]
