#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Classify an orchestrator action into one of four MECE labels.

The labels are MECE on the *output action shape* the orchestrator must take:

  SAFE       proceed on the main worktree, no prompt
  RISKY      proceed on an isolated worktree-branch, push branch, continue main
  DECISION   implementer surfaced novel_decisions[] — auto-pick in long-mode,
             escalate to operator in normal-mode
  PRODUCTION irreversible *and* production-targeted — always escalate

MECE proof. Every action satisfies exactly one of:
  - has an unresolved novel_decisions[] (DECISION)
  - is irreversible + targets production (PRODUCTION)
  - is irreversible non-production OR reversible+broad-blast (RISKY)
  - is none of the above (SAFE)

The decision tree below makes priority explicit so two rules can't fire at once.

Read-only short-circuit. Most operator pain comes from `vercel curl`,
`vercel logs`, `git status`, `cat`, `grep`, etc. being classified `unknown` by
deployment_policy and routed to `confirm`. Read-only commands are mechanically
SAFE regardless of environment — they cannot mutate prod by definition.

Compound commands. A command line is split into its parts (`&&`, `||`, `;`,
`|`, `&`, newlines, `$(...)`, backticks, subshells; quotes respected) and is
rated by its most severe part. It is read-only only when EVERY part is; an
unparseable command (heredoc, unbalanced quote) is never read-only.

CLI:
  python3 classify_action.py --workdir . --command "vercel curl --deployment x.com /"
  python3 classify_action.py --workdir . --files-touched "migrations/0042.sql"
  python3 classify_action.py --workdir . --envelope-json /path/to/envelope.json

Exit codes:
  0 — SAFE
  1 — RISKY
  2 — PRODUCTION
  3 — DECISION
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autonomy_gate  # noqa: E402
import deployment_policy  # noqa: E402


# ---------------------------------------------------------------------------
# A1. Read-only command detection
# ---------------------------------------------------------------------------
#
# A command is read-only when its first token (and any sub-verb) only inspects
# state. We intentionally err on the side of false negatives — when in doubt,
# don't claim a command is read-only. The penalty for missing a read-only is
# a redundant pass through later rules; the penalty for a false positive could
# be auto-executing a destructive op.

READ_ONLY_FIRST_TOKENS = frozenset({
    # POSIX inspection
    "ls", "cat", "head", "tail", "less", "more", "wc", "file", "stat",
    "find", "locate", "which", "type", "whereis",
    "grep", "egrep", "fgrep", "rg", "ag",
    "ps", "top", "htop", "uptime", "uname", "whoami", "id", "groups",
    "pwd", "echo", "printf", "date", "hostname",
    "env", "printenv",
    "du", "df", "free",
    "diff", "cmp",
    "jq", "yq", "xq",
    "tree",
    # Network read
    "curl", "wget", "http", "httpie", "xh",
    "ping", "traceroute", "dig", "nslookup", "host",
    "nc", "ncat",  # only if reading; we accept some false positives here
    # HTTP-API CLIs in read mode (sub-verb matched below)
    # vercel/gh/aws are handled per-subverb in READ_ONLY_SUBVERBS
})

# Sub-verb whitelists: tools whose top-level command name is generic but whose
# read-only sub-verbs are stable. Maps top-level → set of read-only sub-verbs.
READ_ONLY_SUBVERBS: dict[str, frozenset[str]] = {
    "git": frozenset({
        "status", "log", "diff", "show", "branch", "tag", "blame",
        "ls-files", "ls-remote", "ls-tree", "rev-parse", "rev-list",
        "describe", "config", "remote", "shortlog", "reflog", "cat-file",
        "for-each-ref", "show-ref", "name-rev", "whatchanged",
    }),
    "vercel": frozenset({
        "curl", "logs", "inspect", "ls", "list", "whoami", "teams",
        # Notably NOT "env" — `vercel env rm/add` writes to vercel's env store;
        # `vercel env ls/list/pull` is handled via READ_ONLY_SUB_SUBVERBS below.
    }),
    "gh": frozenset({
        "api", "auth", "browse", "issue", "pr", "release", "repo", "run",
        "search", "status", "workflow", "label", "ssh-key", "gpg-key",
    }),  # most gh sub-verbs are read-OR-write; we accept some false negatives downstream
    "aws": frozenset({"sts", "configure"}),  # very conservative
    "kubectl": frozenset({
        "get", "describe", "logs", "top", "explain", "version", "cluster-info",
        "config", "api-resources", "api-versions", "auth",
    }),
    "docker": frozenset({
        "ps", "images", "logs", "inspect", "history", "info", "version",
        "stats", "top", "events", "diff", "search", "system",
    }),
    "npm": frozenset({"list", "ls", "outdated", "ping", "view", "search", "config"}),
    "pnpm": frozenset({"list", "ls", "outdated", "view"}),
    "uv": frozenset({"pip", "lock", "tree", "cache"}),  # uv pip list/show etc.
    "pip": frozenset({"list", "show", "freeze", "check", "config"}),
    "poetry": frozenset({"show", "check", "env", "config"}),
    "node": frozenset({"--version", "-v", "--eval"}),  # generally OK
    "python": frozenset({"--version", "-V", "-c"}),  # -c can mutate but typically used for inspection
    "python3": frozenset({"--version", "-V", "-c"}),
}

