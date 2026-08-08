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
import subprocess
import sys
from typing import Sequence

# ---------------------------------------------------------------------------
# Shell-injection guard — applies before any allowlist logic.
# ---------------------------------------------------------------------------

_SHELL_METACHARACTERS = (";", "&&", "||", "|", "`", "$(", ">", "<")

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


_BRANCH_WRITE_FLAGS = {"-d", "-D", "-m", "-M", "-f", "--force", "--delete", "--move", "--copy", "-c", "-C"}
_BRANCH_READ_FLAGS = {
    "--list", "-l", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose",
    "--contains", "--no-contains", "--merged", "--no-merged", "--sort",
    "--format", "--column", "--show-current", "--points-at",
}


def _branch(subcommand: str, rest: Sequence[str]) -> dict:
    flags, operands = _flags_and_operands(rest)
    if flags & _BRANCH_WRITE_FLAGS:
        return _refuse(
            subcommand,
            "branch_write_form",
            "`git branch` delete/rename/copy/force refused — read-only auditor may only list branches",
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


def _remote(subcommand: str, rest: Sequence[str]) -> dict:
    if not rest or rest[0] in ("-v", "--verbose", "show", "get-url"):
        return _allow(subcommand)
    return _refuse(
        subcommand,
        "remote_mutating_form",
        f"`git remote {rest[0]}` refused — only bare `remote`, `-v`, `show`, and `get-url` are read-only",
    )


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

    for tok in argv:
        for mc in _SHELL_METACHARACTERS:
            if mc in tok:
                return _refuse(
                    None,
                    "shell_metacharacter_in_argv",
                    f"argv element {tok!r} contains shell metacharacter {mc!r} — refused; "
                    "git is never invoked through a shell",
                )

    if not argv:
        return _refuse(None, "no_subcommand", "no git subcommand given")

    if any(flag in argv for flag in _GLOBAL_MUTATING_FLAGS):
        hit = next(flag for flag in _GLOBAL_MUTATING_FLAGS if flag in argv)
        return _refuse(
            argv[0],
            "global_mutating_flag",
            f"`{hit}` refused — read-only auditor; that flag discards or rewrites working-tree state",
        )

    subcommand = argv[0]
    rest = argv[1:]

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

    proc = subprocess.run(cmd, shell=False)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
