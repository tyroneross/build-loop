"""Tests for scripts/check_private_slugs.py — the private-slug guard.

Covers the security-review remediations:
  - SEC-005: word-boundary class catches embedded slugs (`_secretproj`);
             SELF exemption is worktree-safe (resolved-path compare).
  - SEC-006: an unreadable tracked file fails CLOSED in --all/CI mode.
  - SEC-011: denylist is read from a runtime .private-slugs file; a
             missing or empty config fails closed (exit 2).
  - SEC-008-adjacent: missing repo root exits 2 (no silent skip).
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "check_private_slugs.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_private_slugs", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


cps = _load_module()


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A git repo with a .private-slugs denylist already present."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(["init", "-q"], r)
    _git(["config", "user.email", "t@example.com"], r)
    _git(["config", "user.name", "Test"], r)
    (r / ".private-slugs").write_text("secretproj\nhushapp\nexample\\.com\n")
    return r


def _run(repo: Path, *argv: str):
    """Run check_private_slugs in `repo` as a subprocess; return CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *argv],
        cwd=str(repo), capture_output=True, text=True,
    )


# --- SEC-011: runtime denylist, fail closed on missing/empty config ----

def test_missing_denylist_fails_closed(tmp_path: Path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(["init", "-q"], r)
    res = _run(r, "--all")
    assert res.returncode == 2
    assert ".private-slugs" in res.stderr


def test_empty_denylist_fails_closed(repo: Path):
    (repo / ".private-slugs").write_text("# only comments\n\n")
    res = _run(repo, "--all")
    assert res.returncode == 2
    assert "empty" in res.stderr.lower()


def test_denylist_ignores_comments_and_blanks(repo: Path):
    (repo / ".private-slugs").write_text("# header\n\nsecretproj\n")
    (repo / "doc.md").write_text("mentions secretproj here\n")
    _git(["add", "-A"], repo)
    res = _run(repo, "--all")
    assert res.returncode == 1


# --- SEC-005: word-boundary class -------------------------------------

def test_embedded_slug_with_leading_underscore_is_caught(repo: Path):
    # `_secretproj` must NOT slip past the boundary class.
    (repo / "fixture.py").write_text("VAR = '_secretproj'\n")
    _git(["add", "-A"], repo)
    res = _run(repo, "--all")
    assert res.returncode == 1
    assert "secretproj" in res.stdout + res.stderr


def test_embedded_slug_with_trailing_underscore_is_caught(repo: Path):
    (repo / "fixture.py").write_text("name = 'secretproj_'\n")
    _git(["add", "-A"], repo)
    res = _run(repo, "--all")
    assert res.returncode == 1


def test_generic_word_does_not_false_positive(repo: Path):
    (repo / ".private-slugs").write_text("example-app\n")
    (repo / "doc.md").write_text("see the examples directory\n")
    _git(["add", "-A"], repo)
    res = _run(repo, "--all")
    assert res.returncode == 0


# --- SEC-005: worktree-safe SELF exemption ----------------------------

def test_self_exemption_in_linked_worktree(repo: Path, tmp_path: Path):
    """The guard scanning a copy of itself must not block — even when
    invoked from a linked worktree where a relative-path compare fails.
    """
    # Place a copy of the checker into the repo as a tracked file.
    tracked_checker = repo / "scripts" / "check_private_slugs.py"
    tracked_checker.parent.mkdir(parents=True, exist_ok=True)
    tracked_checker.write_text(_SCRIPT.read_text())
    (repo / ".private-slugs").write_text("secretproj\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "init", "--no-verify"], repo)

    # Create a linked worktree and run the guard from inside it.
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-q", str(wt), "HEAD"], repo)
    res = subprocess.run(
        [sys.executable, str(wt / "scripts" / "check_private_slugs.py"), "--all"],
        cwd=str(wt), capture_output=True, text=True,
    )
    # The denylist literals inside the checker copy must be exempted as
    # SELF; scanning must not block on the script itself.
    assert res.returncode == 0, res.stderr


# --- SEC-006: unreadable tracked file fails CLOSED in CI mode ---------

def test_unreadable_tracked_file_fails_closed_in_all_mode(repo: Path,
                                                          monkeypatch):
    """In --all mode, a tracked file the reader cannot read must cause a
    non-zero exit, never a silent pass.
    """
    (repo / "ok.md").write_text("clean content\n")
    (repo / "bad.bin").write_text("clean too\n")
    _git(["add", "-A"], repo)

    real_disk = cps._disk_content

    def flaky_reader(root, path):
        if path == "bad.bin":
            return None  # simulate an unreadable tracked file
        return real_disk(root, path)

    monkeypatch.setattr(cps, "_disk_content", flaky_reader)
    monkeypatch.chdir(repo)
    rc = cps.main(["--all"])
    assert rc == 1


def test_readable_clean_tree_passes(repo: Path, monkeypatch):
    (repo / "ok.md").write_text("perfectly clean\n")
    _git(["add", "-A"], repo)
    monkeypatch.chdir(repo)
    assert cps.main(["--all"]) == 0


# --- SEC-008-adjacent: missing repo root exits 2 ----------------------

def test_not_a_git_repo_exits_2(tmp_path: Path):
    loose = tmp_path / "loose"
    loose.mkdir()
    res = _run(loose, "--all")
    assert res.returncode == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
