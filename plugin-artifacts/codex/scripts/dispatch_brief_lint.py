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

THE `durable` FIELD IS THE POINT
--------------------------------
`durable: none` passes. An ABSENT `durable` fails. That asymmetry is deliberate:
one is a decision somebody made, the other is a decision nobody made, and only
the second produces a report that silently evaporates. Every other required
field works the same way — the lint asks you to choose, never to choose well.

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
