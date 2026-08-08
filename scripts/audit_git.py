#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""audit_git.py — Read-only git front door for read-only agents (e.g. independent-auditor).

Incident this closes: `agents/independent-auditor.md` declares
`tools: ["Read", "Grep", "Glob", "Bash"]` — "read-only" is DECLARED in prose
but never ENFORCED, because Bash lets the agent run *any* git subcommand.
In the observed incident the auditor ran `git checkout -- <file>` mid-audit
and destroyed an implementer's uncommitted work.

This script is the enforcement point. It is an ALLOWLIST, not a deny-list:
every git subcommand is refused by default, and only a fixed set of
read-only subcommands (and only their read-only forms/flags) are let
through to a real `git` invocation. `checkout`, `restore`, `reset`, `clean`,
`commit`, `add`, `rm`, `push`, `merge`, `rebase`, etc. are refused because
they are simply never on the allowlist — not because someone remembered to
name them. An unrecognized/future subcommand (e.g. `git restore`, a
made-up `git frobnicate`) is refused for the same reason: unknown means
refused, never means allowed.

Refuse-by-default at SUBCOMMAND granularity is not enough (audit finding
F1, 2026-08-07). A "read-only" subcommand can still write a file or spawn
a process through its own FLAGS, and the flags are shared across the
`log`/`show`/`diff` family:

    diff --output=<file>          truncates then writes <file>
    log  --output=<file>          same — reproduced the original incident
    grep -O<cmd> / --open-files-in-pager=<cmd>   runs <cmd>
    diff --ext-diff               runs the gitattributes external diff driver
    git -c core.pager=<cmd> log   config injection -> process spawn
    git --git-dir=<other> log     escapes the --repo confinement

So classify() applies three checks BEFORE handler dispatch:

1. The subcommand token must look like a subcommand (`[a-z0-9-]+`), which
   also catches a whole shell string passed as one argv element.
2. Nothing may precede the subcommand. Every pre-subcommand global option
   (`-c`, `-C`, `--config-env`, `--git-dir`, `--work-tree`, `--namespace`,
   `--exec-path`, `--paginate`, `--bare`, `--attr-source`, ...) is refused
   as a class; several of them reach arbitrary execution or move the repo
   out from under `--repo`, and none of them are needed to read.
3. Every remaining token is matched against `_DENIED_FLAGS` by FLAG
   IDENTITY, not by set membership — `--output=/x` is a single token, so
   `flag in argv` never sees it. Long flags match `--flag` or `--flag=…`;
   short flags match as a prefix because their values are stuck (`-Ocmd`).
   Matching on identity is what keeps `--output-indicator-new=X`,
   `--output-object-format=sha1`, `--no-ext-diff` and `ls-files -o`
   allowed while `--output`/`-O`/`--ext-diff` are refused.

There is no shell-metacharacter filter, deliberately. git is invoked with
`shell=False` (see main()), so `;`, `|`, `$(` and friends are inert bytes
in an argv element — the filter could not prevent anything, while it DID
block legitimate reads the auditor needs (`log --pretty=format:'%H|%s'`,
`log --grep='a|b'`, a pathspec containing `<`/`>`). A gate that blocks
real reads is a gate that gets switched off, so it was removed in favour
of the flag scan above, which stops the actual execution paths. The one
residue kept is check 1: it only inspects the subcommand token, where a
metacharacter can only mean the caller passed a shell string by mistake.

Known residual risk (NOT closed by any flag scan): git honours the
AUDITED repo's own `.git/config` and `.gitattributes`, so `diff.<drv>.command`,
`diff.<drv>.textconv` and `core.pager` can execute code from inside a
hostile repository with no flag involved. `GIT_PAGER=cat` is forced below
to close the pager half. The diff-driver half is a property of running
git at all against untrusted content and needs a sandbox, not an argv
check.

Read-only agents must route every git call through this script instead of
calling bare `git`. Bash stays available to them for `python3
scripts/audit_git.py ...` invocations; bare `git ...` is prohibited by
convention in the agent's own instructions (see agents/independent-auditor.md).

