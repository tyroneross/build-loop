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


def _instruction_files() -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in INSTRUCTION_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file():
                seen.setdefault(path, None)
    return sorted(seen)


def _offending_lines(path: Path, pattern: re.Pattern[str]) -> list[str]:
    out: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for number, line in enumerate(text.splitlines(), start=1):
        if not pattern.search(line):
            continue
        # A line that NAMES the lossy view or the unscoped clear in order to
        # FORBID it is the fix, not the defect. Kept to explicit forbidding
        # vocabulary so the exemption cannot quietly swallow a real instruction.
        if any(
            token in line
            for token in (
                "never `owed[]`", "not `owed[]`", "keep only the last row",
                "name-keyed", "is REFUSED", "An unscoped", "an unscoped",
            )
        ):
            continue
        out.append(f"{path.relative_to(ROOT)}:{number}")
    return out


def test_no_instruction_prescribes_an_unscoped_clear() -> None:
    offenders: list[str] = []
    for path in _instruction_files():
        offenders.extend(_offending_lines(path, UNSCOPED_CLEAR))
    assert offenders == [], (
        "these instructions prescribe `clear --verifier <name>` with no --run-id, "
        "which the CLI refuses whenever two runs owe that verifier:\n  "
        + "\n  ".join(offenders)
    )


def test_no_instruction_dispatches_off_the_name_keyed_view() -> None:
    offenders: list[str] = []
    for path in _instruction_files():
        offenders.extend(_offending_lines(path, LOSSY_DISPATCH))
    assert offenders == [], (
        "these instructions tell the agent to dispatch off `dispatch_commands[...]`, "
        "a name-keyed view that keeps only the last row per verifier; iterate "
        "`debts[]` instead:\n  " + "\n  ".join(offenders)
    )
