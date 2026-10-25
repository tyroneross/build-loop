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

Two detection layers:
  - a precise parser for the common cases (quoting kept, heredocs and quoted
    globs are data, subshells scope `cd`, reserved words and brace expansion
    handled, symlink operands judge the link, shell writes into a worktree
    recorded);
  - a fail-closed text backstop: when a level of the command holds more
    deletion verbs than the parser handled, or a deletion it could not
    resolve, any registered worktree whose path, unique suffix or basename
    appears in the text counts as removed. It covers command substitution,
    unparseable input, and whatever shell syntax the parser does not model.

Failure policy:
  - CLOSED once a worktree removal is established: no receipt, an invalid or
    stale one, an incomplete listing, a git error, or a timeout all deny, and
    so does a removal that writes into the worktree first or would delete the
    receipt's archive. Also closed when a relative removal runs in an unknown
    directory and a registered worktree matches it, or when the ancestor scan
    cannot prove no worktree lies beneath its 50,000-directory budget.
  - OPEN, with a visible `systemMessage`, when no removal is established: a
    `$VAR` target, a git listing failure, or the 2 s budget running out first.
    Denials already established survive every failure path, including a crash.

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
from collections import Counter
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


RESERVED = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "case", "esac",
            "in", "select", "function", "!", "{", "}", "time", "coproc"}
WRITERS_ALL = {"tee", "touch", "mkdir"}             # every operand is written
WRITERS_LAST = {"cp", "mv", "ln", "install", "rsync", "ditto"}  # last operand (or -t DIR)

# Backstop: deletion verbs, counted on text whose multi-word quoted strings are
# removed (data, commit messages, `bash -c` scripts judged at their own level).
DELETION_RES = {
    "rm": re.compile(r"(?:^|[\s;&|()`/])rm\s+(?:[^;&|\n]*?\s)?(?:-[A-Za-z]*[rR][A-Za-z]*|--recursive)(?=[\s;&|)]|$)",
                     re.M),
    "trash": re.compile(r"(?:^|[\s;&|()`/])(?:trash|safe-del(?:ete)?)(?=[\s;&|)]|$)", re.M),
    "wtremove": re.compile(r"\bworktree\s+remove\b"),
    "delete": re.compile(r"(?:^|\s)-delete(?=[\s;&|)]|$)", re.M),
}


ANY_DELETE_RE = re.compile(r"(?:^|[\s;&|()`/])(?:rm|unlink|trash|safe-del(?:ete)?)(?=[\s;&|)]|$)|-delete\b",
                           re.M)


def remaining() -> float:
    return DEADLINE - time.monotonic()


class Unresolved(Exception):
    pass


class UncertainCwd(Exception):
    pass


class Word:
    __slots__ = ("value", "glob", "dynamic", "brace")

    def __init__(self) -> None:
        self.value, self.glob, self.dynamic, self.brace = "", False, False, False


def literal(value: str) -> Word:
    w = Word()
    w.value = value
    return w


# ── text helpers ─────────────────────────────────────────────────────────────

def strip_heredocs(cmd: str) -> tuple[str, list[tuple[str, str]]]:
    """Remove heredoc bodies (data, not commands), honouring quotes so that
    `echo '<<EOF'` opens nothing. Returns (cleaned, [(host_line, body)])."""
    s = cmd.replace("\r", "\n")
    out: list[str] = []
    bodies: list[tuple[str, str]] = []
    pending: list[tuple[str, bool, str]] = []
    i, n, q, line_start = 0, len(s), None, 0
    while i < n:
        c = s[i]
        if q:
            out.append(c)
            if q == '"' and c == "\\" and i + 1 < n:
                out.append(s[i + 1]); i += 2; continue
            if c == q:
                q = None
            i += 1; continue
        if c == "\\" and i + 1 < n:
            out.append(c); out.append(s[i + 1]); i += 2; continue
        if c in "'\"":
            q = c; out.append(c); i += 1; continue
        if c == "<" and s.startswith("<<", i) and not s.startswith("<<<", i) and (i == 0 or s[i - 1] != "<"):
            m = re.match(r"<<(-?)[ \t]*(['\"]?)([A-Za-z0-9_.\-]+)\2", s[i:])
            if m:
                pending.append((m.group(3), m.group(1) == "-", "".join(out[line_start:])))
                out.append(" "); i += m.end(); continue
        if c == "\n":
            out.append("\n"); i += 1
            for delim, tabs, host in pending:
                body = []
                while i < n:
                    j = s.find("\n", i)
                    j = n if j < 0 else j
                    line, i = s[i:j], min(j + 1, n)
                    if (line.lstrip("\t") if tabs else line) == delim:
                        break
                    body.append(line)
                bodies.append((host, "\n".join(body)))
            pending, line_start = [], len(out)
            continue
        out.append(c); i += 1
    return "".join(out), bodies