# gh sub-verbs that are read-only when their second sub-verb is one of these
READ_ONLY_SUB_SUBVERBS: dict[tuple[str, str], frozenset[str]] = {
    ("gh", "pr"): frozenset({"list", "view", "diff", "checks", "status"}),
    ("gh", "issue"): frozenset({"list", "view"}),
    ("gh", "release"): frozenset({"list", "view"}),
    ("gh", "run"): frozenset({"list", "view", "watch"}),
    ("gh", "workflow"): frozenset({"list", "view"}),
    ("gh", "repo"): frozenset({"list", "view", "clone"}),
    ("gh", "label"): frozenset({"list"}),
    ("gh", "auth"): frozenset({"status"}),
    ("gh", "ssh-key"): frozenset({"list"}),
    ("gh", "gpg-key"): frozenset({"list"}),
    ("vercel", "env"): frozenset({"ls", "list", "pull"}),
}

# `gh api` sends GET unless a method flag says otherwise; any field or input
# flag switches the default to POST (gh api --help).
_GH_API_METHOD_FLAGS = ("-X", "--method")
_GH_API_BODY_FLAGS = ("-f", "-F", "--field", "--raw-field", "--input")


def _gh_api_is_read(args: list[str]) -> bool:
    """Return True only when `gh api <args>` is a GET that changes nothing."""
    method = None
    has_body = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _GH_API_METHOD_FLAGS:
            method = args[i + 1] if i + 1 < len(args) else ""
            i += 1
        elif arg.startswith("--method="):
            method = arg.split("=", 1)[1]
        elif arg.startswith("-X") and len(arg) > 2:
            method = arg[2:]
        elif arg in _GH_API_BODY_FLAGS:
            has_body = True
            i += 1
        elif arg.startswith(tuple(f + "=" for f in _GH_API_BODY_FLAGS)) or (
                arg[:2] in ("-f", "-F") and len(arg) > 2):
            has_body = True
        i += 1
    if "graphql" in args:
        # GraphQL always POSTs: a query reads; a mutation, or a query read
        # from a file we cannot inspect, may write.
        text = " ".join(args).lower()
        return "mutation" not in text and "=@" not in text
    if method is not None:
        return method.upper() == "GET"
    return not has_body


# Git sub-verbs above that also have writing forms. A sub-verb is read-only only
# when none of its writing flags or writing first-positionals is present.
_GIT_WRITE_FLAGS: dict[str, frozenset[str]] = {
    "branch": frozenset({
        "-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy",
        "-f", "--force", "-u", "--set-upstream-to", "--unset-upstream",
        "--edit-description",
    }),
    "tag": frozenset({
        "-d", "--delete", "-f", "--force", "-a", "--annotate", "-s", "--sign",
        "-u", "--local-user", "-m", "--message", "-F", "--file",
    }),
    "config": frozenset({
        "--unset", "--unset-all", "--add", "--replace-all", "--remove-section",
        "--rename-section", "-e", "--edit",
    }),
}
# First positional argument that makes the sub-verb write (`git remote add`).
# None means: any positional other than the listed READ forms writes.
_GIT_WRITE_POSITIONALS: dict[str, frozenset[str]] = {
    "remote": frozenset({"add", "remove", "rm", "rename", "set-url", "set-head",
                         "set-branches", "prune", "update"}),
    "reflog": frozenset({"expire", "delete", "drop"}),
    "config": frozenset({"set", "unset", "rename-section", "remove-section", "edit"}),
}
_GIT_CONFIG_VALUE_OPTS = frozenset({"-f", "--file", "--blob", "--type", "--default"})


