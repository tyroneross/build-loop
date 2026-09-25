#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reject a dispatch brief that cannot report back.

WHY
---
An agent that was never told where to report cannot report, and no amount of
prompting after launch fixes it. Two measured failures in one session:

  - Two watcher shells ran for TWO DAYS. Their exit condition polled
    `! pgrep -f "vitest run ..."`, which matched the watcher's OWN argv, so it
    could never become true. No iteration cap existed to end it.
  - A handoff document was written to `.build-loop/`, which is gitignored. It
    would have died with the machine, and nothing said so at the time.

Both are dispatch-time omissions, not runtime bugs. This lints the five fields
that make them impossible.

A separate class of dispatch-time omission is a brief that CAN write — Write,
Edit, Bash, NotebookEdit, or prose telling the agent to commit/push/edit files —
but never says what it must not do. Four incidents, one script each:

  - Auto mode blocked a hand-back tagged `[Unauthorized Persistence]` — an
    agent had installed itself to survive past its own task.
  - The owner's email went out in a crates.io `User-Agent` header on an
    outbound request nobody meant to identify them on.
  - An agent acknowledged a pre-push refusal and pushed anyway through its
    own ack path, instead of treating the refusal as terminal.
  - `codex exec`, launched non-interactively without `< /dev/null`, hangs
    waiting on stdin that will never arrive — observed, not hypothetical.

A write-capable brief without a `forbidden:` field is the same omission as an
absent `durable:` field: nobody decided, so nothing stops the agent from doing
any of the above.

THE `durable` FIELD IS THE POINT
--------------------------------
`durable: none` passes. An ABSENT `durable` fails. That asymmetry is deliberate:
one is a decision somebody made, the other is a decision nobody made, and only
the second produces a report that silently evaporates. Every other required
field works the same way — the lint asks you to choose, never to choose well.
`forbidden:` on a write-capable brief works the same way: `forbidden: default`
passes (it means "the standard four prohibitions below apply"), an absent field
fails, and a custom value only passes when it still covers all four.

    dispatch_brief_lint.py <brief.md> [...]      # exit 1 on any violation
    dispatch_brief_lint.py --json <brief.md>

Stdlib only. No network.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

__version__ = "1.0.0"

REQUIRED = ("goal", "max_iterations", "report_primary", "report_backup", "durable")

#: Paths some agent already reads. A brief pointing anywhere else is a message
#: addressed to a mailbox nobody checks.
KNOWN_BACKUPS = (
    ".build-loop/followup/",
    ".build-loop/briefs/",
    "inbox/",
    "build-loop-memory/",
)

#: The only tree that survives a clone. `.build-loop/` is gitignored.
DURABLE_ROOT = "build-loop-memory/"

#: A goal stated as a count is a bound wearing a goal's clothes — it says when to
#: stop, never what was accomplished.
COUNT_SHAPED = re.compile(
    r"^\s*(?:run|repeat|loop|try|iterate|poll)\b[^.]*\b\d+\s*(?:times?|x|iterations?)\b",
    re.IGNORECASE,
)

#: Frontmatter fields that name the agent's tool grant. Any of these is
#: checked for a write-capable tool name.
TOOL_FIELDS = ("tools", "allowed_tools", "allowed-tools")

#: A brief naming any of these tools can mutate the filesystem or run
#: arbitrary commands, which is what makes `forbidden:` mandatory for it.
WRITE_CAPABLE_TOOLS = ("write", "edit", "bash", "notebookedit")

#: Narrow, documented fallback for briefs whose write authority is only
#: described in prose rather than in a `tools:`/`write_capable:` field. Each
#: pattern requires an explicit instruction verb (commit/push/edit) next to a
#: change-related noun — it does not fire on incidental mentions of git or
#: editing elsewhere in the body. Frontmatter signals are checked first and
#: are preferred; this only runs when neither is present.
BODY_WRITE_INSTRUCTION = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:you (?:may|should|must|can)|please)\b[^.\n]{0,40}\b(?:commit|push|edit)\b",
        r"\bgit (?:commit|push)\b",
        r"\bcommit (?:your|the) (?:changes|files|work)\b",
        r"\bedit (?:the )?files?\b",
    )
)