Usage
-----
    audit_git.py [--repo <path>] [--json] -- <git args...>
    audit_git.py <git args...>                      # ergonomic form

On allow: execs `git [-C <repo>] <git args...>`, streams stdout/stderr
through untouched, and exits with git's own return code.

On refuse: never invokes git. Writes a one-line reason to stderr (or a
JSON object to stderr if --json was given) naming the refused
subcommand/flag and a read-only alternative, and exits 2.

`classify(argv) -> dict` is importable and side-effect free — it never
shells out. It returns `{"allowed": bool, "subcommand": str | None,
"reason": str | None, "reason_code": str | None}`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Sequence

# ---------------------------------------------------------------------------
# Flag-level guard (F1) — applies before any per-subcommand handler.
# ---------------------------------------------------------------------------

# Flags that write a file or spawn a process. They ride in on subcommands
# that ARE on the read-only allowlist, so the subcommand allowlist alone
# never sees them. Verified against git 2.50.1 man pages.
_DENIED_FLAGS = (
    # --- writes a file (truncate + write) ---
    "--output",             # diff/log/show: --output=<file>  [the F1 exploit]
    "--to-file",            # format-patch family; kept for future allowlisting
    "--index-output",       # read-tree family; writes an index file
    # --- spawns a process ---
    "-O",                   # grep: -O[<pager>] runs the pager (exec).
                            # Collateral: diff/log -O<orderfile> (a read) is
                            # also refused; orderfile diffs are not worth a
                            # second, subcommand-aware code path.
    "--open-files-in-pager",  # grep: long form of -O
    "--ext-diff",           # runs the gitattributes external diff driver
    "--textconv",           # runs the gitattributes textconv filter
    "--help",               # spawns the man/help viewer, which honours the
                            # repo's own man.<tool>.cmd / help.format config
    "--exec",               # bisect/submodule family: runs a command
    "--upload-pack",        # runs a command on the far side
    "--receive-pack",       # runs a command on the far side
)

# The ONE place the scan skips a token: `git grep -e <pattern>` / `-f <file>`.
# grep REQUIRES a value there, so git consumes the next token as DATA and
# never re-parses it as an option — which is what makes `git grep -e -Ofast`
# (searching for a compiler flag; a real read an auditor performs) safe to
# allow. Verified on git 2.50.1: with `-e`/`-f` in front, neither
# `--output=<file>` nor `-O<cmd>` is honoured by grep.
#
# Keyed by SUBCOMMAND on purpose. An earlier revision of this fix skipped
# after `-e`/`-f` for every subcommand and thereby re-opened the exploit:
# `git log -e --output=victim` and `git diff -f --output=victim` are parse
# ERRORS (exit 128/129), but git truncates the output file BEFORE failing —
# an error exit code is not a safe outcome. Only add a subcommand/option
# pair here after checking that git really consumes the next token as data.
_VALUE_TAKING_OPTIONS = {"grep": frozenset({"-e", "-f"})}


def _flag_matches(token: str, flag: str) -> bool:
    """Match a token against a flag by IDENTITY, not by substring.

    Long flags match `--flag` and `--flag=<value>` — so `--output=/tmp/x`
    is caught while `--output-indicator-new=X` is not. Short flags match
    as a prefix, because git sticks their values to them (`-Otouch /x`).
    """
    if flag.startswith("--"):
        return token == flag or token.startswith(flag + "=")
    return token.startswith(flag)


def _denied_flag_hit(subcommand: str, rest: Sequence[str]) -> str | None:
    """Return the first denied flag token in `rest`, or None.

    Stops at the `--` separator: everything after it is a pathspec, which
    git never parses as an option.
    """
    value_taking = _VALUE_TAKING_OPTIONS.get(subcommand, frozenset())
    skip_next = False
    for token in rest:
        if skip_next:
            skip_next = False
            continue
        if token == "--":
            return None
        if token in value_taking:
            skip_next = True
            continue
        if not token.startswith("-") or token == "-":
            continue
        for flag in _DENIED_FLAGS:
            if _flag_matches(token, flag):
                return token
    return None


