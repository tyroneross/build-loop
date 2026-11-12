# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/rally_point/install_git_hook.py — idempotent installer.

  - only installs inside a git repo
  - idempotent (re-run = no dup)
  - chains an existing post-commit (never clobbers unrelated content)
  - marker-guarded
  - installs public-repo hygiene pre-commit guards alongside post-commit
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
import subprocess
import sys
import time
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
    # The private-slug guard installs only where a denylist makes it runnable
    # (see PrivateSlugGuardScopeTests). This test asserts the guard wiring, so
    # it opts the fixture in rather than asserting the old install-everywhere
    # behaviour — that behaviour was the defect: without a denylist
    # check_private_slugs.py exits 2 and blocks every commit.
    (repo / ".private-slugs").write_text("some-private-slug\n")
    assert igh.install(repo) is True
    hook = repo / ".git" / "hooks" / "pre-commit"
    assert hook.exists() and igh.PRE_MARKER in hook.read_text()
    assert os.access(hook, os.X_OK)
    assert (repo / ".git" / "hooks" / ".private-slug-check.py").exists()
    assert (repo / ".git" / "hooks" / ".runtime-memory-tracking-check.py").exists()
    assert ".runtime-memory-tracking-check.py" in hook.read_text()
    assert "scripts/audit_before_commit.py" in hook.read_text()


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


def test_pre_commit_reinstall_replaces_stale_segment(repo: Path):
    """SEC-004: a re-install with our marker already present must REPLACE
    the guard segment, not early-return. A stale segment (e.g. a pinned
    path that no longer resolves, or an old template) would silently
    disable the guard.
    """
    hook = repo / ".git" / "hooks" / "pre-commit"
    # Seed a STALE segment carrying our markers but obsolete body text.
    stale = (
        "#!/bin/sh\n"
        f"{igh.PRE_MARKER}\n"
        "STALE_GUARD=/old/moved/plugin/path/.private-slug-check.py\n"
        "echo stale-guard-segment\n"
        f"{igh.PRE_MARKER_END}\n"
        "exit 0\n"
    )
    hook.write_text(stale)
    hook.chmod(0o755)
    assert igh.install(repo) is True
    body = hook.read_text()
    assert "stale-guard-segment" not in body  # stale segment gone
    assert "STALE_GUARD=/old/moved/plugin" not in body
    assert igh.PRE_MARKER in body
    assert body.count(igh.PRE_MARKER) == 1  # exactly one segment
    assert "RALLY_POINT_TOPLEVEL" in body  # current template wiring
    assert ".runtime-memory-tracking-check.py" in body


def test_linked_worktree_installs_into_effective_common_hooks(tmp_path: Path):
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", "-b", "main", str(main)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=main, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=main, check=True)
    (main / "README.md").write_text("test\n")
    subprocess.run(["git", "add", "README.md"], cwd=main, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=main, check=True, capture_output=True)
    subprocess.run(["git", "worktree", "add", "-b", "linked", str(linked)], cwd=main, check=True, capture_output=True)

    assert igh.install(linked) is True

    effective = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks/pre-commit"],
        cwd=linked,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    hook = Path(effective)
    assert hook.exists()
    assert igh.PRE_MARKER in hook.read_text()