def counting_text(s: str) -> str:
    """Text for the backstop: quoted strings holding whitespace dropped, other
    quotes and escapes removed, braces and commas split into words. A command
    substitution inside double quotes still runs, and its boundaries are hard
    to find (quoted parens, escaped backticks), so from the first `$(` or
    backtick in a double-quoted string the rest of the text is kept whole,
    quote characters removed: its deletions count and quoted paths survive."""
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c in "'\"":
            j = i + 1
            while j < n and s[j] != c:
                if c == '"' and (s.startswith("$(", j) or s[j] == "`"):
                    out.append(" ; " + re.sub(r"[\"'\\]", "", s[j:]) + " ")
                    return "".join(out)
                j += 2 if (c == '"' and s[j] == "\\") else 1
            inner = s[i + 1:j]
            out.append(" " if re.search(r"\s", inner) else inner.replace("\\", ""))
            i = j + 1; continue
        if c == "\\":
            out.append(s[i + 1:i + 2]); i += 2; continue
        out.append(" " if c in "{}," else c)
        i += 1
    return "".join(out)


def brace_expand(value: str) -> list[str]:
    m = re.search(r"\{([^{}]*,[^{}]*)\}", value)
    if not m:
        return [value]
    out: list[str] = []
    for part in m.group(1).split(","):
        out += brace_expand(value[:m.start()] + part + value[m.end():])
    return out[:64]


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
    """Shell-ish tokens: Word objects and operator strings ('redir'/'redir&')."""
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
            op = s[i:j]
            toks.append("herestr" if op == "<<<" else ("redir&" if op.endswith("&") else "redir")); i = j; continue
        if c in ";&|()":
            flush()
            two = s[i:i + 2]
            if two in ("&&", "||", "|&", ";;"):
                toks.append(two); i += 2; continue
            toks.append(c); i += 1; continue
        if c in "*?[":
            word().glob = True
        if c == "{":
            word().brace = True
        word().value += c
        i += 1
    flush()
    return toks


# ── evaluation state ─────────────────────────────────────────────────────────

class Result:
    def __init__(self, session_cwd: str) -> None:
        self.session_cwd = session_cwd
        self.deny: list[str] = []
        self.warnings: list[str] = []
        self.need_archive: list[str] = []
        self.removals: dict[str, set[str]] = {}   # worktree -> deletion roots
        self.roots: set[str] = set()              # directories this command deletes
        self.deleted: set[str] = set()            # every deletion operand, files included
        self.writes: set[str] = set()             # paths this command writes to
        self.texts: list[str] = []                # every level's backstop text
        self.counter: Counter = Counter()         # deletions the parser handled, this level
        self.uncertain = 0                        # deletions the parser could not resolve
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
        if remaining() > 0:
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
    out = []
    for value in (brace_expand(word.value) if word.brace else [word.value]):
        v = os.path.expanduser(value) if value.startswith("~") else value
        if not os.path.isabs(v):
            if cwd is None:
                raise UncertainCwd(value)
            v = os.path.join(cwd, v)
        for p in ((sorted(glob.glob(v)) or [v]) if word.glob else [v]):
            trailing = p.endswith("/") and p != "/"
            norm = os.path.normpath(p)
            full = (os.path.join(os.path.realpath(os.path.dirname(norm)), os.path.basename(norm))
                    if norm != "/" else "/")
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
            if scanned % 64 == 0 and remaining() <= 0:
                return found, "time"
            path = e.path
            if os.path.isfile(os.path.join(path, ".git")) and wa.linked_worktree_admin(path):
                found.append(os.path.realpath(path))
            if wa.is_cache_root(Path(path)):
                continue
            stack.append(path)
    return found, None


def record_deleted(word: Word, cwd: str | None, res: Result) -> None:
    try:
        for full, _ in resolve(word, cwd):
            res.deleted.add(full)
    except (Unresolved, UncertainCwd):
        pass