# A handful of flags that are unambiguously destructive no matter which
# (allowlisted) subcommand they ride in on. Defense in depth on top of the
# per-subcommand rules below — most of these already can't reach a real git
# subcommand call because the subcommands that use them (reset, checkout,
# apply) simply aren't on the allowlist.
_GLOBAL_MUTATING_FLAGS = ("--hard", "--write-tree")

# Subcommands that are unambiguously mutating/dangerous and are refused by
# the allowlist's default-deny — this table exists only to give a helpful,
# specific refusal message (naming the read-only alternative) instead of a
# generic "not on the allowlist" string. Removing an entry from this table
# does NOT allow the subcommand; only adding it to _HANDLERS would.
_KNOWN_MUTATING_HINTS = {
    "checkout": "use `show <ref>:<path>` to read a file at a revision",
    "checkout-index": "use `show <ref>:<path>` to read a file at a revision",
    "restore": "use `show <ref>:<path>` to read a file at a revision",
    "switch": "use `rev-parse --abbrev-ref HEAD` or `log <branch>` to inspect branches without switching to them",
    "reset": "use `diff` or `log` to inspect changes without moving HEAD",
    "clean": "use `status --short` to see untracked files without deleting them",
    "commit": "auditors report findings; they do not create commits",
    "add": "auditors do not stage files",
    "rm": "use `ls-files` to inspect tracked files without deleting them",
    "mv": "auditors do not move/rename files",
    "push": "auditors do not push",
    "pull": "use `log`/`diff` against the local refs; auditors do not fetch+merge",
    "fetch": "use existing local refs; auditors do not talk to remotes",
    "merge": "auditors do not merge",
    "rebase": "auditors do not rebase",
    "cherry-pick": "auditors do not cherry-pick",
    "revert": "auditors do not revert",
    "apply": "auditors do not apply patches to the working tree or index",
    "am": "auditors do not apply mailboxes",
    "filter-branch": "auditors do not rewrite history",
    "gc": "auditors do not run maintenance that can prune objects",
    "prune": "auditors do not prune objects",
    "reflog": "use `log --walk-reflogs` is also refused; use `log` on real refs instead",
    "update-ref": "auditors do not move refs",
    "notes": "auditors do not attach notes",
    "init": "auditors do not initialize repos",
    "clone": "auditors do not clone repos",
    "submodule": "auditors do not update submodules",
    "filter-repo": "auditors do not rewrite history",
}


def _allow(subcommand: str) -> dict:
    return {"allowed": True, "subcommand": subcommand, "reason": None, "reason_code": None}


def _refuse(subcommand: str | None, code: str, message: str) -> dict:
    return {"allowed": False, "subcommand": subcommand, "reason": message, "reason_code": code}


def _refuse_known(subcommand: str) -> dict:
    hint = _KNOWN_MUTATING_HINTS.get(subcommand)
    if hint:
        message = f"`{subcommand}` refused — read-only auditor; {hint}"
    else:
        message = (
            f"`{subcommand}` refused — not on the read-only allowlist; "
            "only read-only git subcommands are permitted"
        )
    return _refuse(subcommand, "unknown_or_unlisted_subcommand", message)


def _flags_and_operands(rest: Sequence[str]) -> tuple[set, list]:
    flags = {a for a in rest if a.startswith("-") and a != "-"}
    operands = [a for a in rest if not (a.startswith("-") and a != "-")]
    return flags, operands


# ---------------------------------------------------------------------------
# Per-subcommand handlers. Each takes (subcommand, rest_args) and returns a
# classify()-shaped dict. `rest` excludes the subcommand token itself.
# ---------------------------------------------------------------------------

def _simple_readonly(subcommand: str, rest: Sequence[str]) -> dict:
    """Subcommands with no write mode at all: allow unconditionally."""
    return _allow(subcommand)