#: The four standard prohibitions every write-capable brief must carry, one
#: way or another. `forbidden: default` means "these, verbatim". A custom
#: value must still cover each — checked here by keyword, single source of
#: truth for both the lint's own logic and the error message it prints.
STANDARD_PROHIBITIONS = (
    (
        "no persistence beyond the task (LaunchAgents, registries, fleet "
        "entries, cron jobs, login items)",
        ("launchagent", "registry", "registries", "fleet", "cron", "login item", "persist"),
    ),
    (
        "no owner identity (name, email, account ids) in outbound requests",
        ("owner identity", "identity", "email", "account id", "user-agent", "outbound"),
    ),
    (
        "no acknowledging, overriding, or skipping a safety gate",
        ("safety gate", "override", "overrid", "skip", "acknowledg"),
    ),
    (
        "commit only your own (owned) files",
        ("own files", "owned files", "own file", "your own", "commit only"),
    ),
)

#: `codex exec` blocks waiting on stdin when launched non-interactively — an
#: observed hang, not a theoretical one. `< /dev/null` (either spacing) is the
#: fix; a line naming `codex exec` without it is a problem per occurrence.
CODEX_EXEC_LINE = re.compile(r"codex exec\b")
DEV_NULL_STDIN = re.compile(r"<\s*/dev/null")
#: stdin already supplied by a pipe into codex, a heredoc, or a file redirect.
STDIN_SUPPLIED = re.compile(r"\|\s*codex exec\b|codex exec\b.*(?:<<|<\s*\S)")

_FM = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


def frontmatter(text: str) -> dict[str, str] | None:
    m = _FM.match(text)
    if not m:
        return None
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


def body_after_frontmatter(text: str) -> str:
    """Return the text following the frontmatter block (or all of it, if none)."""
    m = _FM.match(text)
    return text[m.end():] if m else text


def is_write_capable(fm: dict[str, str], body: str) -> bool:
    """True if the brief grants the agent the ability to mutate anything.

    Checked in order of preference: an explicit `write_capable: true`, then a
    tools field naming Write/Edit/Bash/NotebookEdit, then — only as a
    fallback — a narrow body-text instruction to commit/push/edit files.
    """
    if fm.get("write_capable", "").strip().lower() == "true":
        return True
    for field in TOOL_FIELDS:
        value = fm.get(field, "")
        if value and any(tool in value.lower() for tool in WRITE_CAPABLE_TOOLS):
            return True
    return any(pattern.search(body) for pattern in BODY_WRITE_INSTRUCTION)


def prohibitions_block() -> str:
    """The four standard prohibitions, formatted so an author can paste them."""
    return "\n".join(f"      {i}. {label}" for i, (label, _) in enumerate(STANDARD_PROHIBITIONS, 1))


NEGATION = re.compile(r"\b(?:no|not|never|don't|do not|must not|only|forbid\w*|prohibit\w*)\b")


def _negated_mention(text: str, keyword: str) -> bool:
    """True when `keyword` appears within 40 characters after a negation.

    A keyword alone is not a prohibition: "email ok" names the thing and permits
    it (independent-auditor f6, 2026-09-25).
    """
    for m in re.finditer(re.escape(keyword), text):
        if NEGATION.search(text[max(0, m.start() - 40):m.start() + len(keyword)]):
            return True
    return False


