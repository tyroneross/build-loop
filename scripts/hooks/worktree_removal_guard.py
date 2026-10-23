#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""PreToolUse:Bash guard — no worktree is deleted before its files are archived.

Named failure (2026-10-23): an agent removed 83 "merged, clean" Easy Terminal
worktrees with `git worktree remove` after `git status --porcelain -uall` came
back empty. That check omits GITIGNORED files, so `.build-loop/`, `.bookmark/`,
`.designdoc/` and `.ibr/` state in 55 of them — including a Groundwork design
project a live server was serving — was deleted, unrecoverably.

The guard denies a Bash command that would delete a registered linked worktree
unless `scripts/worktree_archive.py` wrote a receipt for that path under 30
minutes ago, at the current HEAD, with the worktree's files unchanged since
(content fingerprint) and the archive intact (size + sha256). The deny reason
carries the exact archive command.

It guards cooperative agents, not adversaries. Commands judged:
  - `git [-C d] worktree remove [-f]... <path-or-unique-suffix>` (resolved the
    way git does, against `git worktree list --porcelain`);
  - `rm -r`, `trash`, `safe-del`, `xargs rm -r`, `find ... -delete` and
    `find ... -exec rm` whose target is a registered worktree or an ancestor
    of one, through `env`/`sudo`/`nice`/`timeout`/`time`/`command`/`exec`
    wrappers, `bash -c`, and heredocs fed to a shell;
  - any removal that would also delete a receipt's own archive.
`git worktree prune` is allowed: it only drops metadata for directories that
are already gone.

Shell model: its own tokenizer keeps quoting (a quoted `'*'` is a literal, a
quoted heredoc body is data), subshells scope `cd`, `cd --`/`-P` are options,
and a removal operand that is a symlink judges the link (`rm` unlinks it).

Failure policy:
  - CLOSED when a worktree removal is established and no valid receipt exists,
    when a relative removal runs in an unknown directory and a registered
    worktree matches it, or when the ancestor scan cannot prove no worktree
    lies beneath its directory budget;
  - OPEN, with a visible `systemMessage`, when git cannot read the worktree, a
    target is `$VAR`/command substitution, the guard's 2 s time budget runs out
    before any removal is established, or the guard itself crashes. Denials
    already established survive every failure path.

Input: the PreToolUse event on stdin (`tool_input.command` + `cwd`; a top-level
`command` is accepted for Cursor). Output: Claude/Codex JSON, or Cursor's shape
with `--host cursor`. Always exits 0.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import worktree_archive as wa  # noqa: E402

BUDGET_S = float(os.environ.get("BUILD_LOOP_WT_GUARD_BUDGET_S") or 2.0)
SCAN_BUDGET = int(os.environ.get("BUILD_LOOP_WT_GUARD_SCAN_BUDGET") or 50_000)
DEADLINE = time.monotonic() + BUDGET_S
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
DELETERS = {"rm", "trash", "safe-del", "safe-delete"}
SEPARATORS = {";", "&&", "||", "|", "|&", "&", "\n"}
FIND_FILTERS = {"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex",
                "-type", "-newer", "-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin", "-size",
                "-empty", "-user", "-group", "-perm", "-prune", "-not", "!", "-links", "-inum",
                "-samefile", "-lname", "-ilname", "-uid", "-gid", "-newermt", "-mindepth"}


def remaining() -> float:
    return DEADLINE - time.monotonic()


class Unresolved(Exception):
    pass


class UncertainCwd(Exception):
    pass


class Word:
    __slots__ = ("value", "glob", "dynamic")

    def __init__(self) -> None:
        self.value, self.glob, self.dynamic = "", False, False


# ── tokenizer ────────────────────────────────────────────────────────────────

