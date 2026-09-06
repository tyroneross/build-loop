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

# Shell control operators that separate segments: && || ; | & and newline. A segment is the
# run of text up to the next operator; argv[0] of each segment is what we classify. Splitting
# is done QUOTE-AWARE by _split_on_unquoted_operators (below) — a regex splitter blindly
# broke operators INSIDE quotes (`grep ';|' f`, `find … \;`), leaving a fragment with an
# unbalanced quote that shlex could not parse, which forced the conservative both-subcommands
# fallback and false-fired the pre-push security scan on read-only commands (observed live
# 2026-07-15: `grep -rn '\[;|' file`, `echo "a|b"`, `find . -exec rm {} \;`).

# A heredoc opener: << or <<- , optional whitespace, an optionally-quoted word delimiter.
# We only need the delimiter word to know where the body ends.
_HEREDOC_RE = re.compile(r"<<-?\s*([\"']?)([A-Za-z_][A-Za-z0-9_]*)\1")

# git global options that take a VALUE, so we skip the value token when finding the
# subcommand (`git -C <path> push`, `git -c k=v push`, `git --git-dir=<d> commit`).
_GIT_VALUE_OPTS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix",
})

# Command wrappers that PREFIX a real command without changing what it does. A genuine
# `nohup git push` / `env FOO=bar git push` / `time git push` / `timeout 30 git push` must
# still be seen — the old `*git*push*` substring glob caught these, so missing them would
# be a conservatism regression (never scan less than intended). Some take their own args
# (timeout/sudo/xargs); we can't always count those, so when a wrapper is argv[0] we fall
# back to scanning the whole segment for a nested `git <push|commit>`.
_WRAPPER_COMMANDS = frozenset({
    "env", "nohup", "command", "time", "sudo", "doas", "timeout", "gtimeout",
    "stdbuf", "caffeinate", "builtin", "exec", "nice", "ionice", "xargs",
    # Command RUNNERS: they take their own args and then execute a command
    # given to them, so a `git` token deeper in the segment is still a real
    # invocation. `find . -exec git push \;` and `$(which git) push` were both
    # caught by the old substring guard and lost when it was narrowed; they are
    # ordinary idioms, not adversarial constructions.
    "find", "parallel", "watch", "entr", "flock", "su", "runuser", "script",
    "which", "type",
})
# A leading VAR=val assignment (env-style): `FOO=bar git push`.
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Binaries that can execute a git command supplied as a STRING argument, so a
# `git` token sitting in argument position is still reachable through them.
_SHELL_INTERPRETERS = frozenset({"bash", "sh", "zsh", "ksh", "dash", "fish", "eval", "source"})

# Shell control characters that can start a fresh command position. Split is
# deliberately quote-BLIND: this is only used on text we already failed to parse,
# where quote state is unknowable, and splitting more produces MORE candidate
# command positions — the conservative direction.
_COMMAND_POSITION_SPLIT_RE = re.compile(r"&&|\|\||\$\(|[;|&\n()`{}]")


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


def strip_heredoc_bodies(cmd: str) -> tuple[str, bool]:
    """Remove heredoc BODY lines; return (stripped_text, unterminated).

    A heredoc's text is data, not commands, so example git invocations inside it must not
    trigger a gate. We keep the opener line (`cat <<'PY'` may sit after a real `git push &&`)
    and drop every line until the delimiter, for each heredoc opened on that line in order.

    ``unterminated`` is True when a detected ``<<WORD`` opener has NO matching delimiter line
    (a real shell heredoc is always terminated within one command; an UNTERMINATED one means
    our text-level `<<` detection likely mis-fired on a bit-shift inside a quoted string,
    e.g. ``python3 -c "print(x << shift)"``, and we would otherwise eat the real command that
    follows). The caller degrades that ambiguity to a conservative both-subcommands trigger.
    """
    lines = cmd.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    unterminated = False
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
            found = False
            while i < n:
                if lines[i].strip() == term:
                    found = True
                    break
                i += 1
            if not found:
                unterminated = True
                break  # consumed to EOF; nothing more to scan
            i += 1  # skip the terminator line itself
    return "\n".join(out), unterminated