def check_forbidden(fm: dict[str, str], path: pathlib.Path) -> list[str]:
    """Validate `forbidden:` on a brief already determined to be write-capable."""
    value = fm.get("forbidden")
    if value is None:
        return [
            f"{path}: write-capable brief (can Write/Edit/Bash/NotebookEdit or "
            f"was told to commit/push/edit) is missing `forbidden`. Absent is "
            f"not a valid answer here either — write the literal word `default` "
            f"to apply the standard four prohibitions, or state your own that "
            f"covers all of:\n{prohibitions_block()}"
        ]
    if not value or value.startswith("<"):
        return [
            f"{path}: `forbidden` is still a placeholder ({value!r}). Write "
            f"`default` to apply the standard four:\n{prohibitions_block()}"
        ]
    if value.strip().lower() == "default":
        return []
    lowered = value.lower()
    missing = [
        label for label, keywords in STANDARD_PROHIBITIONS
        if not any(_negated_mention(lowered, k) for k in keywords)
    ]
    if not missing:
        return []
    missing_block = "\n".join(f"      - {label}" for label in missing)
    return [
        f"{path}: custom `forbidden` value does not cover:\n{missing_block}\n"
        f"    Either add coverage for those, or replace the value with the "
        f"literal word `default` to get all four:\n{prohibitions_block()}"
    ]


def check_codex_exec_stdin(text: str, path: pathlib.Path) -> list[str]:
    """`codex exec` without `< /dev/null` hangs waiting on stdin. Per line."""
    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if CODEX_EXEC_LINE.search(line) and not (
            DEV_NULL_STDIN.search(line) or STDIN_SUPPLIED.search(line)
        ):
            problems.append(
                f"{path}:{lineno}: `codex exec` with no `< /dev/null` on the "
                f"same line. Launched non-interactively it waits on stdin and "
                f"hangs — observed, not theoretical. Append `< /dev/null`."
            )
    return problems


def check(path: pathlib.Path) -> list[str]:
    """Return human-readable problems. Empty list means the brief can report."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: unreadable ({exc})"]

    fm = frontmatter(text)
    if fm is None:
        return [
            f"{path}: no frontmatter block. A dispatch brief declares its "
            f"completion contract in frontmatter so it can be checked before "
            f"launch, not discovered after. See templates/dispatch-brief.md."
        ]

    problems: list[str] = []

    for field in REQUIRED:
        if field not in fm:
            problems.append(
                f"{path}: missing `{field}`. Absent is not a valid answer — "
                f"an unstated destination is a decision nobody made."
            )
        elif not fm[field] or fm[field].startswith("<"):
            problems.append(f"{path}: `{field}` is still a placeholder ({fm[field]!r}).")

    if problems:
        return problems  # everything below reads fields that may not exist

    goal = fm["goal"]
    if COUNT_SHAPED.match(goal):
        problems.append(
            f"{path}: `goal` is stated as a count ({goal!r}). That is a BOUND, not "
            f"a goal — it says when to stop, never what was accomplished. State a "
            f"condition the agent can check: 'CI is green on main', not 'run 5 times'."
        )

    bound = fm["max_iterations"]
    if not bound.isdigit() or int(bound) < 1:
        problems.append(
            f"{path}: `max_iterations` must be a positive integer, got {bound!r}. "
            f"It terminates the loop even when the goal is unreachable — which is "
            f"not hypothetical: a poll whose condition matched its own process ran "
            f"two days."
        )

    backup = fm["report_backup"]
    if not any(k in backup for k in KNOWN_BACKUPS):
        problems.append(
            f"{path}: `report_backup` is {backup!r}, which no agent is known to "
            f"read. Use a path something already checks: "
            f"{', '.join(KNOWN_BACKUPS)}"
        )

    durable = fm["durable"]
    if durable.lower() != "none" and DURABLE_ROOT not in durable:
        problems.append(
            f"{path}: `durable` is {durable!r}. `.build-loop/` is GITIGNORED, so a "
            f"report written only there dies with the machine. Point at "
            f"{DURABLE_ROOT}..., or write the literal word `none` to record that "
            f"this output is deliberately session-scoped."
        )

    body = body_after_frontmatter(text)
    if is_write_capable(fm, body):
        problems.extend(check_forbidden(fm, path))
        problems.extend(check_codex_exec_stdin(text, path))

    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("briefs", nargs="+", type=pathlib.Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    problems: list[str] = []
    for brief in args.briefs:
        problems.extend(check(brief))

    if args.json:
        print(json.dumps({"ok": not problems, "problems": problems}, indent=2))
    elif problems:
        print("dispatch_brief_lint: this brief cannot report back\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("\nTemplate: templates/dispatch-brief.md", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
