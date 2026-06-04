# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""locate.py — find the Claude Code session transcript for a given cwd.

Claude Code stores transcripts at::

    ~/.claude/projects/<cwd-slug>/<session-uuid>.jsonl

where ``<cwd-slug>`` is the absolute working directory with ``/`` replaced by
``-`` (e.g. ``/Users/tyroneross/dev/git-folder/build-loop`` →
``-Users-tyroneross-dev-git-folder-build-loop``).

This module returns the **most-recently-modified** JSONL for the given cwd,
or None when none exists. It is the locator the ``transcript_pattern_miner``
package uses, exposed as a public helper for the retrospective agent.
"""
from __future__ import annotations

from pathlib import Path


def cwd_to_slug(cwd: Path | str) -> str:
    """Convert an absolute cwd to its Claude Code slug.

    The slug is the absolute path with leading slash stripped and remaining
    ``/`` replaced by ``-`` (Claude Code's convention).
    """
    p = Path(cwd).resolve()
    abs_str = str(p)
    # Strip the leading '/' (POSIX absolute path) so the slug starts with '-'.
    if abs_str.startswith("/"):
        abs_str = abs_str[1:]
    return "-" + abs_str.replace("/", "-")


def sessions_root() -> Path:
    """Return the Claude Code sessions root (``~/.claude/projects/``)."""
    return Path.home() / ".claude" / "projects"


def find_transcript_for_cwd(cwd: Path | str) -> Path | None:
    """Return the most-recently-modified JSONL for ``cwd``, or None.

    Args:
        cwd: absolute working directory of the build-loop run.

    Returns:
        Path to the JSONL transcript, or None if no transcript directory or
        no JSONL files exist for this cwd.

    Never raises — IO errors return None.
    """
    try:
        slug = cwd_to_slug(cwd)
        root = sessions_root() / slug
        if not root.is_dir():
            return None
        jsonls = sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return jsonls[0] if jsonls else None
    except (OSError, ValueError):
        return None
