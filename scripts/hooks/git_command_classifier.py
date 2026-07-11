#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""git_command_classifier.py — does a Bash command GENUINELY invoke `git commit` / `git push`?

WHY THIS EXISTS (named, observed failure — 2026-07-11)
------------------------------------------------------
``pre_bash_dispatch.sh`` fired its commit-auditor and pre-push security scan from coarse
substring globs (``case "$CMD" in *commit*`` / ``*git*push*``). Those matched "git" and
"push"/"commit" as substrings ANYWHERE in the command — repo paths under ``git-folder``,
the word "pushed" in a ``rally say`` subject, a backlog title, and a python heredoc whose
TEXT contained example git commands all false-fired (6+ times in one session), each dumping
a ~40-finding full-repo scan into context and, for the heredoc, tripping the commit-audit
packet builder.

This module replaces the substring glob with a real parse: strip heredoc BODIES, split the
command on shell control operators (``&&`` ``||`` ``;`` ``|`` ``&`` newline), ``shlex``-parse
each segment, and report a subcommand only when a segment's ``argv[0]`` is ``git`` (or a
path ending ``/git``) and its subcommand token is ``commit`` / ``push``. Heredoc text is
never matched.

CONSERVATISM CONTRACT
---------------------
The gates this feeds are security-relevant, so a MISS is worse than a false fire. On any
parse ambiguity (unbalanced quotes in a segment) or on any top-level error, emit BOTH
subcommands so the conservative gates still run — never scan less than intended. The
downstream pre-push classifier independently re-checks whether a push is "plain" to decide
delta-scope vs full-scan, so an over-broad trigger here only ever costs an unnecessary scan.

USAGE
-----
Reads the PreToolUse event JSON on stdin (same shape the dispatcher receives) and prints the
detected subcommands-of-interest, space-separated, from {commit, push} (empty = neither).
Exit 0 always.

    printf '%s' "$EVENT_JSON" | python3 git_command_classifier.py   # -> e.g. "push"
"""
from __future__ import annotations

import json
import re
import shlex
import sys

SUBCOMMANDS_OF_INTEREST = ("commit", "push")

# Split a command line into segments at shell control operators. A segment is the run of
# text up to the next operator; argv[0] of each segment is what we classify.
_SPLIT_RE = re.compile(r"&&|\|\||[;|&\n]")

# A heredoc opener: << or <<- , optional whitespace, an optionally-quoted word delimiter.
# We only need the delimiter word to know where the body ends.
_HEREDOC_RE = re.compile(r"<<-?\s*([\"']?)([A-Za-z_][A-Za-z0-9_]*)\1")

# git global options that take a VALUE, so we skip the value token when finding the
# subcommand (`git -C <path> push`, `git -c k=v push`, `git --git-dir=<d> commit`).
_GIT_VALUE_OPTS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix",
})


def _read_command_from_stdin() -> str:
    """Extract the raw command (newlines intact) from a PreToolUse event JSON on stdin."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    cmd = (data.get("tool_input") or {}).get("command")
    return cmd if isinstance(cmd, str) else ""


def strip_heredoc_bodies(cmd: str) -> str:
    """Remove heredoc BODY lines, keeping the opener line (which holds the real command).

    A heredoc's text is data, not commands, so example git invocations inside it must not
    trigger a gate. We keep the opener line (`cat <<'PY'` may sit after a real `git push &&`)
    and drop every line until the delimiter, for each heredoc opened on that line in order.
    """
    lines = cmd.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        out.append(line)  # keep the opener (real commands may precede the <<)
        terminators = [m.group(2) for m in _HEREDOC_RE.finditer(line)]
        if not terminators:
            i += 1
            continue
        i += 1
        # Consume each heredoc's body up to (and including) its delimiter line.
        for term in terminators:
            while i < n and lines[i].strip() != term:
                i += 1
            if i < n:  # skip the terminator line itself
                i += 1
    return "\n".join(out)


def _git_subcommand(tokens: list[str]) -> str | None:
    """Return the git subcommand for a segment's argv, or None if it is not a git call.

    argv[0] must be ``git`` or a path ending in ``/git``. Global options (incl. value-taking
    ones) are skipped; the first positional token is the subcommand.
    """
    if not tokens:
        return None
    base = tokens[0].rsplit("/", 1)[-1]
    if base != "git":
        return None
    i, n = 1, len(tokens)
    while i < n:
        t = tokens[i]
        if not t.startswith("-"):
            return t  # first positional == subcommand
        key = t.split("=", 1)[0]
        if key in _GIT_VALUE_OPTS and "=" not in t:
            i += 2  # `-C <path>` style: skip the value token too
        else:
            i += 1
    return None


def classify_command(cmd: str) -> set[str]:
    """Return the set of {commit, push} subcommands genuinely invoked in ``cmd``.

    Heredoc bodies are stripped first; then each shell segment is shlex-parsed and only a
    real ``git`` call with a matching subcommand counts. A segment with unbalanced quotes
    can't be parsed — treat it conservatively as possibly containing both (never miss).
    """
    if not cmd or not cmd.strip():
        return set()
    found: set[str] = set()
    stripped = strip_heredoc_bodies(cmd)
    for segment in _SPLIT_RE.split(stripped):
        seg = segment.strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg)
        except ValueError:
            # Unbalanced quotes — cannot parse this segment. Stay conservative.
            found.update(SUBCOMMANDS_OF_INTEREST)
            continue
        sub = _git_subcommand(tokens)
        if sub in SUBCOMMANDS_OF_INTEREST:
            found.add(sub)
    return found


def main() -> int:
    try:
        cmd = _read_command_from_stdin()
        found = classify_command(cmd)
    except Exception:
        # Top-level fail-open: emit BOTH so the conservative gates still run.
        found = set(SUBCOMMANDS_OF_INTEREST)
    print(" ".join(s for s in SUBCOMMANDS_OF_INTEREST if s in found))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
