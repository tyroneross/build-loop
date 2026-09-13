#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Docs-as-shipped-surface lint for the owed-verification contract.

The Python was re-keyed to one debt row per (verifier, run_id), and the
instruction layer that actually drives the parent agent was not. Four markdown
files plus one paragraph sixty-nine lines below the paragraph that asserted the
opposite still told the agent to dispatch off `owed[]` / `dispatch_commands[...]`
and to clear without `--run-id`. Those views are name-keyed and keep only the
last row, so on a two-run manifest the parent dispatches ONE audit against the
WRONG diff and treats both debts as paid -- the exact silent under-review the
mechanism exists to close.

An agent executes these files. A contract that holds in the code and not in the
instructions is a contract the agent does not follow, so the docs are linted
against the shipped behaviour rather than trusted to stay in step.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files that INSTRUCT an agent about the owed-verification lifecycle.
INSTRUCTION_GLOBS = ("agents/*.md", "references/*.md", "skills/**/*.md")

# `clear --verifier <name>` with no `--run-id` on the same line. Refused by the
# CLI whenever two runs owe the verifier, so an instruction that prescribes it
# is telling the agent to run a command that exits 2.
UNSCOPED_CLEAR = re.compile(r"clear[^\n`]*--verifier(?![^\n`]*--run-id)")

# Telling the agent to READ a name-keyed view to decide a dispatch. Mentioning
# the field while explaining that it is lossy is fine; the negative lookahead
# keeps this a lint on instructions, not on prose about them.
LOSSY_DISPATCH = re.compile(r"dispatch_commands\[")

# Telling the agent to ITERATE the de-duplicated name list. The docstring names
# this as one of the three drift modes and nothing linted it.
LOSSY_ITERATION = re.compile(r"(?:each|every|for each)[^\n`]{0,40}`owed\[\]`")


def _instruction_files() -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in INSTRUCTION_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file():
                seen.setdefault(path, None)
    return sorted(seen)


# A line that NAMES the pattern in order to FORBID it is the fix, not the
# defect. The exemption is scoped to a window around the match, because a
# markdown line here is a whole paragraph: checking the WHOLE line let
# vocabulary borrowed from an unrelated sentence launder a live prescription,
# and the lint sat green over the exact violation it was built to catch.
EXEMPTION_WINDOW = 120

# An exemption must be ABOUT THE PATTERN IT EXEMPTS. A shared token list let
# vocabulary from an unrelated sentence -- "name-keyed", which is about the
# derived views -- launder a live `clear --verifier` prescription on the same
# markdown paragraph, and the lint sat green over the exact violation it was
# built to catch. Each pattern therefore carries its own forbidding vocabulary,
# matched within a window of the hit rather than anywhere on the line.
EXEMPTIONS: dict[str, tuple[str, ...]] = {
    "clear": ("is REFUSED", "unscoped `clear", "refuses an ambiguous"),
    "views": ("never `owed[]`", "not `owed[]`", "keep only the last row", "name-keyed"),
}


def _offending_lines(
    path: Path, pattern: re.Pattern[str], exemption: str
) -> list[str]:
    tokens = EXEMPTIONS[exemption]
    out: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for number, line in enumerate(text.splitlines(), start=1):
        for match in pattern.finditer(line):
            window = line[
                max(0, match.start() - EXEMPTION_WINDOW):
                match.end() + EXEMPTION_WINDOW
            ]
            if any(token in window for token in tokens):
                continue
            try:
                label = str(path.relative_to(ROOT))
            except ValueError:
                label = str(path)  # a planted fixture outside the repo
            out.append(f"{label}:{number}")
            break
    return out


def test_no_instruction_prescribes_an_unscoped_clear() -> None:
    offenders: list[str] = []
    for path in _instruction_files():
        offenders.extend(_offending_lines(path, UNSCOPED_CLEAR, "clear"))
    assert offenders == [], (
        "these instructions prescribe `clear --verifier <name>` with no --run-id, "
        "which the CLI refuses whenever two runs owe that verifier:\n  "
        + "\n  ".join(offenders)
    )


def test_no_instruction_iterates_the_deduplicated_owed_list() -> None:
    offenders: list[str] = []
    for path in _instruction_files():
        offenders.extend(_offending_lines(path, LOSSY_ITERATION, "views"))
    assert offenders == [], (
        "these instructions tell the agent to iterate `owed[]`, which de-duplicates "
        "two runs owing one verifier into a single entry; iterate `debts[]` "
        "instead:\n  " + "\n  ".join(offenders)
    )


def test_the_lint_catches_a_planted_stale_instruction(tmp_path: Path) -> None:
    """The guard must be red on the shape it exists for, including one whose
    line also carries the exemption vocabulary -- that laundering is exactly how
    it sat green over a live violation."""
    planted = tmp_path / "planted.md"
    planted.write_text(
        # The `clear` prescription sits on a line that ALSO carries the views
        # vocabulary. That laundering is exactly how the lint sat green over a
        # live violation, so the exemption must not fire across patterns.
        "Read `debts[]`, never `owed[]`, then run `owed_verification.py clear "
        "--verifier cross-vendor-audit --reason \"x\"`.\n"
        "Dispatch each `dispatch_commands[verifier]` verbatim.\n",
        encoding="utf-8",
    )
    assert _offending_lines(planted, UNSCOPED_CLEAR, "clear") != []
    assert _offending_lines(planted, LOSSY_DISPATCH, "views") != []

    # And the exemption still works when it is genuinely about the pattern.
    forbidding = tmp_path / "forbidding.md"
    forbidding.write_text(
        "An unscoped `clear --verifier <name>` is REFUSED when two runs owe it.\n",
        encoding="utf-8",
    )
    assert _offending_lines(forbidding, UNSCOPED_CLEAR, "clear") == []


def test_no_instruction_dispatches_off_the_name_keyed_view() -> None:
    offenders: list[str] = []
    for path in _instruction_files():
        offenders.extend(_offending_lines(path, LOSSY_DISPATCH, "views"))
    assert offenders == [], (
        "these instructions tell the agent to dispatch off `dispatch_commands[...]`, "
        "a name-keyed view that keeps only the last row per verifier; iterate "
        "`debts[]` instead:\n  " + "\n  ".join(offenders)
    )
