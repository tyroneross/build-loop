# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
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
    # Denylist entries are LITERAL slugs (no regex escaping by the
    # author) — the guard escapes metacharacters itself.
    (r / ".private-slugs").write_text("secretproj\nhushapp\nexample.com\n")
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
    # The hit is reported but the slug itself must never reach (public) logs.
    assert "secretproj" not in res.stdout + res.stderr
    assert "redacted slug sha256:" in res.stderr


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


# --- SEC-011 regression: denylist entries are LITERALS, not regex ------
# Before this fix, _compile_pattern joined denylist entries straight into
# a regex with no re.escape. A denylist literal of the shape
# `<word>.<tld>` had its dot turned into a `.` wildcard and matched the
# hyphenated public marketplace name (`<word>-<tld>-toolkit`) — CI run
# 26304492355 flagged ~30 legitimate PUBLIC marketplace references.
#
# The dotted slug is assembled at runtime, never written as a literal in
# this source: this file is itself scanned by the repo's own guard, and a
# literal private slug here would self-trip CI (exactly the bug below).
_DOTTED_SLUG = ".".join(["examplecorp", "example"])      # word.tld shape
_HYPHEN_NAME = "-".join(["examplecorp", "example", "toolkit"])  # public name


def test_literal_dot_matches_only_a_literal_dot(repo: Path):
    """A denylist entry of the form `word.tld` must match that literal
    string and MUST NOT match the public marketplace name spelled with a
    hyphen or a space.
    """
    (repo / ".private-slugs").write_text(_DOTTED_SLUG + "\n")
    (repo / "leak.md").write_text(f"private ref to {_DOTTED_SLUG} here\n")
    _git(["add", "-A"], repo)
    res = _run(repo, "--all")
    assert res.returncode == 1, res.stderr
    # Detected, but the slug must not leak into output (only a redacted hash).
    assert _DOTTED_SLUG not in res.stdout + res.stderr
    assert "redacted slug sha256:" in res.stderr


def test_literal_dot_does_not_match_hyphen_or_space(repo: Path):
    """The `.` in a `word.tld` slug must not behave as a regex wildcard:
    the hyphenated and space-separated public marketplace names must NOT
    trip the guard.
    """
    word, tld = _DOTTED_SLUG.split(".")
    (repo / ".private-slugs").write_text(_DOTTED_SLUG + "\n")
    (repo / "public.md").write_text(
        f"Install build-loop@{_HYPHEN_NAME} from the\n"
        f"{_HYPHEN_NAME} marketplace; see {word} {tld} docs.\n"
    )
    _git(["add", "-A"], repo)
    res = _run(repo, "--all")
    assert res.returncode == 0, res.stdout + res.stderr


def test_regex_metacharacters_in_slug_are_escaped(repo: Path):
    """Any regex metacharacter in a denylist entry is treated literally —
    the author writes a plain slug and never needs to know regex.
    """
    # `+` would be a quantifier if unescaped; `()` a group; `*` a star.
    (repo / ".private-slugs").write_text("a+b(c)*\n")
    (repo / "leak.md").write_text("token a+b(c)* appears here\n")
    _git(["add", "-A"], repo)
    res = _run(repo, "--all")
    assert res.returncode == 1, res.stderr


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


# --- Binary-file safety: non-UTF-8 staged content must not crash ------
#
# Regression for: `git show :<path>` with `text=True` raised
# UnicodeDecodeError on binary blobs (e.g. .map, images, .db),
# aborting the commit with no recourse except --no-verify.

def test_staged_binary_file_does_not_raise(repo: Path):
    """Staging a file containing non-UTF-8 bytes must not raise and must
    return a clean (no-leak) result — binaries cannot contain text slugs.
    """
    # Write raw non-UTF-8 bytes that would crash str.decode("utf-8").
    binary_path = repo / "asset.bin"
    binary_path.write_bytes(b"\xa2\xff\x00binary\xfe\xed")
    _git(["add", "asset.bin"], repo)

    # _staged_content must return None (binary skip), never raise.
    result = cps._staged_content(repo, "asset.bin")
    assert result is None, f"expected None for binary blob, got {result!r}"


def test_staged_binary_file_commit_does_not_block(repo: Path):
    """End-to-end: a commit that stages only a binary file must exit 0
    (no slug found, no crash).  This is the exact failure path from the
    bug report.
    """
    binary_path = repo / "asset2.bin"
    binary_path.write_bytes(b"\xa2\xff\x00binary\xfe\xed")
    _git(["add", "asset2.bin"], repo)

    # Run as subprocess (same as the pre-commit hook does).
    res = _run(repo)   # default: scan staged files only
    assert res.returncode == 0, (
        f"checker crashed or blocked on binary file:\n{res.stderr}"
    )


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


# --- CI flow: PRIVATE_SLUGS secret -> .private-slugs -> --all scan ----

# Sentinel tokens are assembled at runtime, never written as literals in
# this test source — this file is itself scanned by the repo's own guard,
# and a literal sentinel here would self-trip it.
_SENTINELS = ["-".join(["your", "private", "app", w]) for w in ("name", "ios", "web")]


def test_ci_flow_sentinel_denylist_passes(repo: Path):
    """Mirrors the workflow: a .private-slugs holding only obvious
    sentinel tokens (the kind shipped in .private-slugs.example) finds
    nothing in a clean tree and exits 0.
    """
    (repo / ".private-slugs").write_text("\n".join(_SENTINELS) + "\n")
    (repo / "doc.md").write_text("ordinary content, no private slugs\n")
    _git(["add", "-A"], repo)
    res = _run(repo, "--all")
    assert res.returncode == 0, res.stderr


def test_ci_flow_real_slug_in_tracked_file_fails(repo: Path):
    """A real private slug from the secret, planted in a tracked file,
    must fail the scan (exit 1) — this is the enforcement path.
    """
    slug = "-".join(["acme", "secret", "project"])
    (repo / ".private-slugs").write_text(slug + "\n")
    (repo / "leaked.md").write_text(f"references {slug} here\n")
    _git(["add", "-A"], repo)
    res = _run(repo, "--all")
    assert res.returncode == 1
    # Enforcement reports the hit (path + redacted hash) without leaking the slug.
    assert slug not in res.stdout + res.stderr
    assert "redacted slug sha256:" in res.stderr


def test_example_file_is_exempt_from_scan(repo: Path):
    """`.private-slugs.example` is a tracked format template that
    necessarily contains denylist-vocabulary tokens. The guard must skip
    it by basename, exactly as it skips `.private-slugs` and itself —
    otherwise the example file self-trips the scan.
    """
    slug = "-".join(["acme", "secret", "project"])
    (repo / ".private-slugs").write_text(slug + "\n")
    # The example file contains the very token on the denylist.
    (repo / ".private-slugs.example").write_text(f"# template\n{slug}\n")
    _git(["add", "-A"], repo)
    res = _run(repo, "--all")
    assert res.returncode == 0, res.stderr


# --- SEC-008-adjacent: missing repo root exits 2 ----------------------

def test_not_a_git_repo_exits_2(tmp_path: Path):
    loose = tmp_path / "loose"
    loose.mkdir()
    res = _run(loose, "--all")
    assert res.returncode == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