def _split_on_unquoted_operators(text: str) -> list[str]:
    """Split ``text`` at shell control operators (``&&`` ``||`` ``;`` ``|`` ``&`` newline)
    that are OUTSIDE single/double quotes and are not backslash-escaped.

    Quote-awareness is the whole point: an operator character INSIDE a quoted string
    (a grep pattern ``';|'``, an ``echo "a|b"``, a ``find … \\;`` terminator) is DATA, not a
    command separator. The old ``re.split`` broke such quotes apart, yielding a fragment with
    an unbalanced quote that ``shlex.split`` rejected — which drove the conservative
    both-subcommands fallback and false-fired the pre-push security scan on read-only commands.

    A genuinely UNTERMINATED quote (real ambiguity) is preserved intact in the final segment,
    so the caller's ``shlex.split`` still raises there and the conservatism contract still fires.
    """
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None  # "'" or '"' while inside that quote
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            # Backslash escape outside quotes (`\;`, `\|`) — keep both chars literally so the
            # escaped operator neither splits nor leaves a dangling backslash for shlex.
            buf.append(ch)
            buf.append(text[i + 1])
            i += 2
            continue
        if ch in ("&", "|") and i + 1 < n and text[i + 1] == ch:  # && or ||
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if ch in (";", "|", "&", "\n"):  # single-char operators
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return segments


def _subcommand_after_git(tokens: list[str]) -> str | None:
    """Given argv whose ``tokens[0]`` is a git binary, return its subcommand or None.

    Global options (incl. value-taking ones like ``-C <path>``) are skipped; the first
    positional token is the subcommand.
    """
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


def _strip_leading_wrappers(tokens: list[str]) -> list[str]:
    """Drop leading no-arg command wrappers and VAR=val assignments (`nohup`, `env FOO=bar`)."""
    i, n = 0, len(tokens)
    while i < n:
        base = _cmd_basename(tokens[i])
        if base in _WRAPPER_COMMANDS or _ASSIGNMENT_RE.match(tokens[i]):
            i += 1
            continue
        break
    return tokens[i:]


def _git_subcommand(tokens: list[str]) -> str | None:
    """Return the git subcommand for a segment's argv, or None if it is not a git call.

    Handles wrapper prefixes so a genuine push/commit is never hidden (f1): strip leading
    no-arg wrappers + assignments and re-check argv[0]==git; and, when the ORIGINAL argv[0]
    is any known wrapper (incl. arg-taking ones like `timeout`/`sudo`), scan the whole
    segment for a nested `git <push|commit>`. A false fire here is only an unnecessary scan;
    a miss would let a secret ship — so we bias to firing (never scan less than intended).
    """
    if not tokens:
        return None
    stripped = _strip_leading_wrappers(tokens)
    if stripped and _cmd_basename(stripped[0]) == "git":
        return _subcommand_after_git(stripped)
    # A shell interpreter (and several wrappers) run their `-c` STRING as a
    # command, so the git call is a whole token rather than argv[0].
    # `bash -c "git push origin main"`, `su - deploy -c "git push"`, and
    # `flock /tmp/l -c "git push"` were missed on this parseable path at every
    # revision — the gate simply never ran on them. (Auditor finding f11; the
    # fallback guard already covered them, but only for commands that failed to
    # parse.) Recursing re-uses the whole classifier, so nesting and wrappers
    # inside the string are handled by construction, and the string shrinks each
    # time so the recursion terminates.
    #
    # Both heads are checked: `su - deploy -c …` and `flock /tmp/l -c …` strip to
    # their own arguments (`-`, `/tmp/l`), so the wrapper-stripped head alone
    # misses them, while `nohup bash -c …` needs exactly that stripped head. A
    # direct `git -c k=v push` never reaches here — the branch above returns first
    # — so git's own `-c` is not mistaken for a command string.
    heads = {_cmd_basename(tokens[0])}
    if stripped:
        heads.add(_cmd_basename(stripped[0]))
    if heads & (_SHELL_INTERPRETERS | _WRAPPER_COMMANDS):
        for idx, tok in enumerate(tokens):
            if tok == "-c" and idx + 1 < len(tokens):
                for sub in SUBCOMMANDS_OF_INTEREST:
                    if sub in classify_command(tokens[idx + 1]):
                        return sub
    if _cmd_basename(tokens[0]) in _WRAPPER_COMMANDS:
        # Scan PAST a `git` token whose next positional is not a subcommand of
        # interest: in `find /repos -name git -exec git push \;` the first match
        # is the `-name` predicate VALUE, and returning on it hid the real push.
        for idx in range(len(tokens)):
            if _cmd_basename(tokens[idx]) == "git":
                sub = _subcommand_after_git(tokens[idx:])
                if sub in SUBCOMMANDS_OF_INTEREST:
                    return sub
    return None