_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)[ \t]*(['\"]?)([A-Za-z0-9_.\-]+)\2")


def strip_heredocs(cmd: str) -> tuple[str, list[tuple[str, str]]]:
    """Remove heredoc bodies (data, not commands). Returns (cleaned, [(host_line, body)])."""
    lines = cmd.replace("\r", "\n").split("\n")
    out, bodies, i = [], [], 0
    while i < len(lines):
        line = lines[i]
        found = list(_HEREDOC.finditer(line))
        out.append(_HEREDOC.sub(" ", line) if found else line)
        i += 1
        for m in found:
            strip_tabs, delim = m.group(1) == "-", m.group(3)
            body = []
            while i < len(lines):
                cand = lines[i].lstrip("\t") if strip_tabs else lines[i]
                i += 1
                if cand == delim:
                    break
                body.append(lines[i - 1])
            bodies.append((line[: m.start()], "\n".join(body)))
    return "\n".join(out), bodies


def _matching(s: str, i: int, open_c: str, close_c: str) -> int:
    depth, j = 0, i
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == open_c:
            depth += 1
        elif s[j] == close_c:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    raise ValueError("unbalanced parenthesis")


def tokenize(s: str) -> list:
    """Shell-ish tokens: Word objects and operator strings ('redir' for redirections)."""
    toks: list = []
    cur: Word | None = None
    i, n = 0, len(s)

    def word() -> Word:
        nonlocal cur
        if cur is None:
            cur = Word()
        return cur

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            toks.append(cur)
            cur = None

    while i < n:
        c = s[i]
        if c in " \t":
            flush(); i += 1; continue
        if c == "\n":
            flush(); toks.append("\n"); i += 1; continue
        if c == "#" and cur is None:
            while i < n and s[i] != "\n":
                i += 1
            continue
        if c == "\\":
            if i + 1 < n and s[i + 1] != "\n":
                word().value += s[i + 1]
            i += 2; continue
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise ValueError("unterminated single quote")
            word().value += s[i + 1:j]; i = j + 1; continue
        if c == '"':
            w, k = word(), i + 1
            while k < n and s[k] != '"':
                if s[k] == "\\" and k + 1 < n and s[k + 1] in '"\\$`\n':
                    w.value += s[k + 1]; k += 2; continue
                if s[k] in "$`":
                    w.dynamic = True
                w.value += s[k]; k += 1
            if k >= n:
                raise ValueError("unterminated double quote")
            i = k + 1; continue
        if c == "$" and i + 1 < n and s[i + 1] == "(":
            j = _matching(s, i + 1, "(", ")")
            w = word(); w.dynamic = True; w.value += s[i:j + 1]; i = j + 1; continue
        if c == "`":
            j = s.find("`", i + 1)
            if j < 0:
                raise ValueError("unterminated backtick")
            w = word(); w.dynamic = True; w.value += s[i:j + 1]; i = j + 1; continue
        if c == "$":
            w = word(); w.dynamic = True; w.value += c; i += 1; continue
        if c in "<>" and i + 1 < n and s[i + 1] == "(":
            j = _matching(s, i + 1, "(", ")")
            w = word(); w.dynamic = True; w.value += s[i:j + 1]; i = j + 1; continue
        if c in "<>" or (c == "&" and i + 1 < n and s[i + 1] == ">"):
            if cur is not None and cur.value.isdigit() and not cur.dynamic:
                cur = None  # fd number
            flush()
            j = i + 1
            while j < n and s[j] in "<>&|":
                j += 1
            toks.append("redir"); i = j; continue
        if c in ";&|()":
            flush()
            two = s[i:i + 2]
            if two in ("&&", "||", "|&", ";;"):
                toks.append(two); i += 2; continue
            toks.append(c); i += 1; continue
        if c in "*?[":
            word().glob = True
        word().value += c
        i += 1
    flush()
    return toks


# ── evaluation state ─────────────────────────────────────────────────────────

class Result:
    def __init__(self) -> None:
        self.deny: list[str] = []
        self.warnings: list[str] = []
        self.need_archive: list[str] = []
        self.removals: dict[str, set[str]] = {}   # worktree -> deletion roots
        self.roots: set[str] = set()              # every path this command deletes
        self._registered: dict[str, list[str] | None] = {}

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def add_deny(self, msg: str, archive: str | None = None) -> None:
        if msg not in self.deny:
            self.deny.append(msg)
        if archive and archive not in self.need_archive:
            self.need_archive.append(archive)

    def removal(self, wt: str, root: str) -> None:
        self.removals.setdefault(wt, set()).add(root)
        self.roots.add(root)

    def registered(self, directory: str | None) -> list[str] | None:
        """Linked worktrees registered in the repository owning `directory`."""
        if not directory:
            return None
        if directory in self._registered:
            return self._registered[directory]
        out: list[str] | None = None
        try:
            proc = subprocess.run(["git", "-C", directory, "worktree", "list", "--porcelain"],
                                  capture_output=True, text=True, check=False,
                                  timeout=max(0.2, min(3.0, remaining())))
            if proc.returncode == 0:
                paths = [ln[9:].strip() for ln in proc.stdout.splitlines() if ln.startswith("worktree ")]
                out = [os.path.realpath(p) for p in paths[1:]]  # first entry is the main worktree
        except (OSError, subprocess.TimeoutExpired):
            out = None
        self._registered[directory] = out
        return out


def _under(child: str, parent: str) -> bool:
    return (child.rstrip("/") + "/").startswith(parent.rstrip("/") + "/")


def resolve(word: Word, cwd: str | None) -> list[tuple[str, bool]]:
    """[(absolute path with the LAST component unresolved, trailing_slash)]."""
    if word.dynamic:
        raise Unresolved(word.value)
    v = os.path.expanduser(word.value) if word.value.startswith("~") else word.value
    if not os.path.isabs(v):
        if cwd is None:
            raise UncertainCwd(word.value)
        v = os.path.join(cwd, v)
    candidates = (sorted(glob.glob(v)) or [v]) if word.glob else [v]
    out = []
    for p in candidates:
        trailing = p.endswith("/") and p != "/"
        norm = os.path.normpath(p)
        full = os.path.join(os.path.realpath(os.path.dirname(norm)), os.path.basename(norm)) if norm != "/" else "/"
        if trailing and os.path.islink(full):
            full = os.path.realpath(full)
        out.append((full, trailing))
    return out


def worktrees_under(target: str, res: Result) -> tuple[list[str], str | None]:
    """Registered linked worktrees at or beneath `target`. Returns (found, gap).

    gap: None (proven complete) · "budget" (directory budget hit) · "time".
    Only marker-verified cache roots and `.git` directories are skipped.
    """
    if os.path.islink(target) or not os.path.isdir(target):
        return [], None
    found = [target] if wa.linked_worktree_admin(target) else []
    stack, scanned = [target], 0
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False) or e.name == ".git":
                    continue
            except OSError:
                continue
            scanned += 1
            if scanned > SCAN_BUDGET:
                return found, "budget"
            if scanned % 256 == 0 and remaining() <= 0:
                return found, "time"
            path = e.path
            if os.path.isfile(os.path.join(path, ".git")) and wa.linked_worktree_admin(path):
                found.append(os.path.realpath(path))
            if wa.is_cache_root(Path(path)):
                continue
            stack.append(path)
    return found, None


def judge_root(word: Word, cwd: str | None, res: Result, verb: str, session_cwd: str) -> None:
    """A deletion operand: find the worktrees it would remove."""
    try:
        targets = resolve(word, cwd)
    except Unresolved as exc:
        res.warn(f"`{verb}` target {exc} is not resolvable before the shell runs; "
                 "if it names a worktree, archive it first")
        return
    except UncertainCwd as exc:
        uncertain_relative(str(exc), res, verb, session_cwd)
        return
    for full, _trailing in targets:
        if os.path.islink(full):
            continue  # rm/trash remove the link itself
        found, gap = worktrees_under(full, res)
        for wt in found:
            res.removal(wt, full)
        if os.path.isdir(full):
            res.roots.add(full)
        if gap == "budget":
            res.add_deny(f"{full}: could not prove no worktree lies beneath it "
                         f"(over {SCAN_BUDGET} directories); remove a narrower path")
        elif gap == "time":
            if found:
                res.add_deny(f"{full}: worktree scan did not finish within the guard's time budget")
            else:
                res.warn(f"could not finish scanning {full} for worktrees within the time budget")


def uncertain_relative(rel: str, res: Result, verb: str, session_cwd: str) -> None:
    needle = "/" + rel.strip("/") + "/"
    cands = [w for w in (res.registered(session_cwd) or []) if needle in w + "/"]
    if cands:
        for w in cands:
            res.add_deny(f"{w}: `{verb} {rel}` runs in a directory the guard cannot determine, "
                         "and this registered worktree matches it", w)
    else:
        res.warn(f"`{verb} {rel}` runs in a directory the guard cannot determine")


# ── command handlers ─────────────────────────────────────────────────────────

_ARG_OPTS = {
    "env": {"-u", "-C", "-S", "-P", "--unset", "--chdir", "--split-string"},
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-T", "-U", "--user", "--group"},
    "doas": {"-u", "-C"},
    "nice": {"-n", "--adjustment"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "exec": {"-a"},
    "time": {"-f", "-o"},
    "stdbuf": {"-i", "-o", "-e"},
    "nohup": set(), "command": set(), "builtin": set(), "caffeinate": {"-t", "-w"},
}


def strip_prefix(words: list[Word]) -> list[Word] | None:
    """Drop assignments and wrappers. None when the segment only queries (`command -v`)."""
    i = 0
    while i < len(words):
        w = words[i]
        if not w.dynamic and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w.value):
            i += 1
            continue
        name = os.path.basename(w.value)
        if name not in _ARG_OPTS:
            break
        opts = _ARG_OPTS[name]
        i += 1
        while i < len(words):
            v = words[i].value
            if v == "--":
                i += 1
                break
            if name == "command" and v in ("-v", "-V"):
                return None
            if name in ("env",) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", v):
                i += 1
                continue
            if v.startswith("-") and len(v) > 1:
                i += 2 if (v in opts) else 1
                continue
            break
        if name == "timeout" and i < len(words):
            i += 1  # the DURATION operand
    return words[i:]


def handle_git(args: list[Word], cwd: str | None, res: Result, session_cwd: str) -> None:
    i, eff = 0, cwd
    while i < len(args):
        v = args[i].value
        if v == "-C" and i + 1 < len(args):
            nxt = args[i + 1]
            if nxt.dynamic:
                eff = None
            else:
                p = os.path.expanduser(nxt.value)
                eff = p if os.path.isabs(p) else (os.path.join(eff, p) if eff else None)
            i += 2
            continue
        if v in ("-c", "--git-dir", "--work-tree", "--namespace", "--config-env") and i + 1 < len(args):
            i += 2
            continue
        if v.startswith("-"):
            i += 1
            continue
        break
    rest = args[i:]
    if len(rest) < 2 or rest[0].value != "worktree" or rest[1].value != "remove":
        return
    operands = [w for w in rest[2:] if not w.value.startswith("-") or w.dynamic]
    if not operands:
        return
    op = operands[0]
    if op.dynamic:
        res.warn(f"`git worktree remove` target {op.value} is not resolvable before the shell runs; "
                 "if it names a worktree, archive it first")
        return
    base = eff if eff is not None else session_cwd
    registered = res.registered(base)
    suffix = "/" + op.value.rstrip("/")
    cands: list[str] = []
    exact = os.path.realpath(os.path.join(eff, os.path.expanduser(op.value))) if eff else None
    if registered is not None:
        if exact and exact in registered:
            cands = [exact]
        else:
            cands = [w for w in registered if w.endswith(suffix) or w == op.value.rstrip("/")]
    elif exact and wa.linked_worktree_admin(exact):
        cands = [exact]
    else:
        res.warn(f"could not list worktrees for `git worktree remove {op.value}`; not checked")
    if eff is None and cands:
        for w in cands:
            res.add_deny(f"{w}: `git worktree remove {op.value}` runs in a directory the guard cannot "
                         "determine, and this registered worktree matches it", w)
        return
    for w in cands:
        res.removal(w, w)


def handle_rm(prog: str, args: list[Word], cwd, res: Result, session_cwd: str) -> None:
    recursive = prog != "rm"
    operands, done = [], False
    for w in args:
        v = w.value
        if not done and not w.dynamic and v == "--":
            done = True
        elif not done and not w.dynamic and v.startswith("--"):
            recursive |= v == "--recursive"
        elif not done and not w.dynamic and v.startswith("-") and len(v) > 1:
            recursive |= ("r" in v[1:] or "R" in v[1:])
        else:
            operands.append(w)
    if not recursive:
        return
    for w in operands:
        judge_root(w, cwd, res, prog, session_cwd)


def handle_xargs(args: list[Word], cwd, res: Result, session_cwd: str, upstream: list[Word]) -> None:
    i = 0
    with_arg = {"-n", "-I", "-L", "-P", "-d", "-E", "-s", "-J", "-R", "-S"}
    while i < len(args) and args[i].value.startswith("-"):
        i += 2 if args[i].value in with_arg else 1
    inner = strip_prefix(args[i:]) or []
    if not inner:
        return
    prog = os.path.basename(inner[0].value)
    if prog not in DELETERS:
        return
    flags = [w.value for w in inner[1:] if w.value.startswith("-")]
    if prog == "rm" and not any(("r" in f[1:] or "R" in f[1:]) and not f.startswith("--") or f == "--recursive"
                                for f in flags):
        return
    hit = False
    registered = res.registered(session_cwd) or []
    for w in upstream:
        if w.dynamic or w.value.startswith("-"):
            continue
        try:
            targets = resolve(w, cwd)
        except (Unresolved, UncertainCwd):
            continue
        for full, _ in targets:
            if os.path.islink(full) or not os.path.isdir(full):
                continue
            wts = ([full] if wa.linked_worktree_admin(full) else []) + [x for x in registered if _under(x, full)]
            for wt in wts:
                res.removal(wt, full)
                hit = True
    if not hit:
        res.warn(f"`xargs {prog}` takes its targets from stdin; the guard could not check them")


def handle_find(args: list[Word], cwd, res: Result, session_cwd: str) -> None:
    i = 0
    while i < len(args) and args[i].value in ("-H", "-L", "-P", "-E", "-X", "-s", "-x", "-d"):
        i += 1
    starts = []
    while i < len(args) and not (args[i].value.startswith("-") or args[i].value in ("(", "!", ")")):
        starts.append(args[i])
        i += 1
    expr = [w.value for w in args[i:]]
    deletes = "-delete" in expr or any(
        expr[k] in ("-exec", "-execdir", "-ok", "-okdir") and k + 1 < len(expr)
        and os.path.basename(expr[k + 1]) in DELETERS for k in range(len(expr)))
    if not deletes:
        return
    if not starts:
        dot = Word(); dot.value = "."
        starts = [dot]
    if any(tok in FIND_FILTERS for tok in expr):
        for w in starts:
            try:
                targets = resolve(w, cwd)
            except (Unresolved, UncertainCwd):
                continue
            for full, _ in targets:
                found, _gap = worktrees_under(full, res)
                for wt in found:
                    res.warn(f"`find ... -delete` with filters deletes matching files inside worktree {wt}; "
                             "archive it first if it holds state you need")
        return
    for w in starts:
        judge_root(w, cwd, res, "find -delete", session_cwd)


# ── driver ───────────────────────────────────────────────────────────────────

def _cd_target(args: list[Word], cwd: str | None, prog: str) -> str | None:
    if prog == "popd":
        return None
    operands, done = [], False
    for w in args:
        if not done and not w.dynamic and w.value == "--":
            done = True
        elif not done and not w.dynamic and w.value.startswith("-") and w.value != "-":
            continue
        else:
            operands.append(w)
    if not operands:
        return os.path.expanduser("~") if prog == "cd" else None
    w = operands[0]
    if w.dynamic or w.value == "-" or w.value.startswith("+"):
        return None
    try:
        full, _ = resolve(w, cwd)[0]
    except (Unresolved, UncertainCwd):
        return None
    full = os.path.realpath(full)
    return full if os.path.isdir(full) else None


def evaluate(cmd: str, cwd: str | None, res: Result, session_cwd: str, depth: int = 0) -> None:
    if depth > 4:
        return
    try:
        cleaned, bodies = strip_heredocs(cmd)
        toks = tokenize(cleaned)
    except ValueError as exc:
        if "worktree" in cmd or "rm" in cmd or "delete" in cmd:
            res.warn(f"could not parse the command to check for worktree removal ({exc})")
        return
    for host, body in bodies:
        try:
            host_toks = [t for t in tokenize(host) if isinstance(t, Word)]
        except ValueError:
            continue
        host_words = strip_prefix(host_toks) or []
        if (host_words and os.path.basename(host_words[0].value) in SHELLS
                and not any(w.value.startswith("-") and "c" in w.value for w in host_words[1:])):
            evaluate(body, cwd, res, session_cwd, depth + 1)

    stack: list[str | None] = []
    seg: list[Word] = []
    prev_op = ";"
    pipeline_words: list[Word] = []
    skip_next = False
    items = toks + [";"]
    for idx, tok in enumerate(items):
        if isinstance(tok, Word):
            if skip_next:
                skip_next = False
                continue
            seg.append(tok)
            continue
        if tok == "redir":
            skip_next = True
            continue
        next_op = tok
        if seg:
            cwd = run_segment(seg, cwd, res, session_cwd, prev_op, next_op, pipeline_words, depth)
            pipeline_words = (pipeline_words + seg) if next_op in ("|", "|&") else []
            seg = []
        if tok == "(":
            stack.append(cwd)
        elif tok == ")":
            if stack:
                cwd = stack.pop()
        if tok in SEPARATORS or tok == ";;":
            prev_op = tok
        if remaining() <= -1.0:  # hard stop well past budget
            res.warn("time budget exhausted while parsing")
            return


def run_segment(seg, cwd, res, session_cwd, prev_op, next_op, upstream, depth):
    words = [w for w in seg if not (not w.dynamic and w.value in ("{", "}", "!"))]
    words = strip_prefix(words)
    if not words:
        return cwd
    prog, args = os.path.basename(words[0].value), words[1:]
    if words[0].dynamic:
        return cwd
    local = prev_op in ("|", "|&") or next_op in ("|", "|&", "&")
    if prog in ("cd", "pushd", "popd"):
        return cwd if local else _cd_target(args, cwd, prog)
    if prog in SHELLS:
        cidx = next((k for k, a in enumerate(args[:-1])
                     if a.value.startswith("-") and not a.value.startswith("--") and "c" in a.value), None)
        if cidx is not None:
            evaluate(args[cidx + 1].value, cwd, res, session_cwd, depth + 1)
        return cwd
    if prog == "git":
        handle_git(args, cwd, res, session_cwd)
    elif prog in DELETERS:
        handle_rm(prog, args, cwd, res, session_cwd)
    elif prog == "xargs":
        handle_xargs(args, cwd, res, session_cwd, upstream)
    elif prog == "find":
        handle_find(args, cwd, res, session_cwd)
    return cwd


def verify(res: Result) -> None:
    """Second pass: every established removal needs a valid receipt."""
    for wt in sorted(res.removals):
        try:
            if remaining() <= 0:
                res.add_deny(f"{wt}: receipt not verified within the guard's time budget "
                             f"({BUDGET_S:g} s); re-run the command", wt)
                continue
            verdict, detail = wa.receipt_verdict(wt, deadline=DEADLINE)
        except Exception as exc:  # noqa: BLE001 — one bad receipt never clears another denial
            res.add_deny(f"{wt}: receipt could not be validated ({exc})", wt)
            continue
        if verdict == "ok":
            archive = os.path.realpath(detail)
            for root in sorted(res.roots | {wt}):
                if _under(archive, root):
                    res.add_deny(f"{wt}: this command would also delete its archive {detail}; "
                                 "move the archive out first")
                    break
        elif verdict == "git_error":
            res.warn(f"could not read the state of {wt} ({detail}); removal allowed unverified")
        elif verdict == "timeout":
            res.add_deny(f"{wt}: {detail} ({BUDGET_S:g} s); re-run the command", wt)
        else:
            res.add_deny(f"{wt}: {detail}", wt)


def _command_from_event(event: dict) -> str:
    ti = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    cmd = ti.get("command", event.get("command", ""))
    if isinstance(cmd, list):
        if len(cmd) >= 3 and os.path.basename(str(cmd[0])) in SHELLS and str(cmd[1]).startswith("-"):
            return str(cmd[-1])
        return shlex.join(str(c) for c in cmd)
    return str(cmd or "")


def render(res: Result, host: str) -> dict:
    if res.deny:
        lines = ["Blocked: this command deletes a git worktree, including git-IGNORED files "
                 "(.build-loop/, .bookmark/, .ibr/, ...) that `git status` does not show."]
        lines += [f"- {d}" for d in res.deny]
        if res.need_archive:
            lines.append("Archive first (one step; regenerable caches are listed, not archived):")
            lines.append(f"  {wa.archive_command(res.need_archive, PLUGIN_ROOT)}")
            lines.append(f"then re-run the same command within {wa.RECEIPT_TTL_S // 60} minutes, "
                         "without changing the worktree in between.")
        if res.warnings:
            lines.append("Also: " + "; ".join(res.warnings))
        reason = "\n".join(lines)
        if host == "cursor":
            return {"permission": "deny", "userMessage": reason, "agentMessage": reason}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}
    if res.warnings:
        msg = "[build-loop worktree guard] " + "; ".join(res.warnings)
        return {"permission": "allow", "userMessage": msg} if host == "cursor" else {"systemMessage": msg}
    return {"permission": "allow"} if host == "cursor" else {}


def main(argv: list[str]) -> int:
    host = "claude"
    if "--host" in argv and argv[argv.index("--host") + 1:][:1] == ["cursor"]:
        host = "cursor"
    res = Result()
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        cmd = _command_from_event(event)
        if cmd:
            cwd = str(event.get("cwd") or "") or os.getcwd()
            evaluate(cmd, cwd, res, cwd)
            verify(res)
    except Exception as exc:  # noqa: BLE001 — fail open visibly; established denials survive
        res.warn(f"guard error, removal not fully checked: {exc}")
        for wt in res.removals:
            if not any(d.startswith(wt + ":") for d in res.deny):
                res.add_deny(f"{wt}: removal established but the guard failed before verifying it", wt)
    out = render(res, host)
    if res.warnings:
        print("; ".join(res.warnings), file=sys.stderr)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