def judge_root(word: Word, cwd: str | None, res: Result, verb: str) -> None:
    """A recursive deletion operand: find the worktrees it would remove."""
    try:
        targets = resolve(word, cwd)
    except Unresolved as exc:
        res.uncertain += 1
        res.warn(f"`{verb}` target {exc} is not resolvable before the shell runs; "
                 "if it names a worktree, archive it first")
        return
    except UncertainCwd as exc:
        uncertain_relative(str(exc), res, verb)
        return
    for full, _trailing in targets:
        res.deleted.add(full)
        if not os.path.lexists(full):
            res.uncertain += 1  # nothing there: maybe misresolved, let the backstop look
            continue
        if os.path.islink(full):
            continue  # rm/trash remove the link itself
        if wa.is_cache_root(Path(full)):
            continue  # a verified regenerable cache root holds no worktree we track
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
                res.uncertain += 1
                res.warn(f"could not finish scanning {full} for worktrees within the time budget")


def uncertain_relative(rel: str, res: Result, verb: str) -> None:
    needle = "/" + rel.strip("/") + "/"
    cands = [w for w in (res.registered(res.session_cwd) or []) if needle in w + "/"]
    if cands:
        for w in cands:
            res.add_deny(f"{w}: `{verb} {rel}` runs in a directory the guard cannot determine, "
                         "and this registered worktree matches it", w)
    else:
        res.uncertain += 1
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


def strip_prefix(words: list[Word], info: dict | None = None) -> list[Word] | None:
    """Drop assignments and wrappers. None when the segment only queries
    (`command -v`). `info["chdir"]` receives a directory from `env -C`."""
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
            if name == "env" and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", v):
                i += 1
                continue
            if name == "env" and info is not None and (v in ("-C", "--chdir") or v.startswith("--chdir=")):
                arg = words[i + 1] if v in ("-C", "--chdir") and i + 1 < len(words) else literal(v[8:])
                info["chdir"] = None if arg.dynamic else arg.value
            if v.startswith("-") and len(v) > 1:
                i += 2 if (v in opts) else 1
                continue
            break
        if name == "timeout" and i < len(words):
            i += 1  # the DURATION operand
    return words[i:]


def handle_git(args: list[Word], cwd: str | None, res: Result) -> None:
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
    res.counter["wtremove"] += 1
    operands = [w for w in rest[2:] if not w.value.startswith("-") or w.dynamic]
    if not operands:
        return
    op = operands[0]
    if op.dynamic:
        res.uncertain += 1
        res.warn(f"`git worktree remove` target {op.value} is not resolvable before the shell runs; "
                 "if it names a worktree, archive it first")
        return
    base = eff if eff is not None else res.session_cwd
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
        res.uncertain += 1
        res.warn(f"could not list worktrees for `git worktree remove {op.value}`; not checked")
    if eff is None and cands:
        for w in cands:
            res.add_deny(f"{w}: `git worktree remove {op.value}` runs in a directory the guard cannot "
                         "determine, and this registered worktree matches it", w)
        return
    for w in cands:
        res.removal(w, w)


def _is_recursive(flags: list[str]) -> bool:
    return any(f == "--recursive" or (f.startswith("-") and not f.startswith("--") and ("r" in f or "R" in f))
               for f in flags)


def handle_rm(prog: str, args: list[Word], cwd, res: Result) -> None:
    operands, flags, done = [], [], False
    for w in args:
        v = w.value
        if not done and not w.dynamic and v == "--":
            done = True
        elif not done and not w.dynamic and v.startswith("-") and len(v) > 1:
            flags.append(v)
        else:
            operands.append(w)
    recursive = prog != "rm" or _is_recursive(flags)
    if not recursive:
        for w in operands:
            record_deleted(w, cwd, res)
        return
    res.counter["rm" if prog == "rm" else "trash"] += 1
    for w in operands:
        judge_root(w, cwd, res, prog)


def handle_xargs(args: list[Word], cwd, res: Result, upstream: list[Word]) -> None:
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
    explicit = [w for w in inner[1:] if not w.value.startswith("-") or w.dynamic]
    if prog == "rm" and not _is_recursive(flags):
        for w in explicit:
            record_deleted(w, cwd, res)
        return
    res.counter["rm" if prog == "rm" else "trash"] += 1
    for w in explicit:
        judge_root(w, cwd, res, f"xargs {prog}")
    hit = bool(explicit)
    registered = res.registered(res.session_cwd) or []
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
        res.uncertain += 1
        res.warn(f"`xargs {prog}` takes its targets from stdin; the guard could not check them")