def _symbolic_ref(subcommand: str, rest: Sequence[str]) -> dict:
    flags, operands = _flags_and_operands(rest)
    if flags & {"-d", "--delete"}:
        return _refuse(
            subcommand,
            "symbolic_ref_delete",
            "`symbolic-ref --delete` refused — read-only auditor; "
            "use `for-each-ref` to read symbolic refs without deleting them",
        )
    if len(operands) >= 2:
        return _refuse(
            subcommand,
            "symbolic_ref_write_form",
            "`symbolic-ref <ref> <value>` refused — that form writes the ref; "
            "use `symbolic-ref --short HEAD` (one operand) to read it",
        )
    return _allow(subcommand)


def _config(subcommand: str, rest: Sequence[str]) -> dict:
    read_flags = {"--get", "--get-all", "--list", "-l", "--get-regexp"}
    if not (set(rest) & read_flags):
        return _refuse(
            subcommand,
            "config_write_form",
            "`git config` refused unless it carries --get/--get-all/--list/--get-regexp — "
            "bare `config <key> <value>` mutates repo config",
        )
    return _allow(subcommand)


# Strict allowlist (F14). `git branch` has write forms that carry NO operand
# — `--set-upstream-to=<x>`, `--unset-upstream`, `--edit-description`,
# `--track`, `--create-reflog`, `--recurse-submodules` — so a rule keyed on
# "write flag present OR operand present" lets them through. Anything not
# named here is refused, which covers the write forms nobody enumerated.
# Full read set taken from `git help branch` (git 2.50.1).
_BRANCH_READ_FLAGS = {
    "--list", "-l", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose",
    "--contains", "--no-contains", "--merged", "--no-merged", "--sort",
    "--format", "--column", "--no-column", "--show-current", "--points-at",
    "--color", "--no-color", "-i", "--ignore-case", "--omit-empty",
    "-q", "--quiet", "--abbrev", "--no-abbrev",
}


def _normalize_flags(flags: Sequence[str]) -> set:
    """Split `--flag=value` -> `--flag` and short clusters `-av` -> {-a, -v}.

    Without this, a strict allowlist over-blocks real reads
    (`branch --sort=committerdate`, `branch -av`). `-vv` is a real spelling
    of --verbose and is kept whole.
    """
    out: set = set()
    for flag in flags:
        base = flag.split("=", 1)[0]
        if base.startswith("--") or len(base) <= 2 or base == "-vv":
            out.add(base)
            continue
        if set(base[1:]) == {"v"}:  # -vv, -vvv
            out.add("-vv")
            continue
        out.update("-" + ch for ch in base[1:])
    return out


def _branch(subcommand: str, rest: Sequence[str]) -> dict:
    raw_flags, operands = _flags_and_operands(rest)
    flags = _normalize_flags(raw_flags)
    unknown = flags - _BRANCH_READ_FLAGS
    if unknown:
        return _refuse(
            subcommand,
            "branch_write_form",
            f"`git branch {' '.join(sorted(unknown))}` refused — read-only auditor may only "
            "list branches; delete/rename/copy/force and the operand-less write forms "
            "(--set-upstream-to, --unset-upstream, --edit-description, --track) all mutate refs or config",
        )
    if operands and not (flags & _BRANCH_READ_FLAGS):
        return _refuse(
            subcommand,
            "branch_create_form",
            "`git branch <name>` refused — a bare name argument creates a branch; "
            "use `git branch --list <pattern>` to filter",
        )
    return _allow(subcommand)


_TAG_WRITE_FLAGS = {
    "-d", "-D", "-a", "--annotate", "-f", "--force", "-m", "--message",
    "-F", "--file", "-s", "--sign", "-u", "--local-user", "-e", "--edit",
}
_TAG_READ_FLAGS = {
    "--list", "-l", "--contains", "--no-contains", "--merged", "--no-merged",
    "--sort", "--format", "--points-at", "-n", "--column",
}


