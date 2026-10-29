#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Equivalence corpus for pre_bash_dispatch.sh's single-interpreter fast path.

pre_bash_fastpath.py runs the dispatcher's always-on Python gates in one
interpreter. Its contract is that no decision changes. This file holds the
corpus that proves it, used three ways:

  1. Drift guard (unittest): the helper's in-process emulation of
     pre_bash_autonomy.sh must print exactly what the real script prints, for
     every corpus input. pre_bash_autonomy.sh stays the reference; an edit to
     it that is not mirrored in the helper fails here.
  2. Decision pins (unittest): one input per refusal class must still produce
     its refusal (stdout + exit code) through the real dispatcher.
  3. Before/after comparison (CLI): run the corpus through two plugin roots
     and report every stdout / exit-code / stderr mismatch:

       python3 scripts/hooks/test_pre_bash_fastpath.py --compare \\
           --before /path/to/main-checkout --after /path/to/branch-checkout \\
           --scratch /tmp/scratch-dir
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent.parent
DISPATCH_REL = "scripts/hooks/pre_bash_dispatch.sh"
AUTONOMY_SH = HERE / "pre_bash_autonomy.sh"
FASTPATH = HERE / "pre_bash_fastpath.py"

# Projects the corpus runs in (built by make_projects):
#   opted   — .build-loop/state.json (gates armed, default autonomy policy)
#   policy  — .build-loop/config.json with custom block/confirm/warn patterns
#   sub     — a subdirectory of `policy` (root resolution path)
#   plain   — a git repo that never opted in
#   nogit   — a plain directory, not a repository
POLICY = {"autonomy": {"blockFor": ["danger-block*"], "confirmFor": ["danger-confirm*"],
                       "warnFor": ["danger-warn*"]}}

# (name, project, command-or-raw-event, extra env). A dict is sent as the whole
# event; a str is the command; bytes are sent verbatim as stdin.
CORPUS: list[tuple[str, str, object, dict]] = [
    # allowed, common shapes
    ("ls", "opted", "ls", {}),
    ("ls-plain", "plain", "ls -la", {}),
    ("ls-nogit", "nogit", "ls", {}),
    ("git-status", "opted", "git status", {}),
    ("git-log", "opted", "git log --oneline -5 | head -3", {}),
    ("git-diff-sub", "sub", "git diff --stat", {}),
    ("pipe", "opted", "cat README.md | grep -n foo | wc -l", {}),
    ("env-assign", "opted", "FOO=1 BAR='x y' env | grep FOO", {}),
    ("heredoc", "opted", "cat <<'EOF' > /dev/null\ngit push origin main\nrm -rf /\nEOF", {}),
    ("multiline", "opted", "echo a\necho b\necho c", {}),
    ("crlf", "opted", "echo a\r\necho b", {}),
    ("unicode", "opted", "echo 'héllo — wörld ✓'", {}),
    ("leading-dash", "opted", "--self-test", {}),
    ("dash-cmd", "opted", "-n foo", {}),
    ("subshell", "opted", "(cd /tmp && ls) && echo done", {}),
    ("python-c", "opted", "python3 -c 'print(1)'", {}),
    ("node-e", "opted", "node -e 'console.log(1)'", {}),
    ("sleep-bounded", "opted", "for i in 1 2 3; do sleep 0; done", {}),
    # credential policy (Keychain) refusals
    ("cred-find-generic", "opted", "security find-generic-password -s foo -w", {}),
    ("cred-plain-repo", "plain", "security find-internet-password -s github.com", {}),
    ("cred-dump", "nogit", "security dump-keychain", {}),
    ("cred-obfuscated", "opted", "$'security' find-generic-password -s x", {}),
    ("cred-git-helper", "opted", "git -c credential.helper=osxkeychain fetch origin", {}),
    ("cred-osxkeychain", "opted", "git credential-osxkeychain get", {}),
    ("cred-kill-switch", "opted", "security find-generic-password -s foo -w", {"BUILD_LOOP_HOOKS": "off"}),
    # privileged-command gate
    ("priv-sfltool", "opted", "sfltool dumpbtm", {}),
    ("priv-sudo", "plain", "sudo ls /var/root", {}),
    ("priv-spctl-add", "opted", "spctl --add /Applications/X.app", {}),
    ("priv-spctl-read", "opted", "spctl -a /Applications/Safari.app", {}),
    ("priv-diskutil-list", "opted", "diskutil list", {}),
    # worktree-removal guard
    ("wt-remove", "opted", "git worktree remove ../some-worktree", {}),
    ("wt-rm-rf", "opted", "rm -rf ../some-worktree", {}),
    ("wt-list", "opted", "git worktree list", {}),
    # unbounded load / wait gates
    ("load-node", "opted", "node -e 'while(true){}'", {}),
    ("load-python", "opted", "python3 -c 'while True: pass'", {}),
    ("wait-while-true", "opted", "while true; do sleep 30; done", {}),
    ("wait-until-false", "opted", "until false; do sleep 5; done", {}),
    # autonomy gate (default + repo policy)
    ("auto-rm-rf-root", "opted", "rm -rf /", {}),
    ("auto-force-push-main", "opted", "git push --force origin main", {}),
    ("auto-gh-pr-merge", "opted", "gh pr merge 12 --squash", {}),
    ("auto-drop-table", "opted", "DROP TABLE users", {}),
    ("policy-block", "policy", "danger-block now", {}),
    ("policy-confirm", "policy", "danger-confirm now", {}),
    ("policy-warn", "policy", "danger-warn now", {}),
    ("policy-sub-block", "sub", "danger-block now", {}),
    ("policy-plain-ignored", "plain", "rm -rf /", {}),
    # deploy gate + push gate + commit auditor + consent
    ("deploy-vercel", "opted", "vercel deploy --prod", {}),
    ("deploy-wrangler", "opted", "npm run build && wrangler deploy", {}),
    ("push-plain", "opted", "git push", {}),
    ("commit", "opted", "git commit -m 'x'", {}),
    ("consent-codex", "opted", "codex exec 'hello' < /dev/null", {}),
    ("consent-grep", "opted", "grep -rn claude README.md", {}),
    ("dep-npm-ci", "opted", "npm ci --ignore-scripts", {}),
    # malformed / edge events
    ("empty-cmd", "opted", "", {}),
    ("no-tool-input", "opted", {"hook_event_name": "PreToolUse", "tool_name": "Bash"}, {}),
    ("cwd-null", "opted", {"tool_input": {"command": "ls"}, "cwd": None}, {}),
    ("cwd-missing-dir", "opted", {"tool_input": {"command": "ls"}, "cwd": "/nonexistent/dir"}, {}),
    ("cmd-number", "opted", {"tool_input": {"command": 5}}, {}),
    ("invalid-json", "opted", b"{not json", {}),
    ("list-json", "opted", b"[1, 2]", {}),
    ("kill-switch-ls", "opted", "ls", {"BUILD_LOOP_HOOKS": "off"}),
]