def handle_find(args: list[Word], cwd, res: Result) -> None:
    i = 0
    while i < len(args) and args[i].value in ("-H", "-L", "-P", "-E", "-X", "-s", "-x", "-d"):
        i += 1
    starts = []
    while i < len(args) and not (args[i].value.startswith("-") or args[i].value in ("(", "!", ")")):
        starts.append(args[i])
        i += 1
    expr = [w.value for w in args[i:]]
    delete = "-delete" in expr
    exec_rm = [k for k in range(len(expr) - 1)
               if expr[k] in ("-exec", "-execdir", "-ok", "-okdir") and os.path.basename(expr[k + 1]) in DELETERS]
    def exec_flags(k: int) -> list[str]:
        out = []
        for t in expr[k + 2:]:
            if t in (";", "+"):
                break
            if t.startswith("-"):
                out.append(t)
        return out

    exec_recursive = any(os.path.basename(expr[k + 1]) != "rm" or _is_recursive(exec_flags(k)) for k in exec_rm)
    if delete:
        res.counter["delete"] += expr.count("-delete")
    if exec_rm and exec_recursive:
        res.counter["rm"] += 1
    if not starts:
        starts = [literal(".")]
    full_removal = (delete or (exec_rm and exec_recursive)) and not any(t in FIND_FILTERS for t in expr)
    if full_removal:
        for w in starts:
            judge_root(w, cwd, res, "find -delete")
        return
    if delete or exec_rm:
        for w in starts:
            try:
                targets = resolve(w, cwd)
            except (Unresolved, UncertainCwd):
                continue
            for full, _ in targets:
                found, _gap = worktrees_under(full, res)
                for wt in found:
                    res.warn(f"`find` deletes files (not the worktree itself) inside {wt}; "
                             "archive it first if it holds state you need")


def record_writes(prog: str, args: list[Word], cwd, res: Result) -> None:
    operands = [w for w in args if not (w.value.startswith("-") and not w.dynamic)]
    if prog in WRITERS_ALL:
        targets = operands
    else:
        tdir = next((args[k + 1] for k in range(len(args) - 1) if args[k].value in ("-t", "--target-directory")),
                    None)
        targets = [tdir] if tdir else operands[-1:]
    for w in targets:
        try:
            for full, _ in resolve(w, cwd):
                res.writes.add(full)
        except (Unresolved, UncertainCwd):
            pass


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


def backstop(text: str, cwd: str | None, res: Result, handled: Counter, uncertain: bool) -> None:
    """Fail-closed text check for what the parser may have missed.

    Fires when this level's text holds more deletion verbs than the parser
    handled, or a deletion the parser could not resolve. Any registered
    worktree whose path, unique suffix or basename appears in the text then
    counts as removed and must carry a valid receipt.
    """
    ct = counting_text(text)
    res.texts.append(ct)
    need = {k: len(r.findall(ct)) for k, r in DELETION_RES.items()}
    if not any(need.values()):
        return
    if not uncertain and all(handled[k] >= n for k, n in need.items()):
        return
    words = [w for w in re.split(r"[\s;&|()<>`]+", ct) if w]
    base_dir = cwd or res.session_cwd
    regs = set(res.registered(base_dir) or [])
    probed = 0
    for w in words:
        if "/" not in w or w.startswith("-") or "$" in w:
            continue
        q = os.path.abspath(os.path.join(base_dir, os.path.expanduser(w)))
        while q not in ("/", "") and not os.path.isdir(q):
            q = os.path.dirname(q)
        if q in ("/", ""):
            continue
        if wa.linked_worktree_admin(q):
            regs.add(os.path.realpath(q))
        if probed < 6:
            probed += 1
            regs.update(res.registered(q) or [])
    for wt in sorted(regs):
        if wt in res.removals:
            continue
        base = os.path.basename(wt)
        hit = wt in ct
        for w in words:
            ws = w.rstrip("/")
            if ws == base or ws.endswith("/" + base) or ("/" in ws and wt.endswith("/" + ws.lstrip("./"))):
                hit = True
                break
        if hit:
            res.removal(wt, wt)


def evaluate(cmd: str, cwd: str | None, res: Result, depth: int = 0) -> None:
    if depth > 4:
        return
    saved_counter, res.counter = res.counter, Counter()
    uncertain_before = res.uncertain
    cleaned = cmd
    try:
        try:
            cleaned, bodies = strip_heredocs(cmd)
            toks = tokenize(cleaned)
        except ValueError as exc:
            res.uncertain += 1
            if re.search(r"rm|worktree|delete|trash|safe-del", cmd):
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
                evaluate(body, cwd, res, depth + 1)
        _walk_tokens(toks, cwd, res, depth)
    finally:
        handled, res.counter = res.counter, saved_counter
        backstop(cleaned, cwd, res, handled, res.uncertain > uncertain_before)


