#!/usr/bin/env python3
"""Global path/schema resolver for the episodic memory framework.

This module is the *only* place where legacy path/schema literals live
outside of test fixtures. Every other writer/reader script imports the
helpers below and goes through them.

Environment variable contract:
- ``$AGENT_MEMORY_ROOT``     : override the agent_memory root directory.
                                Defaults to ``~/dev/git-folder/build-loop-memory``.
- ``$AGENT_MEMORY_SCHEMA``   : override the default Postgres schema.
                                Defaults to ``personal_memory``.
- ``$AGENT_MEMORY_DUAL_WRITE``: when set to ``"1"``, writers must produce
                                BOTH the legacy artifact (``<repo>/.episodic/decisions/``,
                                ``build_loop_memory.semantic_facts``) AND the
                                new artifact (``<root>/decisions/<project>/``,
                                ``personal_memory.semantic_facts``).

Cutover lock:
- ``/tmp/agent-memory-cutover.lock`` (exists) → ``write_decision.py``
  prints ``cutover in progress, skipping`` and exits 0 with no writes.

These functions are pure and side-effect-free except for environment
inspection. They never mkdir or touch files; callers handle creation.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Legacy fallback constants. This is the *only* file outside test fixtures
# that may name these literals. The drift gate (Acceptance criterion 6)
# greps for them and excludes this file.
# ---------------------------------------------------------------------------
LEGACY_SCHEMA = "build_loop_memory"
LEGACY_DECISIONS_REL = ".episodic/decisions"

DEFAULT_AGENT_MEMORY_ROOT = "~/dev/git-folder/build-loop-memory"
DEFAULT_SCHEMA = "personal_memory"

CUTOVER_LOCK_PATH = "/tmp/agent-memory-cutover.lock"


def agent_memory_root() -> Path:
    """Return the root of the global agent_memory store.

    Reads ``$AGENT_MEMORY_ROOT`` (expanded for ``~``) and falls back to
    ``~/dev/git-folder/build-loop-memory``. Path is not required to
    exist; callers that need the directory should create it.
    """
    raw = os.environ.get("AGENT_MEMORY_ROOT") or DEFAULT_AGENT_MEMORY_ROOT
    return Path(os.path.expanduser(raw))


def decisions_root() -> Path:
    """Return ``<agent_memory_root()>/decisions``."""
    return agent_memory_root() / "decisions"


# Project tag whitelist: alphanumerics, underscore, dash, dot. No path
# separators, no leading dot+dot, no leading slash. Length 1..127. The
# leading underscore allowance covers the canonical ``_unscoped`` tag.
_SAFE_PROJECT_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def _safe_project_tag(tag: str) -> str:
    """Return ``tag`` if it is safe to use as a directory name.

    Rejects path-traversal sequences (``..``, ``/``, ``\\``) and
    suspicious characters that could escape the decisions tree on
    case-insensitive or symlink-following filesystems.
    """
    if not tag or not _SAFE_PROJECT_TAG_RE.match(tag) or tag in {".", ".."}:
        raise ValueError(f"unsafe project tag: {tag!r}")
    return tag


def decisions_dir_for_project(project: str) -> Path:
    """Return ``decisions_root() / <project>``.

    Validates ``project`` against ``_safe_project_tag`` to prevent
    directory traversal. Empty strings collapse to ``_unscoped``.
    Then asserts the resolved path is rooted under ``decisions_root()``
    to defend against symlink-based escapes.
    """
    if not project:
        project = "_unscoped"
    safe = _safe_project_tag(project)
    candidate = (decisions_root() / safe).resolve()
    root_resolved = decisions_root().resolve()
    # Path.is_relative_to was added in 3.9; fall back to startswith on str.
    rel = str(candidate)
    root_str = str(root_resolved)
    if not (rel == root_str or rel.startswith(root_str + os.sep)):
        raise ValueError(
            f"project tag {project!r} resolves outside decisions_root()"
        )
    return decisions_root() / safe


def legacy_decisions_dir(workdir: Path) -> Path:
    """Return ``<workdir>/.episodic/decisions`` (the per-repo legacy path)."""
    return Path(workdir) / ".episodic" / "decisions"


def default_schema() -> str:
    """Return the default Postgres schema for the new system.

    Reads ``$AGENT_MEMORY_SCHEMA``, falls back to ``personal_memory``.
    """
    return os.environ.get("AGENT_MEMORY_SCHEMA") or DEFAULT_SCHEMA


def legacy_schema() -> str:
    """Return the legacy Postgres schema name (``build_loop_memory``).

    Used during the dual-write transitional window. There is no env-var
    override for the legacy schema — Phase D removes it entirely.
    """
    return LEGACY_SCHEMA


def dual_write_enabled() -> bool:
    """Return True iff ``$AGENT_MEMORY_DUAL_WRITE`` is set to ``"1"``."""
    return os.environ.get("AGENT_MEMORY_DUAL_WRITE") == "1"


def cutover_lock_active() -> bool:
    """Return True iff the cutover lock file exists.

    Writers must check this at the *very top* of their entry point and
    exit cleanly when active.
    """
    return Path(CUTOVER_LOCK_PATH).exists()