def make_projects(base: Path) -> dict[str, Path]:
    def git(repo: Path, *args: str) -> None:
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        for k in [k for k in env if k.startswith("GIT_") and k not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")]:
            env.pop(k)
        subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, check=False)

    projects = {}
    for name in ("opted", "policy", "plain"):
        repo = base / name
        repo.mkdir(parents=True, exist_ok=True)
        if not (repo / ".git").exists():
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "t@example.com")
            git(repo, "config", "user.name", "Test")
            (repo / "README.md").write_text("seed\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-q", "-m", "seed")
        projects[name] = repo
    (base / "opted" / ".build-loop").mkdir(exist_ok=True)
    (base / "opted" / ".build-loop" / "state.json").write_text("{}", encoding="utf-8")
    (base / "policy" / ".build-loop").mkdir(exist_ok=True)
    (base / "policy" / ".build-loop" / "config.json").write_text(json.dumps(POLICY), encoding="utf-8")
    projects["sub"] = base / "policy" / "src" / "deep"
    projects["sub"].mkdir(parents=True, exist_ok=True)
    projects["nogit"] = base / "nogit"
    projects["nogit"].mkdir(exist_ok=True)
    return projects


def event_bytes(spec: object, cwd: Path, session: str = "bench") -> bytes:
    if isinstance(spec, bytes):
        return spec
    if isinstance(spec, dict):
        event = {"session_id": session, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                 "cwd": str(cwd), **spec}
        return json.dumps(event).encode()
    return json.dumps({"session_id": session, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "tool_input": {"command": spec}, "cwd": str(cwd)}).encode()


def _env(root: Path, project_dir: Path, extra: dict) -> dict:
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(root), CLAUDE_PROJECT_DIR=str(project_dir),
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", **extra)
    env.pop("BUILD_LOOP_HOOKS", None) if "BUILD_LOOP_HOOKS" not in extra else None
    return env


def run_dispatch(root: Path, project_dir: Path, cwd: Path, stdin: bytes, extra: dict):
    """Run the dispatcher the way hooks.json does (`"$hook"` via bash -c)."""
    cmd = 'root="${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}"; hook="$root/scripts/hooks/pre_bash_dispatch.sh"; ' \
          'if [ -x "$hook" ]; then "$hook"; else printf \'{}\'; fi'
    return subprocess.run(["/bin/bash", "-c", cmd], cwd=cwd, input=stdin, capture_output=True,
                          env=_env(root, project_dir, extra), timeout=60)