def _git_subverb_writes(sub: str, args: list[str]) -> bool:
    """True when `git <sub> <args>` uses a writing form of a read-only sub-verb."""
    write_flags = _GIT_WRITE_FLAGS.get(sub, frozenset())
    positionals: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("--"):
            if arg.split("=", 1)[0] in write_flags:
                return True
        elif arg.startswith("-") and len(arg) > 1:
            # Short-flag cluster: `-Df` carries -D.
            if any(f"-{ch}" in write_flags for ch in arg[1:]):
                return True
        else:
            positionals.append(arg)
        if sub == "config" and arg in _GIT_CONFIG_VALUE_OPTS:
            skip_next = True
    if positionals and positionals[0] in _GIT_WRITE_POSITIONALS.get(sub, frozenset()):
        return True
    if sub == "config":
        # `git config key` reads; `git config key value` writes.
        reads = positionals and positionals[0] in ("get", "list")
        return not reads and len(positionals) > 1
    return False


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _part_is_read_only(command: str) -> bool:
    """Return True when ONE simple command (no separators) only inspects state."""
    cmd = command.strip()
    if not cmd:
        return False
    words = cmd.split()
    # A brace group or function definition left in a part is not a simple command.
    if any(w in ("{", "}") or w.endswith("()") for w in words):
        return False
    head = words[0]
    if head in READ_ONLY_FIRST_TOKENS:
        return True

    tokens = _tokens(cmd)
    if not tokens:
        return False
    first = tokens[0]
    sub = tokens[1] if len(tokens) > 1 else ""
    sub2 = tokens[2] if len(tokens) > 2 else ""

    # Two-level whitelist (e.g. `gh pr view`)
    # When (first, sub) appears in the two-level table, it OVERRIDES the
    # one-level whitelist — we treat the second level as authoritative for that
    # sub-verb. Prevents false positives like `vercel env rm` matching the
    # one-level "env" entry when the two-level entry restricts to {ls,list,pull}.
    if first == "gh" and sub == "api":
        return _gh_api_is_read(tokens[2:])

    two = (first, sub)
    if two in READ_ONLY_SUB_SUBVERBS:
        return sub2 in READ_ONLY_SUB_SUBVERBS[two]

    # One-level whitelist (e.g. `git status`, `vercel logs`)
    sub_set = READ_ONLY_SUBVERBS.get(first)
    if sub_set is not None and sub in sub_set:
        if first == "git" and _git_subverb_writes(sub, tokens[2:]):
            return False
        return True

    return False


def _is_read_only(command: str) -> bool:
    """Return True when EVERY part of the command is mechanically read-only.

    An unparseable command (unbalanced quotes, heredoc, ...) is never read-only.
    """
    parts = split_command(command)
    if not parts or len(parts) > MAX_PARTS:
        return False
    return all(_part_is_read_only(p) for p in parts)


# ---------------------------------------------------------------------------
# A1b. Compound-command splitting
# ---------------------------------------------------------------------------
#
# A shell command line is judged by its most severe part, so every part must be
# found: `ls && rm -rf ~/x` is two commands, and the first word says nothing
# about the second. The splitter is deliberately small and explicit (shlex
# cannot keep separators). It returns None for anything it does not
# understand; callers must then judge the whole string WITHOUT the read-only
# shortcut, never fall back to "read-only".

class _Unparseable(Exception):
    pass


# Reserved words that open a compound statement; the command follows them.
_LEADING_KEYWORDS = frozenset({"then", "do", "else", "elif", "if", "while", "until", "!", "{", "time"})
# Reserved words that only close a compound statement.
_CLOSING_KEYWORDS = frozenset({"fi", "done", "}", "esac"})


# `name () {` / `function name {` — the header defines, the body runs later.
_FUNCTION_HEADER = re.compile(
    r"^(?:function\s+[^\s(){}]+\s*(?:\(\s*\))?|[^\s(){}]+\s*\(\s*\))\s*(?:\{\s*)?"
)


def _normalize_part(text: str) -> str:
    part = _FUNCTION_HEADER.sub("", text.strip(), count=1)
    while part:
        head, _, rest = part.partition(" ")
        if head in _LEADING_KEYWORDS:
            part = rest.strip()
            continue
        break
    while part.endswith((" }", ";}")) or part == "}":
        part = part[:-1].rstrip()
    return "" if part in _CLOSING_KEYWORDS else part


