#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Differential test: this checkout's Bash dispatcher against a base ref's.

The two files that make up the dispatcher's Python fast path,
scripts/hooks/pre_bash_dispatch.sh and scripts/hooks/pre_bash_fastpath.py, are
taken from `git show <base>:<path>` (base defaults to `main`, override with
BL_DIFF_BASE_REF) into a temporary plugin tree whose every other file is a
symlink to this checkout. A second tree holds this checkout's own copies of the
two files. Every case runs through both trees with the same stdin, environment
and an identically shaped scratch area (repositories + HOME), and the test
compares, per case, the exit status, the exact stdout bytes and the exact stderr
bytes, and at the end every file the runs left behind (path + content).

Run:  python3 scripts/hooks/test_pre_bash_fastpath_differential.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from test_pre_bash_fastpath import CORPUS, POLICY, event_bytes  # noqa: E402

# Pinned to the last commit before the one-interpreter change. A moving ref such
# as "main" would compare this code with itself once the branch merges.
BASE_REF = os.environ.get("BL_DIFF_BASE_REF", "04d4cf2f")
OWNED = ("scripts/hooks/pre_bash_dispatch.sh", "scripts/hooks/pre_bash_fastpath.py")
HOOK = 'root="${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}"; hook="$root/scripts/hooks/pre_bash_dispatch.sh"; ' \
       'if [ -x "$hook" ]; then "$hook"; else printf \'{}\'; fi'
REAL_PY = shutil.which("python3")

# Cases beyond the shared corpus: (name, project, command-or-event, env changes).
# An env value of None removes the variable; "@nopy" / "@gatesfail" select a PATH.
EXTRA: list[tuple[str, str, object, dict]] = [
    ("linked-wt-ls", "linked", "ls", {}),
    ("linked-wt-block", "linkedpol", "danger-block now", {}),
    ("linked-wt-confirm", "linkedpol", "danger-confirm now", {}),
    ("opted-subdir-ls", "optsub", "ls", {}),
    ("opted-subdir-block", "polsub", "danger-block now", {}),
    ("symlinked-cwd-block", "symlink", "danger-block now", {}),
    ("trailing-slash-block", "slash", "danger-block now", {}),
    ("mixed-case-cwd-block", "mixedcase", "danger-block now", {}),
    ("core-worktree-elsewhere", "corewt", "danger-block now", {}),
    ("invalid-dotgit-in-repo", "invdot", "danger-block now", {}),
    ("nested-optin-in-plain", "nested", "danger-confirm now", {}),
    ("compound-first-word", "policy", "echo ok && danger-block now", {}),
    ("compound-first-word-2", "opted", "ls && git push --force origin main", {}),
    ("autonomy-ask-default", "opted", "gh pr merge 12 --squash", {}),
    ("home-unset", "opted", "ls", {"HOME": None}),
    ("home-unset-plain", "plain", "ls", {"HOME": None}),
    ("cwd-is-home", "home", "danger-block now", {}),
    ("git-dir-env", "opted", "ls", {"GIT_DIR": "/nonexistent"}),
    ("python-missing", "opted", "ls", {"PATH": "@nopy"}),
    ("python-missing-kill", "opted", "ls", {"PATH": "@nopy", "BUILD_LOOP_HOOKS": "off"}),
    ("gates-helper-fails-sub", "optsub", "ls", {"PATH": "@gatesfail"}),
    ("gates-helper-fails-plain", "plain", "ls", {"PATH": "@gatesfail"}),
    ("kill-switch-codex", "opted", "codex exec hi < /dev/null", {"BUILD_LOOP_HOOKS": "off"}),
    ("kill-switch-empty", "opted", "", {"BUILD_LOOP_HOOKS": "off"}),
    ("sfltool-advisory", "opted", "sfltool dumpbtm | head", {}),
    ("load-in-config-repo", "policy", "node -e 'while(true){}'", {}),
    ("wait-in-linked-wt", "linked", "while true; do sleep 30; done", {}),
    ("cwd-newline", "opted", {"tool_input": {"command": "ls"}, "cwd": "/tmp\nx"}, {}),
]

# Known, intended difference: the dispatcher used to start a SECOND python3 for
# the gates. When that one start failed while the first succeeded, it exited 2.
# In an opted-in repo root the gates now run inside the first interpreter, so
# there is no second start to fail; the gates really ran, and their verdict
# stands. Pinned here so the difference is visible, not silent.
SECOND_SPAWN_FAIL = ("gates-helper-fails-root", "opted", "ls", {"PATH": "@gatesfail"})


def _git(repo: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, check=True)


