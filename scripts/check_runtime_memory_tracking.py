#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Fail when runtime memory/state directories are tracked.

The build-loop plugin repo is public. Project runtime state and memory must
stay local or be promoted into the private build-loop-memory repo. This guard
blocks accidental commits of those directories while allowing similarly named
public plugin folders such as `.claude-plugin/` and `.codex-plugin/`, plus a
small allowlist of distributable config files that live under an otherwise
runtime segment (e.g. `.codex/hooks.json`, the Codex counterpart to the tracked
`hooks/hooks.json`).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import PurePosixPath

BLOCKED_SEGMENTS = {
    ".build-loop",
    ".episodic",
    ".semantic",
    ".procedural",
    ".in_use",
    ".bookmark",
    ".claude",
    ".codex",
    ".agent-rally-point",
}

# Exact repo-relative paths intentionally tracked despite living under a blocked
# segment. Keep this tight — only distributable CONFIG, never runtime state.
ALLOWED_PATHS = {
    ".codex/hooks.json",  # Codex Stop/SessionStart hook wiring → hooks/closeout.sh
}


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
    )


def _repo_root() -> str:
    result = _git(["rev-parse", "--show-toplevel"])
    if result.returncode != 0 or not result.stdout.strip():
        print(
            "check_runtime_memory_tracking: not a git repo",
            file=sys.stderr,
        )
        sys.exit(2)
    return result.stdout.strip()


def _split_paths(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _staged_files() -> list[str]:
    # ACMR intentionally excludes deletions: removing previously tracked
    # runtime memory is the repair path this guard should permit.
    result = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    if result.returncode != 0:
        print(result.stderr.strip() or "cannot read staged files", file=sys.stderr)
        sys.exit(2)
    return _split_paths(result.stdout)


def _all_tracked_files() -> list[str]:
    result = _git(["ls-files"])
    if result.returncode != 0:
        print(result.stderr.strip() or "cannot list tracked files", file=sys.stderr)
        sys.exit(2)
    return _split_paths(result.stdout)


def _is_blocked_path(path: str) -> bool:
    if PurePosixPath(path).as_posix() in ALLOWED_PATHS:
        return False
    parts = PurePosixPath(path).parts
    return any(part in BLOCKED_SEGMENTS for part in parts)


def _paths_for_args(argv: list[str]) -> list[str]:
    if "--all" in argv:
        return _all_tracked_files()
    explicit = [arg for arg in argv if not arg.startswith("-")]
    if explicit:
        return explicit
    return _staged_files()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _repo_root()
    blocked = sorted(path for path in _paths_for_args(argv) if _is_blocked_path(path))
    if not blocked:
        return 0

    print(
        "check_runtime_memory_tracking: runtime memory/state files are tracked.",
        file=sys.stderr,
    )
    print(
        "Move durable content to the private build-loop-memory repo and keep "
        "project-local runtime state gitignored.",
        file=sys.stderr,
    )
    for path in blocked:
        print(f"  {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