@pytest.mark.parametrize("layout", ["normal", "linked", "custom", "relative-custom", "linked-custom"])
def test_installed_hooks_execute_helpers_from_effective_directory(repo: Path, layout: str):
    """Exercise generated pre/post hooks, not just where installation writes them."""
    workdir = repo
    if layout.startswith("linked"):
        _git(["config", "user.email", "test@example.com"], repo)
        _git(["config", "user.name", "Test"], repo)
        _git(["commit", "--allow-empty", "-m", "init"], repo)
        workdir = repo.parent / "linked"
        _git(["worktree", "add", "-b", "linked", str(workdir)], repo)
    if layout == "relative-custom":
        (workdir / ".gitignore").write_text(".local-hooks/\n")
        _git(["config", "core.hooksPath", ".local-hooks"], workdir)
    elif layout.endswith("custom"):
        _git(["config", "core.hooksPath", str(repo.parent / "custom hooks")], workdir)

    # Only the committing checkout opts in, including the linked-worktree case.
    (workdir / ".private-slugs").write_text("some-private-slug\n")
    assert igh.install(workdir)
    hooks = Path(subprocess.check_output(
        ["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
        cwd=workdir, text=True,
    ).strip())
    private = hooks / ".private-slug-check.py"
    runtime = hooks / ".runtime-memory-tracking-check.py"
    capture = hooks / ".rally-point-capture.py"
    assert private.is_file() and runtime.is_file() and capture.is_file()
    receipt = repo.parent / "hook-calls"

    def stub(path: Path, label: str, code: int = 0):
        path.write_text(
            f"from pathlib import Path\n"
            f"with Path({str(receipt)!r}).open('a') as f: f.write({label + chr(10)!r})\n"
            f"raise SystemExit({code})\n"
        )

    stub(private, "private-denied", 1)
    stub(runtime, "runtime-denied", 1)
    pre = subprocess.run(["sh", str(hooks / "pre-commit")], cwd=workdir, capture_output=True)
    assert pre.returncode == 1
    assert receipt.read_text().splitlines() == ["private-denied"]

    stub(private, "private-allowed")
    pre = subprocess.run(["sh", str(hooks / "pre-commit")], cwd=workdir, capture_output=True)
    assert pre.returncode == 1
    assert receipt.read_text().splitlines() == ["private-denied", "private-allowed", "runtime-denied"]

    stub(capture, "captured")
    post = subprocess.run(["sh", str(hooks / "post-commit")], cwd=workdir, capture_output=True)
    assert post.returncode == 0
    deadline = time.monotonic() + 10
    while "captured\n" not in receipt.read_text() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert receipt.read_text().splitlines()[-1] == "captured"


def test_post_commit_reinstall_replaces_stale_segment(repo: Path):
    """SEC-004 (post-commit symmetry): re-install replaces a stale
    rally-point segment instead of leaving an obsolete template.
    """
    hook = repo / ".git" / "hooks" / "post-commit"
    stale = (
        "#!/bin/sh\n"
        f"{igh.MARKER}\n"
        "echo stale-capture-segment\n"
        f"{igh.MARKER_END}\n"
        "exit 0\n"
    )
    hook.write_text(stale)
    hook.chmod(0o755)
    assert igh.install(repo) is True
    body = hook.read_text()
    assert "stale-capture-segment" not in body
    assert body.count(igh.MARKER) == 1
    assert "RALLY_POINT_TOPLEVEL" in body


def test_capture_routes_commit_records_through_post_bridge(repo: Path):
    """The capture hook must use the Rust-aware post bridge, not flat JSONL."""
    assert igh.install(repo) is True
    capture = repo / ".git" / "hooks" / ".rally-point-capture.py"
    body = capture.read_text()
    assert "from post import post as _post" in body
    assert "append_change" not in body
    assert "make_record" not in body
    assert "bump_revision" not in body


class PrivateSlugGuardScopeTests(unittest.TestCase):
    """The private-slug guard must install ONLY where it can run.

    check_private_slugs.py exits 2 without a .private-slugs denylist, and
    rejects an empty one, so a repo without a denylist has no passive
    configuration — installing the guard there blocks every commit by every
    agent. Observed 2026-08-20 in a private consumer repo.
    """

    def _repo(self, *, denylist: bool, example: bool = False) -> Path:
        d = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        if denylist:
            (d / ".private-slugs").write_text("some-private-slug\n")
        if example:
            (d / ".private-slugs.example").write_text("# template\n")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def _guard(self, repo: Path) -> Path:
        return repo / ".git" / "hooks" / ".private-slug-check.py"

    def test_guard_installed_when_denylist_present(self) -> None:
        """The working path. If this ever fails the guard has been disabled
        everywhere, which is the opposite defect."""
        repo = self._repo(denylist=True)
        igh.install(repo)
        self.assertTrue(self._guard(repo).exists(),
                        "guard must install where a denylist makes it runnable")

    def test_guard_installed_when_only_the_example_is_present(self) -> None:
        """Shipping the template is opting in; the operator just has to fill it."""
        repo = self._repo(denylist=False, example=True)
        igh.install(repo)
        self.assertTrue(self._guard(repo).exists())

    def test_guard_NOT_installed_without_a_denylist(self) -> None:
        """The defect this closes."""
        repo = self._repo(denylist=False)
        igh.install(repo)
        self.assertFalse(self._guard(repo).exists(),
                         "guard installed into a repo where it can only exit 2")

    def test_reinstall_removes_a_guard_left_by_an_earlier_bad_install(self) -> None:
        """Re-running the installer must self-heal, not re-create the blocker.
        The observed repo had the guard re-appear after being disabled by hand."""
        repo = self._repo(denylist=False)
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        stale = self._guard(repo)
        stale.write_text("#!/usr/bin/env python3\nraise SystemExit(2)\n")
        stale.chmod(0o755)
        igh.install(repo)
        self.assertFalse(stale.exists(), "stale blocking guard survived a re-install")

    def test_other_guards_still_install_without_a_denylist(self) -> None:
        """Scope check: only the slug guard is gated. The runtime-memory guard
        needs no config and must keep working."""
        repo = self._repo(denylist=False)
        igh.install(repo)
        hook = (repo / ".git" / "hooks" / "pre-commit")
        self.assertTrue(hook.exists())
        self.assertIn("RUNTIME_MEMORY_GUARD", hook.read_text())


class PrivateSlugGuardResilienceTest(unittest.TestCase):
    """The guard must survive a plugin upgrade and must fail CLOSED.

    A pinned versioned cache path bricked commits in three repos when the plugin
    bumped 0.39.4 -> 0.40.0: the directory was replaced, the hook raised
    FileNotFoundError, and the commit was refused with a traceback that never
    mentioned private slugs.
    """

    def _guard_src(self):
        import install_git_hook as igh
        return igh._PRE_GUARD_SRC.format(checker="/nonexistent/pinned/check_private_slugs.py")

    def test_guard_does_not_rely_on_the_pinned_path_alone(self):
        src = self._guard_src()
        self.assertIn("_candidates", src,
                      "guard must resolve dynamically, not trust one pinned path")
        self.assertIn("plugins", src, "guard must be able to search the plugin caches")

    def test_guard_fails_closed_when_nothing_resolves(self):
        """Exit 2, not 0. An advisory skip would silently drop a security guard."""
        import subprocess, sys, tempfile, os
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            g = Path(d) / "guard.py"
            g.write_text(self._guard_src())
            env = dict(os.environ)
            env["HOME"] = d
            env["GIT_TOPLEVEL"] = d
            env["BUILD_LOOP_SLUG_CHECKER"] = "/nonexistent/x.py"
            r = subprocess.run([sys.executable, str(g)], capture_output=True,
                               text=True, env=env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Refusing the commit", r.stderr)

    def test_guard_runs_a_resolvable_checker(self):
        import subprocess, sys, tempfile, os
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            checker = Path(d) / "scripts" / "check_private_slugs.py"
            checker.parent.mkdir(parents=True)
            checker.write_text("import sys\nsys.exit(0)\n")
            g = Path(d) / "guard.py"
            g.write_text(self._guard_src())
            env = dict(os.environ)
            env["HOME"] = d
            env["BUILD_LOOP_SLUG_CHECKER"] = str(checker)
            r = subprocess.run([sys.executable, str(g)], capture_output=True,
                               text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)


def _cfg(repo: Path) -> None:
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "t"], repo)


