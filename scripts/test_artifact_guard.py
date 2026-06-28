# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/artifact_guard.py — the checked-in-artifact drift guard.

Covers the named failure it earns its place against (artifacts silently drift
because regen is a manual step caught only by a late CI gate):

  - --staged regenerates + re-stages a drifted artifact whose watched source
    is staged, so drift cannot be committed (exit 0).
  - --staged is scoped: an artifact whose watched paths are NOT staged is
    untouched (unrelated commits don't pay).
  - --check is a read-only freshness gate (stale -> exit 1, names the regen cmd).
  - an artifact whose generator script is absent self-skips (no false drift).
  - hook install coexists with a later-APPENDED segment (the rally-point
    private-slug guard) without turning it into dead code, in BOTH install
    orders, and is idempotent.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "artifact_guard.py"
_REPO = _SCRIPT.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("artifact_guard", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ag = _load()


# A self-contained generator: out.txt = sha256(src.txt). --check compares.
_GEN = (
    "import sys, hashlib, pathlib\n"
    "root = pathlib.Path(__file__).resolve().parent\n"
    "src = (root/'src.txt').read_text() if (root/'src.txt').exists() else ''\n"
    "want = hashlib.sha256(src.encode()).hexdigest()\n"
    "out = root/'out.txt'\n"
    "if '--check' in sys.argv:\n"
    "    have = out.read_text() if out.exists() else ''\n"
    "    sys.exit(0 if have == want else 1)\n"
    "out.write_text(want)\n"
)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _synthetic_repo(tmp_path: Path):
    repo = tmp_path
    _git_init(repo)
    (repo / "gen.py").write_text(_GEN)
    (repo / "src.txt").write_text("a")
    subprocess.run(["python3", "gen.py"], cwd=str(repo), check=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    return repo


def _git_init(repo: Path):
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "t")


def _synthetic_artifact():
    return ag.Artifact(
        name="synthetic", watch=("src.txt",),
        check_argv=("gen.py", "--check"), regen_argv=("gen.py",),
        outputs=("out.txt",), why="test fixture",
    )


@pytest.fixture
def synth(tmp_path, monkeypatch):
    repo = _synthetic_repo(tmp_path)
    monkeypatch.setattr(ag, "ARTIFACTS", (_synthetic_artifact(),))
    return repo


# --- watch matching -------------------------------------------------------


def test_matches_dir_prefix_and_exact():
    art = ag.Artifact("x", ("skills/", "AGENTS.md"), ("c",), ("r",), ("o",))
    assert ag._matches(art, ["skills/build-loop/SKILL.md"])
    assert ag._matches(art, ["AGENTS.md"])
    assert not ag._matches(art, ["README.md"])
    assert ag._matches(art, ["skills"])  # the dir itself


def test_registry_has_both_real_artifacts():
    names = {a.name for a in ag.ARTIFACTS}
    assert {"architecture-diagram", "codex-plugin-artifact"} == names


# --- staged regen + restage ----------------------------------------------


def test_staged_regenerates_and_restages_on_drift(synth):
    repo = synth
    (repo / "src.txt").write_text("b")          # source changed -> out stale
    _git(repo, "add", "src.txt")
    assert ag.mode_staged(repo) == 0
    # out.txt regenerated to sha(b) AND staged in the same commit.
    assert (repo / "out.txt").read_text() == hashlib.sha256(b"b").hexdigest()
    staged = subprocess.run(["git", "-C", str(repo), "diff", "--cached",
                             "--name-only"], capture_output=True, text=True).stdout
    assert "out.txt" in staged


def test_staged_is_scoped_to_watched_paths(synth):
    repo = synth
    (repo / "src.txt").write_text("b")          # drift exists...
    (repo / "unrelated.txt").write_text("z")
    _git(repo, "add", "unrelated.txt")          # ...but only unrelated staged
    assert ag.mode_staged(repo) == 0
    staged = subprocess.run(["git", "-C", str(repo), "diff", "--cached",
                             "--name-only"], capture_output=True, text=True).stdout
    assert "out.txt" not in staged             # guard left the artifact alone


def test_staged_advisory_warns_without_regen(synth, monkeypatch):
    repo = synth
    monkeypatch.setenv("BL_ARTIFACT_ADVISORY", "1")
    (repo / "src.txt").write_text("b")
    _git(repo, "add", "src.txt")
    assert ag.mode_staged(repo) == 0
    # advisory: out.txt NOT regenerated (still sha(a)).
    assert (repo / "out.txt").read_text() == hashlib.sha256(b"a").hexdigest()


# --- read-only check ------------------------------------------------------


def test_check_detects_stale(synth):
    repo = synth
    (repo / "src.txt").write_text("b")          # out.txt now stale on disk
    assert ag.mode_check(repo, as_json=False) == 1


def test_check_fresh(synth):
    assert ag.mode_check(synth, as_json=False) == 0


def test_absent_generator_self_skips(tmp_path, monkeypatch):
    _git_init(tmp_path)
    monkeypatch.setattr(ag, "ARTIFACTS", (ag.Artifact(
        "ghost", ("src.txt",), ("nope.py", "--check"), ("nope.py",), ("o",)),))
    # No nope.py present -> check is a no-op, never reports false drift.
    assert ag.mode_check(tmp_path, as_json=False) == 0
    (tmp_path / "src.txt").write_text("x")
    _git(tmp_path, "add", "src.txt")
    assert ag.mode_staged(tmp_path) == 0


# --- hook installer: coexistence + idempotency ----------------------------


_RALLY_SEG = ("# --- BEGIN private-slug-guard pre-commit ---\n"
              "echo SLUG_RAN\n"
              "# --- END private-slug-guard pre-commit ---\n")


def _hook_path(repo: Path) -> Path:
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-path",
                          "hooks"], capture_output=True, text=True).stdout.strip()
    p = Path(out)
    return (p if p.is_absolute() else repo / p) / "pre-commit"