def _walk_tokens(toks: list, cwd: str | None, res: Result, depth: int) -> None:
    stack: list[str | None] = []
    seg: list[Word] = []
    prev_op = ";"
    pipeline_words: list[Word] = []
    redir: str | None = None
    here: list[str] = []
    for tok in toks + [";"]:
        if isinstance(tok, Word):
            if redir == "herestr":
                here.append(tok.value)
                redir = None
                continue
            if redir is not None:
                if not (redir == "redir&" and (tok.value.isdigit() or tok.value == "-")):
                    try:
                        for full, _ in resolve(tok, cwd):
                            res.writes.add(full)
                    except (Unresolved, UncertainCwd):
                        pass
                redir = None
                continue
            seg.append(tok)
            continue
        if tok in ("redir", "redir&", "herestr"):
            redir = tok
            continue
        next_op = tok
        if seg:
            cwd = run_segment(seg, cwd, res, prev_op, next_op, pipeline_words, depth, here)
            pipeline_words = (pipeline_words + seg) if next_op in ("|", "|&") else []
            seg, here = [], []
        if tok == "(":
            stack.append(cwd)
        elif tok == ")":
            if stack:
                cwd = stack.pop()
        if tok in SEPARATORS or tok == ";;":
            prev_op = tok
        if remaining() <= -1.0:
            res.warn("time budget exhausted while parsing")
            return


def run_segment(seg, cwd, res, prev_op, next_op, upstream, depth, here=()):
    words = list(seg)
    while words and not words[0].dynamic and words[0].value in RESERVED:
        words = words[1:]
    info: dict = {}
    words = strip_prefix(words, info)
    if not words or words[0].dynamic:
        return cwd
    eff = cwd
    if "chdir" in info:
        d = info["chdir"]
        eff = None if d is None else (_cd_target([literal(d)], cwd, "cd") if d else cwd)
    prog, args = os.path.basename(words[0].value), words[1:]
    local = prev_op in ("|", "|&") or next_op in ("|", "|&", "&")
    if prog in ("cd", "pushd", "popd"):
        return cwd if local else _cd_target(args, cwd, prog)
    if prog in SHELLS or prog == "eval":
        if prog == "eval":
            evaluate(" ".join(w.value for w in args), eff, res, depth + 1)
            return cwd
        cidx = next((k for k, a in enumerate(args[:-1])
                     if a.value.startswith("-") and not a.value.startswith("--") and "c" in a.value), None)
        if cidx is not None:
            evaluate(args[cidx + 1].value, eff, res, depth + 1)
        else:
            for text in here:  # `bash <<< "script"`
                evaluate(text, eff, res, depth + 1)
        return cwd
    if prog == "git":
        handle_git(args, eff, res)
    elif prog in DELETERS:
        handle_rm(prog, args, eff, res)
    elif prog == "xargs":
        handle_xargs(args, eff, res, upstream)
    elif prog == "find":
        handle_find(args, eff, res)
    elif prog in WRITERS_ALL or prog in WRITERS_LAST:
        record_writes(prog, args, eff, res)
    return cwd


def verify(res: Result) -> None:
    """Second pass: every established removal needs a valid receipt."""
    for wt in sorted(res.removals):
        writes = sorted(w for w in res.writes if _under(w, wt))
        if writes:
            res.add_deny(f"{wt}: this command writes into the worktree ({writes[0]}) before removing it; "
                         "run the write first, then archive, then remove", wt)
            continue
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
            doomed = sorted(res.roots | res.deleted | {wt})
            hit = next((p for p in doomed if archive == p or _under(archive, p)), None)
            # Text-level backstop: the archive's name beside any deletion verb.
            name = os.path.basename(archive)
            if hit is None and any(name in t and ANY_DELETE_RE.search(t) for t in res.texts):
                hit = name
            if hit is not None:
                res.add_deny(f"{wt}: this command would also delete its archive {detail} (via {hit}); "
                             "keep the archive out of the command")
        elif verdict == "timeout":
            res.add_deny(f"{wt}: {detail} ({BUDGET_S:g} s); re-run the command", wt)
        elif verdict == "git_error":
            res.add_deny(f"{wt}: git could not read this worktree to verify its receipt ({detail})", wt)
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
    res = Result(os.getcwd())
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        cmd = _command_from_event(event)
        if cmd:
            cwd = str(event.get("cwd") or "") or os.getcwd()
            res.session_cwd = cwd
            evaluate(cmd, cwd, res)
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