class AutonomyEmulationMatchesScript(unittest.TestCase):
    """Drift guard: the helper's autonomy emulation == pre_bash_autonomy.sh."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        cls.projects = make_projects(cls.base)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_every_corpus_input(self) -> None:
        for name, project, spec, extra in CORPUS:
            with self.subTest(name=name):
                cwd = self.projects[project]
                stdin = event_bytes(spec, cwd)
                env = _env(PLUGIN_ROOT, cwd, extra)
                ref = subprocess.run(["bash", str(AUTONOMY_SH)], cwd=cwd, input=stdin,
                                     capture_output=True, env=env, timeout=60)
                emu = subprocess.run([sys.executable, str(FASTPATH), "autonomy"], cwd=cwd, input=stdin,
                                     capture_output=True, env=dict(env, BL_PLUGIN_ROOT=str(PLUGIN_ROOT)),
                                     timeout=60)
                self.assertEqual(ref.stdout.rstrip(b"\n"), emu.stdout.rstrip(b"\n"))


class RefusalClassesStillRefuse(unittest.TestCase):
    """One pinned input per refusal class, through the real dispatcher."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        cls.projects = make_projects(cls.base)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _run(self, project: str, spec: object, extra: dict | None = None):
        cwd = self.projects[project]
        return run_dispatch(PLUGIN_ROOT, cwd, cwd, event_bytes(spec, cwd), extra or {})

    def _decision(self, r) -> str | None:
        try:
            return (json.loads(r.stdout or b"{}").get("hookSpecificOutput") or {}).get("permissionDecision")
        except ValueError:
            return "unparseable"

    def test_plain_command_allows_with_empty_envelope(self) -> None:
        r = self._run("opted", "ls")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, b"{}"))

    def test_keychain_read_is_denied_even_with_kill_switch(self) -> None:
        for extra in ({}, {"BUILD_LOOP_HOOKS": "off"}):
            r = self._run("opted", "security find-generic-password -s foo -w", extra)
            self.assertEqual((r.returncode, self._decision(r)), (0, "deny"), extra)

    def test_unparseable_event_is_denied_by_credential_policy(self) -> None:
        r = self._run("opted", b"{not json")
        self.assertEqual((r.returncode, self._decision(r)), (0, "deny"))

    def test_privileged_command_is_redirected(self) -> None:
        r = self._run("plain", "sfltool dumpbtm")
        self.assertEqual((r.returncode, self._decision(r)), (0, "deny"))

    def test_unbounded_load_and_wait_exit_2(self) -> None:
        for cmd in ("node -e 'while(true){}'", "while true; do sleep 30; done"):
            r = self._run("opted", cmd)
            self.assertEqual(r.returncode, 2, cmd)
            self.assertIn(b"rejected", r.stderr, cmd)

    def test_autonomy_block_confirm_warn(self) -> None:
        self.assertEqual(self._decision(self._run("policy", "danger-block now")), "deny")
        self.assertEqual(self._decision(self._run("sub", "danger-confirm now")), "ask")
        self.assertIsNone(self._decision(self._run("policy", "danger-warn now")))
        self.assertIsNone(self._decision(self._run("plain", "danger-block now")))


def compare(before: Path, after: Path, scratch: Path) -> int:
    projects = make_projects(scratch)
    mismatches = 0
    # Two normalisations, both for state the comparison itself would perturb:
    #   - a fresh session id per run, because run_registration_gate warns once
    #     per (session, repo) and the first variant would consume the warning;
    #   - the after-root path rewritten to the before-root path, because the
    #     privileged gate's redirect names the broker under $CLAUDE_PLUGIN_ROOT.
    nonce = os.urandom(4).hex()
    norm = lambda b: b.replace(str(after).encode(), str(before).encode())
    print(f"{'input':24} {'before rc/stdout':40} {'after':6}")
    for name, project, spec, extra in CORPUS:
        cwd = projects[project]
        a = run_dispatch(before, scratch, cwd, event_bytes(spec, cwd, f"cmp-{nonce}-{name}-a"), extra)
        b = run_dispatch(after, scratch, cwd, event_bytes(spec, cwd, f"cmp-{nonce}-{name}-b"), extra)
        same = (a.returncode, a.stdout, a.stderr) == (b.returncode, norm(b.stdout), norm(b.stderr))
        mismatches += not same
        out = a.stdout.decode(errors="replace").replace("\n", "\\n")
        print(f"{name:24} {a.returncode} {out[:60]:60} {'same' if same else 'DIFF'}")
        if not same:
            print(f"   before rc={a.returncode} out={a.stdout[:300]!r} err={a.stderr[-300:]!r}")
            print(f"   after  rc={b.returncode} out={b.stdout[:300]!r} err={b.stderr[-300:]!r}")
    print(f"inputs={len(CORPUS)} mismatches={mismatches} (stdout, exit code and stderr compared)")
    return 1 if mismatches else 0


if __name__ == "__main__":
    if "--compare" in sys.argv:
        ap = argparse.ArgumentParser()
        ap.add_argument("--compare", action="store_true")
        ap.add_argument("--before", required=True, type=Path)
        ap.add_argument("--after", required=True, type=Path)
        ap.add_argument("--scratch", required=True, type=Path)
        a = ap.parse_args()
        a.scratch.mkdir(parents=True, exist_ok=True)
        raise SystemExit(compare(a.before, a.after, a.scratch))
    unittest.main()