def _tag(subcommand: str, rest: Sequence[str]) -> dict:
    flags, operands = _flags_and_operands(rest)
    if flags & _TAG_WRITE_FLAGS:
        return _refuse(
            subcommand,
            "tag_write_form",
            "`git tag` create/delete/sign refused — read-only auditor may only list tags",
        )
    if operands and not (flags & _TAG_READ_FLAGS):
        return _refuse(
            subcommand,
            "tag_create_form",
            "`git tag <name>` refused — a bare name argument creates a tag; "
            "use `git tag --list <pattern>` to filter",
        )
    return _allow(subcommand)


def _worktree(subcommand: str, rest: Sequence[str]) -> dict:
    if not rest or rest[0] != "list":
        return _refuse(
            subcommand,
            "worktree_non_list_form",
            "`git worktree` refused unless it is `worktree list` — "
            "add/remove/prune mutate the filesystem",
        )
    return _allow(subcommand)


def _stash(subcommand: str, rest: Sequence[str]) -> dict:
    if not rest:
        return _refuse(
            subcommand,
            "stash_bare_push_form",
            "bare `git stash` refused — it is shorthand for `stash push`, which mutates the "
            "working tree; use `stash list` or `stash show`",
        )
    if rest[0] not in ("list", "show"):
        return _refuse(
            subcommand,
            "stash_mutating_subcommand",
            f"`git stash {rest[0]}` refused — only `stash list`/`stash show` are read-only",
        )
    return _allow(subcommand)


_REMOTE_MUTATING_VERBS = {
    "add", "remove", "rm", "rename", "set-url", "set-head", "set-branches",
    "prune", "update",
}
_REMOTE_READ_VERBS = {"show", "get-url"}


def _remote(subcommand: str, rest: Sequence[str]) -> dict:
    # F14: key on ALL of rest, not rest[0]. `git remote -v add o <url>` is a
    # real add — git skips the leading -v and executes the verb — but a
    # rest[0]-only check saw "-v", allowed it, and let the add through.
    hit = next((tok for tok in rest if tok in _REMOTE_MUTATING_VERBS), None)
    if hit is not None:
        return _refuse(
            subcommand,
            "remote_mutating_form",
            f"`git remote {hit}` refused — that verb mutates remotes/refs even when it "
            "follows a read-only flag like -v; only bare `remote`, `-v`, `show`, and `get-url` are read-only",
        )
    _, operands = _flags_and_operands(rest)
    if operands and operands[0] not in _REMOTE_READ_VERBS:
        return _refuse(
            subcommand,
            "remote_mutating_form",
            f"`git remote {operands[0]}` refused — only bare `remote`, `-v`, `show`, and `get-url` are read-only",
        )
    return _allow(subcommand)


_SIMPLE_READONLY_SUBCOMMANDS = (
    "log", "show", "diff", "status", "rev-parse", "rev-list", "ls-files",
    "ls-tree", "cat-file", "blame", "shortlog", "describe", "merge-base",
    "name-rev", "for-each-ref", "grep", "count-objects", "verify-commit",
    "check-ignore",
)

_HANDLERS = {name: _simple_readonly for name in _SIMPLE_READONLY_SUBCOMMANDS}
_HANDLERS.update(
    {
        "symbolic-ref": _symbolic_ref,
        "config": _config,
        "branch": _branch,
        "tag": _tag,
        "worktree": _worktree,
        "stash": _stash,
        "remote": _remote,
    }
)


