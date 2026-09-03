#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Reject ad-hoc unbounded CPU probes and route callers to the owned supervisor."""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

_JS_INFINITE = re.compile(r"\bwhile\s*\(\s*true\s*\)|\bfor\s*\(\s*;\s*;\s*\)", re.IGNORECASE)
_PYTHON_INFINITE = re.compile(r"\bwhile\s+True\s*:", re.IGNORECASE)


def _segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = ""
    segments: list[list[str]] = [[]]
    for token in lexer:
        if token and all(char in ";&|" for char in token):
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token)
    return [segment for segment in segments if segment]


def _eval_script(segment: list[str]) -> tuple[str, str] | None:
    if not segment:
        return None
    executable = os.path.basename(segment[0])
    if executable == "build-loop-load-probe":
        return ("trusted", "")
    if executable == "node" and "-e" in segment:
        index = segment.index("-e")
        return ("javascript", segment[index + 1] if index + 1 < len(segment) else "")
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable, re.IGNORECASE) and "-c" in segment:
        index = segment.index("-c")
        return ("python", segment[index + 1] if index + 1 < len(segment) else "")
    return None


def evaluate(command: str) -> str | None:
    if not command:
        return None
    try:
        scripts = [_eval_script(segment) for segment in _segments(command)]
    except ValueError:
        return None
    unsafe = False
    for parsed in scripts:
        if not parsed or parsed[0] == "trusted":
            continue
        language, script = parsed
        infinite = _JS_INFINITE.search(script) if language == "javascript" else _PYTHON_INFINITE.search(script)
        if infinite:
            unsafe = True
            break
    if not unsafe:
        return None
    return (
        "This command starts an unbounded synthetic workload with no owned "
        "deadline or verified cleanup. Run the bounded supervisor instead:\n\n"
        "  build-loop-load-probe --workers 4 --duration-seconds 30 -- <test command>\n\n"
        "Every worker then has a visible run-correlated title, an internal hard "
        "deadline, and a sanitized cleanup receipt."
    )


def _command(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command")
    return command if isinstance(command, str) else ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        message = evaluate(_command(payload))
    except Exception:
        print("{}")
        return 0
    print("{}")
    if message:
        sys.stderr.write("[build-loop] unbounded CPU load rejected.\n\n" + message + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