def _find_backtick_end(s: str, i: int) -> int:
    """Index of the backtick closing the one at s[i]."""
    j = i + 1
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == "`":
            return j
        j += 1
    raise _Unparseable("unterminated backtick")


# Nesting deeper than this is not ordinary shell; refusing it keeps recursion
# depth independent of the input.
MAX_NESTING = 16
# More parts than this are not judged one by one (see _is_read_only).
MAX_PARTS = 64


def _scan(s: str, i: int, closer: str | None, parts: list[str], depth: int = 0) -> int:
    """Append the parts found from s[i] on; return the index after `closer`."""
    if depth > MAX_NESTING:
        raise _Unparseable("nesting too deep")
    buf: list[str] = []

    def flush() -> None:
        part = _normalize_part("".join(buf))
        if part:
            parts.append(part)
        buf.clear()

    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            if s.startswith("\\\n", i):  # line continuation
                i += 2
                continue
            buf.append(s[i:i + 2])
            i += 2
        elif s.startswith("$'", i):  # ANSI-C quoting: backslash escapes the quote
            j = i + 2
            while j < n and s[j] != "'":
                j += 2 if s[j] == "\\" else 1
            if j >= n:
                raise _Unparseable("unterminated $' quote")
            buf.append(s[i:j + 1])
            i = j + 1
        elif c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise _Unparseable("unterminated single quote")
            buf.append(s[i:j + 1])
            i = j + 1
        elif c == '"':
            j = i + 1
            while True:
                if j >= n:
                    raise _Unparseable("unterminated double quote")
                if s[j] == "\\":
                    j += 2
                elif s[j] == '"':
                    break
                elif s[j] == "`":
                    end = _find_backtick_end(s, j)
                    _scan(s[j + 1:end], 0, None, parts, depth + 1)
                    j = end + 1
                elif s.startswith("$(", j):
                    j = _scan(s, j + 2, ")", parts, depth + 1)
                else:
                    j += 1
            buf.append(s[i:j + 1])
            i = j + 1
        elif c == "`":
            end = _find_backtick_end(s, i)
            _scan(s[i + 1:end], 0, None, parts, depth + 1)
            buf.append(s[i:end + 1])
            i = end + 1
        elif s.startswith("$(", i):
            j = _scan(s, i + 2, ")", parts, depth + 1)
            buf.append(s[i:j])
            i = j
        elif c == "(":
            j = _scan(s, i + 1, ")", parts, depth + 1)
            if "".join(buf).strip():  # `<(cmd)`, `f()`, `a=(1 2)`: keep the text
                buf.append(s[i:j])
            i = j
        elif c == ")":
            if closer != ")":
                raise _Unparseable("unbalanced ')'")
            flush()
            return i + 1
        elif s.startswith("<<<", i):
            buf.append("<<<")
            i += 3
        elif s.startswith("<<", i):
            raise _Unparseable("heredoc")
        elif c == "#" and (not buf or (len(buf[-1]) == 1 and buf[-1].isspace())):
            while i < n and s[i] != "\n":  # comment runs to end of line
                i += 1
        elif s.startswith(("&&", "||"), i):
            flush()
            i += 2
        elif c == "&" and (s[i + 1:i + 2] == ">" or (buf and buf[-1][-1:] in ("<", ">"))):
            buf.append(c)  # redirection: `&>file`, `2>&1`, `<&3`
            i += 1
        elif c in ";|&\n\r":
            flush()
            i += 1
        else:
            buf.append(c)
            i += 1
    if closer is not None:
        raise _Unparseable(f"missing {closer!r}")
    flush()
    return i


def split_command(command: str) -> list[str] | None:
    """Return the simple commands in a shell command line, or None if unparseable.

    Splits on `&&`, `||`, `;`, `|`, `&` and newlines, and adds the bodies of
    `$(...)`, backticks and `(...)` subshells as parts of their own. Separators
    inside quotes do not split. Heredocs, unbalanced quotes or parentheses and
    unterminated substitutions return None.
    """
    parts: list[str] = []
    try:
        _scan(command, 0, None, parts)
    except (_Unparseable, RecursionError):
        return None
    return parts


