#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Resolve only unambiguous post-push targets; never evaluate shell text.

Unlike a pre-action safety classifier, an observer must not guess a repository
on parse failure. Unsupported shell constructs leave the pre-push baton for
normal reconciliation instead of closing out the wrong project.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path


def push_target(command: str, cwd: Path) -> Path | None:
    if not command or any(c in command for c in "$`\n\r*?[]{}#"):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = [[]]
    separators = []
    for token in tokens:
        if token in ("&&", ";"):
            separators.append(token)
            segments.append([])
        elif token and all(c in ";&|()<>" for c in token):
            return None
        else:
            segments[-1].append(token)
    current = Path(os.path.abspath(cwd))
    targets = []
    for index, args in enumerate(segments):
        if not args:
            return None
        if args[0] == "cd":
            if len(args) != 2 or index >= len(separators) or separators[index] != "&&":
                return None
            if args[1].startswith(("-", "~")):
                return None
            if os.environ.get("CDPATH"):
                return None
            # Shell cd is logical by default: resolve '..' before following
            # symlinks. Git -C, below, performs physical chdir operations.
            current = Path(os.path.abspath(current / args[1]))
            if not current.is_dir():
                return None
            continue
        if args[0] in ("command", "exec"):
            args = args[1:]
        if not args or Path(args[0]).name != "git":
            return None
        target = current
        i = 1
        while i < len(args) and args[i].startswith("-"):
            option = args[i]
            if option == "-C":
                i += 1
                if i >= len(args) or args[i].startswith("~"):
                    return None
                target = (target / args[i]).resolve()
            elif option.startswith("-C"):
                if option[2:].startswith("~"):
                    return None
                target = (target / option[2:]).resolve()
            else:
                # -c, --git-dir, --work-tree and aliases can redirect work.
                return None
            i += 1
        if i >= len(args):
            return None
        if args[i] == "push":
            options = args[i + 1:]
            if "--" in options:
                options = options[:options.index("--")]
            if any(a == "--dry-run" or
                   (a.startswith("-") and not a.startswith("--") and "n" in a[1:])
                   for a in options):
                continue
            targets.append(target.resolve())
        elif args[i] not in {"add", "commit", "status", "diff", "fetch", "pull",
                             "switch", "checkout", "merge", "rebase", "log", "show"}:
            # Unknown commands may be user aliases with shell side effects.
            return None
    unique = set(targets)
    return unique.pop() if len(unique) == 1 else None


def main() -> None:
    try:
        event = json.load(sys.stdin)
        if event.get("hook_event_name") != "PostToolUse" or event.get("tool_name") != "Bash":
            return
        # Codex reports failed tools on PostToolUse too. Reuse the trace
        # adapter's host-specific failure contract instead of treating a failed
        # `commit && push` command as evidence that the push ran.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from tool_trace import codex_failure_phase
        if event.get("is_error") is True or codex_failure_phase("end", event) == "error":
            return
        command = event.get("tool_input", {}).get("command")
        cwd = event.get("cwd") or sys.argv[1]
        if not isinstance(command, str) or not isinstance(cwd, str):
            return
        target = push_target(command, Path(cwd))
        if target is not None:
            print(target)
    except (ValueError, TypeError, AttributeError, OSError, IndexError):
        return


if __name__ == "__main__":
    main()