def classify(argv: Sequence[str]) -> dict:
    """Classify a git argv (WITHOUT the leading `git`) as allowed or refused.

    Pure function — never touches the filesystem or subprocess. Refuse is
    the default: an unrecognized subcommand is refused, not allowed.
    """
    argv = list(argv)

    if not argv:
        return _refuse(None, "no_subcommand", "no git subcommand given")

    head = argv[0]

    # Pre-subcommand global options: `-c`, `--git-dir`, `--exec-path`, ...
    # Refused as a CLASS. Several reach arbitrary execution (`-c
    # core.pager=<cmd>`, `-c alias.x=!sh`, `--config-env`) or escape the
    # --repo confinement (`-C`, `--git-dir`, `--work-tree`). None is needed
    # to read. This is position-sensitive on purpose: `log -c` (combined
    # merge diff) and `diff -C` (find copies) are read-only and stay
    # allowed, because they come AFTER the subcommand.
    if head.startswith("-"):
        return _refuse(
            None,
            "pre_subcommand_global_option",
            f"`{head}` refused — pre-subcommand global options are not permitted "
            "(`-c`/`--config-env` inject config that can execute commands; "
            "`-C`/`--git-dir`/`--work-tree` retarget the repo). "
            "Put the subcommand first and use --repo <path> to choose the repository",
        )

    if not head.replace("-", "").isalnum() or not head.islower():
        return _refuse(
            None,
            "shell_string_as_subcommand",
            f"{head!r} is not a git subcommand — refused. git is invoked with "
            "shell=False, so a whole shell command line passed as one argv "
            "element can never run; pass each argument as its own element",
        )

    if any(flag in argv for flag in _GLOBAL_MUTATING_FLAGS):
        hit = next(flag for flag in _GLOBAL_MUTATING_FLAGS if flag in argv)
        return _refuse(
            argv[0],
            "global_mutating_flag",
            f"`{hit}` refused — read-only auditor; that flag discards or rewrites working-tree state",
        )

    subcommand = argv[0]
    rest = argv[1:]

    # F1: flag-level scan, BEFORE handler dispatch, so it covers every
    # allowlisted subcommand including the _simple_readonly family.
    denied = _denied_flag_hit(subcommand, rest)
    if denied is not None:
        return _refuse(
            subcommand,
            "mutating_flag_on_readonly_subcommand",
            f"`{denied}` refused on `{subcommand}` — that flag writes a file or spawns "
            "a process (e.g. `--output=<file>` truncates <file>; `-O<cmd>`/"
            "`--open-files-in-pager` and `--ext-diff`/`--textconv` execute commands). "
            "Read-only output goes to stdout; redirect it yourself if you need a file",
        )

    handler = _HANDLERS.get(subcommand)
    if handler is None:
        return _refuse_known(subcommand)

    return handler(subcommand, rest)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_argv(argv: list[str]) -> tuple[str | None, bool, list[str]]:
    """Split our own flags (--repo, --json) from the git args.

    Supports both:
      audit_git.py --repo <path> --json -- <git args...>
      audit_git.py <git args...>                          (ergonomic form)

    Only a LEADING run of --repo/--json tokens is treated as ours; the first
    "--" encountered immediately after that run is consumed as our own
    separator (if present). Everything after belongs to git verbatim,
    including any "--" git itself needs (e.g. `checkout -- <path>`).
    """
    repo: str | None = None
    as_json = False
    i = 0
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok == "--repo":
            i += 1
            if i >= n:
                raise ValueError("--repo requires a value")
            repo = argv[i]
            i += 1
            continue
        if tok == "--json":
            as_json = True
            i += 1
            continue
        break

    if i < n and argv[i] == "--":
        i += 1

    return repo, as_json, argv[i:]


def _emit_refusal(verdict: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(verdict), file=sys.stderr)
    else:
        print(f"audit_git: {verdict['reason']}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else list(argv)
    try:
        repo, as_json, git_args = _parse_argv(raw)
    except ValueError as exc:
        print(f"audit_git: {exc}", file=sys.stderr)
        return 2

    verdict = classify(git_args)
    if not verdict["allowed"]:
        _emit_refusal(verdict, as_json)
        return 2

    cmd = ["git"]
    if repo:
        cmd += ["-C", repo]
    cmd += git_args

    # shell=False is load-bearing: argv elements are never re-parsed by a
    # shell, which is why classify() needs no metacharacter filter.
    # GIT_PAGER=cat closes the one execution path argv inspection cannot
    # see: when stdout IS a tty, git spawns core.pager, and core.pager is
    # readable from the AUDITED repo's own .git/config.
    env = dict(os.environ, GIT_PAGER="cat")
    proc = subprocess.run(cmd, shell=False, env=env)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