# ---------------------------------------------------------------------------
# A5. Irreversible-non-deployment commands
# ---------------------------------------------------------------------------
# These are destructive *regardless* of environment target. force-push to a
# feature branch is recoverable (you can re-push); force-push to main is not.
# We classify the COMMAND here; the environment determines whether it becomes
# PRODUCTION or RISKY.

IRREVERSIBLE_PATTERNS = (
    re.compile(r"\bdrop\s+(table|database|schema)\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+publish\b"),
    re.compile(r"\bpnpm\s+publish\b"),
    re.compile(r"\byarn\s+publish\b"),
    re.compile(r"\btwine\s+upload\b"),
    re.compile(r"\bcargo\s+publish\b"),
    re.compile(r"\bgh\s+release\s+create\b"),
    re.compile(r"\brm\s+-r?f?\s+/(\s|$)"),
    re.compile(r"\bgit\s+push\s+(--force|-f)\b"),
)


def _is_irreversible(command: str) -> bool:
    cmd = command.strip()
    return any(p.search(cmd) for p in IRREVERSIBLE_PATTERNS)


# ---------------------------------------------------------------------------
# A2. RISKY file globs (broad blast on otherwise-reversible work)
# ---------------------------------------------------------------------------

DEFAULT_RISKY_GLOBS: list[str] = [
    "migrations/**",
    "**/migrations/**",
    "schema*.prisma",
    "**/schema*.prisma",
    ".github/workflows/**",
    "Dockerfile",
    "docker-compose*.y*ml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "requirements.txt",
    "pyproject.toml",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "**/auth/**",
    "**/secrets/**",
]


def _load_classify_config(workdir: Path) -> dict[str, Any]:
    config_path = workdir / ".build-loop" / "config.json"
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get("classifyAction")
    if not isinstance(block, dict):
        return {}
    return block


def _resolved_risky_globs(workdir: Path) -> list[str]:
    cfg = _load_classify_config(workdir)
    custom = cfg.get("riskyGlobs")
    if isinstance(custom, list):
        return [str(p) for p in custom]
    return list(DEFAULT_RISKY_GLOBS)


def _files_match_risky(files: list[str], globs: list[str]) -> tuple[bool, str]:
    for f in files:
        for g in globs:
            if fnmatch.fnmatch(f, g) or fnmatch.fnmatch(f.lower(), g.lower()):
                return True, f"{f} matches {g}"
    return False, ""


# ---------------------------------------------------------------------------
# A4. Deployment target detection (delegates to deployment_policy)
# ---------------------------------------------------------------------------

def _deployment_target(command: str) -> str:
    """Return 'production' | 'testflight' | 'preview' | 'unknown' | 'n/a'."""
    if not command.strip():
        return "n/a"
    target, _reason = deployment_policy.classify_command(command)
    return target


# ---------------------------------------------------------------------------
# A3. Decision pickability from envelope
# ---------------------------------------------------------------------------

def _decision_state(envelope: dict[str, Any]) -> tuple[str, str]:
    """Return (state, reason).
    state ∈ {"none", "pickable", "low_confidence", "malformed"}
    """
    novel = envelope.get("novel_decisions") or []
    if not isinstance(novel, list) or not novel:
        return "none", ""

    for i, entry in enumerate(novel):
        if not isinstance(entry, dict):
            return "malformed", f"novel_decisions[{i}] not an object"
        if not entry.get("recommended_default"):
            return "malformed", f"novel_decisions[{i}] missing recommended_default"
        if entry.get("confidence") == "low":
            return "low_confidence", f"novel_decisions[{i}] confidence=low"

    return "pickable", f"{len(novel)} pickable decisions"


# ---------------------------------------------------------------------------
# MECE classifier
# ---------------------------------------------------------------------------