def _porcelain(repo: Path) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=str(repo),
                          capture_output=True, text=True, check=True).stdout


def test_refuses_tracked_hookspath(repo: Path):
    _cfg(repo)
    d = repo / "scripts" / "git-hooks"
    d.mkdir(parents=True)
    pc = d / "pre-commit"
    pc.write_bytes(b"#!/bin/sh\necho mine\n")
    _git(["add", "."], repo)
    _git(["commit", "-qm", "hooks"], repo)
    _git(["config", "core.hooksPath", "scripts/git-hooks"], repo)
    before = pc.read_bytes()
    assert igh.install(repo) is False
    assert pc.read_bytes() == before
    assert _porcelain(repo) == ""
    assert not (d / "post-commit").exists()
    assert not (d / ".rally-point-capture.py").exists()


def test_refuses_untracked_visible_hookspath(repo: Path):
    d = repo / "tools" / "hooks"
    d.mkdir(parents=True)
    _git(["config", "core.hooksPath", "tools/hooks"], repo)
    assert igh.install(repo) is False
    assert list(d.iterdir()) == []


def test_allows_gitignored_hookspath(repo: Path):
    _cfg(repo)
    (repo / ".gitignore").write_text(".githooks-local/\n")
    _git(["add", ".gitignore"], repo)
    _git(["commit", "-qm", "ignore"], repo)
    _git(["config", "core.hooksPath", ".githooks-local"], repo)
    assert igh.install(repo) is True
    assert (repo / ".githooks-local" / "post-commit").exists()


def test_allows_hookspath_outside_worktree(repo: Path, tmp_path: Path):
    ext = tmp_path / "ext-hooks"
    _git(["config", "core.hooksPath", str(ext)], repo)
    assert igh.install(repo) is True
    assert (ext / "post-commit").exists()