def _cmd_basename(token: str) -> str:
    """Token → its command basename, ignoring quoting and alias-bypass syntax.

    Two prefixes must not hide a command:

      - a quote character, because on the fallback path the text failed to parse
        and a token can still carry the quote shlex would have consumed
        (``bash -c "git`` splits to the token ``"git``);
      - a LEADING BACKSLASH, because ``\\git push`` is the ordinary way to bypass
        a shell alias and is still an invocation of git.
    """
    return token.strip("\"'").lstrip("\\").rsplit("/", 1)[-1]


def could_invoke_git(text: str) -> bool:
    """True when a ``git`` token could occupy a COMMAND position in ``text``.

    This is the guard on the two CONSERVATIVE fallbacks below (a segment with
    unbalanced quotes, and an unterminated pseudo-heredoc). Both used to ask the
    raw-substring question ``"git" in seg``, which cannot tell a command from a
    word inside an argument.

    Named failure (2026-09-05, RossLabs Ambient Agent): a filing command shaped
    ``python3 file_to_operations_center.py --spec "…prose describing a git push…"``
    contained a ``<<`` in its prose, tripped the unterminated-heredoc branch, and
    matched ``"git" in cmd`` — so the pre-push security scan ran, and blocked, on
    a command that pushed nothing.

    The question asked here instead: after splitting on every character that could
    open a command position and dropping leading wrappers/assignments, does any
    fragment START with ``git``? An argument-position ``git`` answers no; a real
    ``nohup git push``, a ``git push`` on a later line, and a ``bash -c "git push"``
    all still answer yes. Conservatism is preserved where it is load-bearing —
    a miss would let a secret ship — and dropped only where the token provably
    is not a command.
    """
    for frag in _COMMAND_POSITION_SPLIT_RE.split(text):
        toks = frag.split()
        if not toks:
            continue
        stripped = _strip_leading_wrappers(toks)
        if stripped and _cmd_basename(stripped[0]) == "git":
            return True
        first = _cmd_basename(toks[0])
        # An arg-taking wrapper (`timeout 30 git push`) or a shell interpreter
        # (`bash -c "git push"`) hides the command position behind its own args,
        # so any `git` token in the fragment counts.
        if first in _WRAPPER_COMMANDS or first in _SHELL_INTERPRETERS:
            if any(_cmd_basename(t) == "git" for t in toks):
                return True
    return False


def classify_command(cmd: str) -> set[str]:
    """Return the set of {commit, push} subcommands genuinely invoked in ``cmd``.

    Heredoc bodies are stripped first; then each shell segment is shlex-parsed and only a
    real ``git`` call with a matching subcommand counts. A segment with unbalanced quotes
    can't be parsed — treat it conservatively as possibly containing both (never miss).
    """
    if not cmd or not cmd.strip():
        return set()
    found: set[str] = set()
    stripped, unterminated = strip_heredoc_bodies(cmd)
    if unterminated:
        # A `<<WORD` opener with no matching delimiter — our text-level detection likely
        # mis-fired on a quoted bit-shift and would have eaten a real command. Degrade to
        # the conservative both-subcommands trigger rather than risk a missed push/commit —
        # but only when the raw text could invoke git from a COMMAND position: a `git` that
        # only ever appears inside an argument cannot hide a push/commit in the eaten
        # region, so set() is provably safe and skips an idle scan.
        return set(SUBCOMMANDS_OF_INTEREST) if could_invoke_git(cmd) else set()
    for segment in _split_on_unquoted_operators(stripped):
        seg = segment.strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg)
        except ValueError:
            # Unbalanced quotes — cannot parse this segment. Stay conservative,
            # but only for a segment that could plausibly BE a git command.
            #
            # Same guard the heredoc branch already applies above: a segment that
            # cannot reach `git` from a COMMAND position cannot hide a git
            # subcommand no matter how it parses, so claiming both is not
            # conservatism, it is a false positive.
            #
            # Observed 2026-07-30: `psql "$URL" -tAc "select 'role: '||..."` — SQL
            # string literals use apostrophes, shlex rejects the segment, and this
            # branch reported commit+push on a command containing no git at all.
            # The pre-push security gate then hard-blocked a production migration.
            # Third sibling of the argument-data-as-command-structure defect, after
            # `git stash push` and the deploy-policy substring tests.
            #
            # The 2026-09-05 sibling narrowed `"git" in seg` to `could_invoke_git`:
            # a segment whose only `git` sits inside a `--spec "…"` argument used
            # to fire both gates, blocking a filing command that pushed nothing.
            if could_invoke_git(seg):
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