def classify(
    workdir: Path,
    *,
    command: str = "",
    files_touched: list[str] | None = None,
    envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a single MECE classification envelope.

    Priority order (first match wins):
      1. PRODUCTION   — irreversible + production target
      2. RISKY        — irreversible + non-production target
      3. PRODUCTION   — production deployment command (not read-only)
      4. SAFE         — read-only command
      5. DECISION     — has unresolved novel_decisions[]
      6. RISKY        — touches broad-blast files
      7. SAFE         — default
    """
    files_touched = files_touched or []
    envelope = envelope or {}

    # Rules 1-4 judge each part of a compound command and keep the most severe
    # verdict; rule 4 (read-only) needs EVERY part read-only. The whole string
    # is judged as one more candidate, so splitting can never lower a verdict.
    read_only = False
    if command.strip():
        parts = split_command(command)
        read_only = _is_read_only(command)
        candidates = list(dict.fromkeys([command] + [p for p in (parts or []) if p != command.strip()]))
        worst: tuple[int, dict[str, Any]] | None = None
        for text in candidates:
            verdict = _command_verdict(text, read_only if text == command else _part_is_read_only(text))
            if verdict and (worst is None or verdict[0] > worst[0]):
                worst = verdict
        if worst:
            return worst[1]

    # 4. SAFE — read-only short-circuit
    if read_only:
        return _envelope(
            "SAFE",
            reason="read-only command",
            matched_rule="read_only",
            delegated_to="classify_action",
        )

    # 5. DECISION — envelope has open decisions
    dstate, dreason = _decision_state(envelope)
    if dstate != "none":
        return _envelope(
            "DECISION",
            reason=dreason,
            matched_rule=f"envelope:{dstate}",
            delegated_to="envelope",
            decision_state=dstate,
        )

    # 6. RISKY — broad-blast files (only if reversible + non-prod + no decision)
    if files_touched:
        globs = _resolved_risky_globs(workdir)
        hit, detail = _files_match_risky(files_touched, globs)
        if hit:
            return _envelope(
                "RISKY",
                reason=f"file touch warrants branch isolation: {detail}",
                matched_rule=detail,
                delegated_to="risky_globs",
            )

    # 7. SAFE — default
    return _envelope(
        "SAFE",
        reason="no irreversible action, no production target, no broad-blast file, no decision",
        matched_rule=None,
        delegated_to=None,
    )


def _command_verdict(command: str, read_only: bool) -> tuple[int, dict[str, Any]] | None:
    """Rules 1-3 for one command text: (severity rank, envelope), or None."""
    target = _deployment_target(command)
    irreversible = _is_irreversible(command)

    # 1. PRODUCTION — irreversible + production
    if irreversible and target == "production":
        return 3, _envelope(
            "PRODUCTION",
            reason=f"irreversible command targets production ({target})",
            matched_rule="irreversible+production",
            delegated_to="classify_action",
        )

    # 2. RISKY — irreversible + non-production (force-push to feature, etc.)
    if irreversible:
        return 1, _envelope(
            "RISKY",
            reason="irreversible command targeting non-production — isolate to branch",
            matched_rule="irreversible+non-production",
            delegated_to="classify_action",
        )

    # 3. PRODUCTION — non-irreversible production deploy command
    # Read-only inspection of prod (e.g. `vercel logs --prod`) is NOT production.
    if target == "production" and not read_only:
        return 2, _envelope(
            "PRODUCTION",
            reason="command targets production deployment",
            matched_rule="deployment_policy:production",
            delegated_to="deployment_policy",
        )
    return None


def _envelope(
    classification: str,
    *,
    reason: str,
    matched_rule: str | None,
    delegated_to: str | None,
    **extra: Any,
) -> dict[str, Any]:
    env = {
        "classification": classification,
        "reason": reason,
        "matched_rule": matched_rule,
        "delegated_to": delegated_to,
    }
    env.update(extra)
    return env


_EXIT_CODES = {"SAFE": 0, "RISKY": 1, "PRODUCTION": 2, "DECISION": 3}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--command", default="")
    parser.add_argument("--files-touched", default="")
    parser.add_argument("--envelope-json", default="")
    parser.add_argument("--action-label", default="")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()

    files_touched: list[str] = []
    if args.files_touched:
        for t in args.files_touched.replace("\n", ",").split(","):
            t = t.strip()
            if t:
                files_touched.append(t)

    envelope: dict[str, Any] = {}
    if args.envelope_json:
        env_path = Path(args.envelope_json)
        if env_path.exists():
            try:
                envelope = json.loads(env_path.read_text())
            except json.JSONDecodeError as exc:
                print(json.dumps({
                    "classification": "SAFE",
                    "reason": f"envelope JSON unparseable ({exc}); defaulting SAFE",
                    "matched_rule": None,
                    "delegated_to": None,
                    "warning": "envelope_unparseable",
                }))
                return 0

    result = classify(workdir, command=args.command, files_touched=files_touched, envelope=envelope)
    if args.action_label:
        result["action_label"] = args.action_label

    print(json.dumps(result))
    return _EXIT_CODES.get(result["classification"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