def _init(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _optin(repo: Path, policy: bool) -> None:
    (repo / ".build-loop").mkdir(parents=True, exist_ok=True)
    if policy:
        (repo / ".build-loop" / "config.json").write_text(json.dumps(POLICY), encoding="utf-8")
    else:
        (repo / ".build-loop" / "state.json").write_text("{}", encoding="utf-8")


def make_scratch(base: Path) -> dict:
    """Every project the cases run in, under `base` (resolved, no symlinks)."""
    base.mkdir(parents=True)
    base = base.resolve()
    p: dict[str, Path] = {}
    p["home"] = base / "home"
    p["home"].mkdir()
    p["opted"] = _init(base / "opted")
    _optin(p["opted"], policy=False)
    p["policy"] = _init(base / "policy")
    _optin(p["policy"], policy=True)
    p["plain"] = _init(base / "plain")
    p["nogit"] = base / "nogit"
    p["nogit"].mkdir()
    p["sub"] = p["policy"] / "src" / "deep"
    p["sub"].mkdir(parents=True)
    p["optsub"] = p["opted"] / "pkg"
    p["optsub"].mkdir()
    p["polsub"] = p["policy"] / "lib"
    p["polsub"].mkdir()
    _git(p["opted"], "worktree", "add", "-q", str(base / "linked"))
    p["linked"] = base / "linked"
    _optin(p["linked"], policy=False)
    _git(p["policy"], "worktree", "add", "-q", str(base / "linkedpol"))
    p["linkedpol"] = base / "linkedpol"
    _optin(p["linkedpol"], policy=True)
    (base / "link").symlink_to(p["policy"])
    p["symlink"] = base / "link"
    p["slash"] = str(p["policy"]) + "/"  # a str: Path() would drop the slash
    mc = _init(base / "Mixedcase")
    _optin(mc, policy=True)
    p["mixedcase"] = base / "mixedcase"  # wrong case; same directory on APFS/HFS+
    p["corewt"] = _init(base / "corewt")
    _optin(p["corewt"], policy=True)
    other = base / "corewt-tree"
    other.mkdir()
    # git reports `other` as the top level, which has no opt-in: no envelope.
    _git(p["corewt"], "config", "core.worktree", str(other))
    p["invdot"] = p["policy"] / "inv"
    (p["invdot"] / ".git").mkdir(parents=True)
    # Default policy here, block policy at the real root: a resolver that stops
    # at the invalid .git instead of walking up (as git does) flips the verdict.
    _optin(p["invdot"], policy=False)
    p["nested"] = p["plain"] / "tool"
    _optin(p["nested"], policy=True)
    return p


def make_tree(dest: Path, owned: dict[str, bytes]) -> Path:
    """A plugin root that is this checkout except for the OWNED files."""
    dest.mkdir(parents=True)
    owned_dirs = {"scripts", "scripts/hooks"}

    def mirror(rel: str) -> None:
        src = PLUGIN_ROOT / rel if rel else PLUGIN_ROOT
        out = dest / rel if rel else dest
        out.mkdir(exist_ok=True)
        for entry in os.listdir(src):
            child = f"{rel}/{entry}" if rel else entry
            if child in owned_dirs:
                mirror(child)
            elif child in owned:
                (dest / child).write_bytes(owned[child])
                (dest / child).chmod(0o755)
            else:
                (out / entry).symlink_to(src / entry)

    mirror("")
    return dest


def make_paths(base: Path) -> dict[str, str]:
    """PATH values: no python3 at all, and a python3 that fails only for `gates`."""
    keep = ["bash", "sh", "perl", "git", "cat", "dirname", "sed", "head", "mktemp", "rm", "env", "uname"]
    nopy = base / "nopy"
    nopy.mkdir(parents=True)
    for tool in keep:
        found = shutil.which(tool)
        if found:
            (nopy / tool).symlink_to(found)
    gf = base / "gatesfail"
    gf.mkdir()
    shim = gf / "python3"
    shim.write_text('#!/bin/sh\nfor a in "$@"; do [ "$a" = gates ] && exit 1; done\n'
                    f'exec "{REAL_PY}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return {"@nopy": str(nopy), "@gatesfail": f"{gf}:{os.environ.get('PATH', '')}"}


def run_case(tree: Path, scratch: dict, paths: dict[str, str], case) -> subprocess.CompletedProcess:
    name, project, spec, extra = case
    cwd = scratch[project]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BUILD_LOOP_", "BL_", "CLAUDE_"))}
    env.update(CLAUDE_PLUGIN_ROOT=str(tree), CLAUDE_PROJECT_DIR=str(scratch["opted"]), HOME=str(scratch["home"]))
    for key, value in extra.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = paths.get(value, value)
    run_dir = Path(cwd) if Path(cwd).is_dir() else scratch["opted"]
    return subprocess.run(["/bin/bash", "-c", HOOK], cwd=run_dir, input=event_bytes(spec, cwd, f"diff-{name}"),
                          capture_output=True, env=env, timeout=60)


_STAMPS = [(re.compile(rb"\d{4}-\d\d-\d\dT[0-9:.]+(?:Z|[+-]\d\d:?\d\d)?"), b"<ts>"),
           (re.compile(rb"\d{8}T\d{6,12}Z"), b"<ts>"),
           (re.compile(rb"\b\d{10}(?:\.\d+)?\b"), b"<epoch>"),
           (re.compile(rb"\b[0-9a-f]{64}\b"), b"<sha256>")]


_BUDGET_NOTE = re.compile(rb"within the time budget[^\"]*")


def _mask(data: bytes, tmp: Path, side: str) -> bytes:
    """Content with this run's paths and wall-clock stamps (and hashes over them) masked."""
    for name in ("scr", "tree"):
        data = data.replace(str(tmp / f"{name}-{side}").encode(), f"<{name}>".encode())
    for pattern, repl in _STAMPS:
        data = pattern.sub(repl, data)
    return data


def snapshot(root: Path, tmp: Path, side: str) -> list[tuple[bytes, bytes]]:
    """Every file under `root` (git internals excluded): masked path and masked content."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            if f == ".git":  # a linked worktree's pointer file names its own path
                continue
            path = Path(dirpath) / f
            body = ("link:" + os.readlink(path)).encode() if path.is_symlink() else path.read_bytes()
            out.append((_mask(str(path.relative_to(root)).encode(), tmp, side), _mask(body, tmp, side)))
    return sorted(out)


def base_owned() -> dict[str, bytes]:
    return {rel: subprocess.run(["git", "-C", str(PLUGIN_ROOT), "show", f"{BASE_REF}:{rel}"],
                                capture_output=True, check=True).stdout for rel in OWNED}


def head_owned() -> dict[str, bytes]:
    return {rel: (PLUGIN_ROOT / rel).read_bytes() for rel in OWNED}


class Differential(unittest.TestCase):
    """Base ref vs this checkout: identical stdout, stderr, exit status, files."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name).resolve()
        # Same-length names so normalising one path onto the other is exact.
        cls.trees = (make_tree(tmp / "tree-a", base_owned()), make_tree(tmp / "tree-b", head_owned()))
        cls.scratch = (make_scratch(tmp / "scr-a"), make_scratch(tmp / "scr-b"))
        cls.paths = make_paths(tmp / "bin")
        cls.tmp = tmp

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _norm(self, data: bytes, side: str) -> bytes:
        """Paths onto side a; the worktree guard's budget note onto its prefix.

        `rm -rf /` makes the worktree guard scan / under a time budget, and the
        note says WHERE the budget ran out, which varies run to run even when
        both sides are the same code. Only that clause is masked.
        """
        for name in ("tree", "scr"):
            data = data.replace(str(self.tmp / f"{name}-{side}").encode(), str(self.tmp / f"{name}-a").encode())
        return _BUDGET_NOTE.sub(b"within the time budget", data)

    def _pair(self, case):
        a = run_case(self.trees[0], self.scratch[0], self.paths, case)
        b = run_case(self.trees[1], self.scratch[1], self.paths, case)
        return a, b

    def test_every_case_identical(self) -> None:
        cases = list(CORPUS) + EXTRA
        seen = 0
        for case in cases:
            with self.subTest(case=case[0]):
                a, b = self._pair(case)
                self.assertEqual((a.returncode, self._norm(a.stdout, "a"), self._norm(a.stderr, "a")),
                                 (b.returncode, self._norm(b.stdout, "b"), self._norm(b.stderr, "b")))
                seen += 1
        self.assertEqual(seen, len(cases))
        # Files the runs wrote, compared after all cases ran.
        files = [snapshot(self.tmp / f"scr-{side}", self.tmp, side) for side in "ab"]
        self.assertGreater(len(files[0]), 10)
        self.assertEqual(files[0], files[1])

    def test_second_python_start_failure_is_the_one_intended_difference(self) -> None:
        a, b = self._pair(SECOND_SPAWN_FAIL)
        if a.returncode == b.returncode == 2:
            self.skipTest("this checkout still starts a second python3 for the gates")
        self.assertEqual((a.returncode, a.stdout), (2, b"{}"))
        self.assertEqual((b.returncode, b.stdout), (0, b"{}\n"))


class ToplevelProofAgreesWithGit(unittest.TestCase):
    """pre_bash_fastpath._is_own_toplevel may skip git only when git agrees."""

    def test_skip_only_where_git_prints_the_same_path(self) -> None:
        import pre_bash_fastpath as fp
        with tempfile.TemporaryDirectory() as tmp:
            scratch = make_scratch(Path(tmp).resolve() / "s")
            dirs = [str(v) for v in scratch.values()] + [str(PLUGIN_ROOT), str(PLUGIN_ROOT) + "/scripts", "/", "/tmp"]
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k in fp._GIT_ENV_INERT}
            skipped = set()
            with unittest.mock.patch.dict(os.environ, env, clear=True):
                for d in dirs:
                    git = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                                         capture_output=True).stdout.decode().rstrip("\n")
                    if fp._is_own_toplevel(d):
                        skipped.add(d)
                        self.assertEqual(git, d, d)
                with unittest.mock.patch.dict(os.environ, {"GIT_DIR": "/nonexistent"}):
                    self.assertFalse(fp._is_own_toplevel(str(scratch["opted"])))
        # The proof must actually fire where it pays: a repo root and a linked worktree.
        for name in ("opted", "policy", "linked", "linkedpol"):
            self.assertIn(str(scratch[name]), skipped, name)
        for name in ("symlink", "slash", "mixedcase", "corewt", "invdot", "optsub", "nested"):
            self.assertNotIn(str(scratch[name]), skipped, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