def _install_source(repo: Path):
    # The installer reads the marked segment from <repo>/hooks/git/pre-commit.
    (repo / "hooks" / "git").mkdir(parents=True)
    (repo / "hooks" / "git" / "pre-commit").write_text(
        (_REPO / "hooks" / "git" / "pre-commit").read_text())


def test_install_fresh_has_no_trailing_exit0_regression(tmp_path):
    """Fresh install must NOT emit a trailing ``exit 0`` — that would turn a
    later-appended rally segment into dead code (the order-2 regression)."""
    _git_init(tmp_path)
    _install_source(tmp_path)
    res = ag.install_hook(tmp_path)
    assert res["installed"] and res["action"] == "created"
    body = _hook_path(tmp_path).read_text()
    assert ag._MARKER in body
    assert "exit 0" not in body


@pytest.mark.parametrize("rally_first", [True, False])
def test_install_coexists_with_rally_segment_both_orders(tmp_path, rally_first):
    _git_init(tmp_path)
    _install_source(tmp_path)
    hook = _hook_path(tmp_path)
    if rally_first:
        hook.write_text("#!/bin/sh\n" + _RALLY_SEG + "exit 0\n")
        hook.chmod(0o755)
        ag.install_hook(tmp_path)
    else:
        ag.install_hook(tmp_path)
        with hook.open("a") as fh:
            fh.write("\n" + _RALLY_SEG)
    body = hook.read_text()
    assert ag._MARKER in body and "private-slug-guard" in body
    # The rally segment must be reachable: it must not sit after a line that is
    # exactly ``exit 0``.
    lines = body.splitlines()
    slug_at = next(i for i, ln in enumerate(lines) if "BEGIN private-slug" in ln)
    assert not any(ln.strip() == "exit 0" for ln in lines[:slug_at])
    # And it actually executes.
    run = subprocess.run([str(hook)], cwd=str(tmp_path),
                         capture_output=True, text=True)
    assert "SLUG_RAN" in run.stdout


def test_install_is_idempotent(tmp_path):
    _git_init(tmp_path)
    _install_source(tmp_path)
    ag.install_hook(tmp_path)
    ag.install_hook(tmp_path)
    body = _hook_path(tmp_path).read_text()
    assert body.count(ag._MARKER) == 1


def test_uninstall_removes_only_our_segment(tmp_path):
    _git_init(tmp_path)
    _install_source(tmp_path)
    hook = _hook_path(tmp_path)
    hook.write_text("#!/bin/sh\n" + _RALLY_SEG + "exit 0\n")
    hook.chmod(0o755)
    ag.install_hook(tmp_path)
    ag.uninstall_hook(tmp_path)
    body = hook.read_text()
    assert ag._MARKER not in body
    assert "private-slug-guard" in body  # foreign segment preserved


def test_hook_blocks_undeclared_import_and_advisory_bypasses(tmp_path):
    """End-to-end: the installed pre-commit blocks a commit that adds an
    undeclared hard import, and BL_ARTIFACT_ADVISORY=1 downgrades to warn."""
    import os
    _git_init(tmp_path)
    (tmp_path / "scripts").mkdir()
    for s in ("artifact_guard.py", "import_manifest_lint.py"):
        (tmp_path / "scripts" / s).write_text((_REPO / "scripts" / s).read_text())
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="t"\ndependencies=["pathspec"]\n')
    _install_source(tmp_path)
    ag.install_hook(tmp_path)
    (tmp_path / "scripts" / "feature.py").write_text("import requests\n")
    _git(tmp_path, "add", "-A")
    # default: blocked (non-zero) by the undeclared import
    blocked = subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "x"],
                             capture_output=True, text=True)
    assert blocked.returncode != 0
    assert "requests" in (blocked.stdout + blocked.stderr)
    # advisory: same staged content commits successfully
    env = {**os.environ, "BL_ARTIFACT_ADVISORY": "1"}
    ok = subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "x"],
                        capture_output=True, text=True, env=env)
    assert ok.returncode == 0
