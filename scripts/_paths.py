#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
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

# Global build-loop memory root — separate from the decisions store. Holds
# constitution.md, MEMORY.md, free-form lessons (feedback/pattern/reference),
# and (per memory consolidation PR-series) the projects/<slug>/ subtree.
DEFAULT_BUILD_LOOP_MEMORY_ROOT = "~/.build-loop/memory"

# Sub-component patterns: paths under a project that count as a distinct
# slug. Today: just `workers/`. Extend by adding entries; order matters
# (longest match wins).
SUBCOMPONENT_PATTERNS: tuple[str, ...] = ("workers",)


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


# ---------------------------------------------------------------------------
# Build-loop memory paths (global + per-project segmentation).
#
# Per the memory-consolidation PR series (2026-05-13):
#   - ~/.build-loop/memory/                   global root (existing)
#   - ~/.build-loop/memory/projects/<slug>/   project-scoped lessons (NEW)
#   - ~/.build-loop/memory/projects/_archive/<slug>/   retired projects
#
# PR 1 (read-path tolerance, 2026-05-13) added the projects/<slug>/ tier
# while still reading the legacy per-repo location. PR 2 cut writes over.
# PR 3 (2026-05-13) removed the legacy read shim — the consolidated tree
# is now the only path read. Slug derivation reuses _safe_project_tag so
# the same normalization applies to filesystem dirs AND the Postgres
# semantic_facts.project column (no split-brain by construction).
# ---------------------------------------------------------------------------


def build_loop_memory_root() -> Path:
    """Return the global build-loop memory root (``~/.build-loop/memory``).

    Reads ``$BUILD_LOOP_MEMORY_ROOT`` (expanded for ``~``) and falls back
    to ``~/.build-loop/memory``. Path is not required to exist; callers
    that need the directory should create it (typically via
    ``scripts/install_memory.py``).
    """
    raw = os.environ.get("BUILD_LOOP_MEMORY_ROOT") or DEFAULT_BUILD_LOOP_MEMORY_ROOT
    return Path(os.path.expanduser(raw))


def project_memory_root() -> Path:
    """Return ``<build_loop_memory_root()>/projects``."""
    return build_loop_memory_root() / "projects"


def project_memory_dir_for_project(project: str) -> Path:
    """Return ``project_memory_root() / <project>`` (validated).

    Validates ``project`` against ``_safe_project_tag`` to prevent
    directory traversal — same validation used for the decisions store.
    Empty strings collapse to ``_unscoped``.

    Sub-component slugs containing ``/`` (e.g. ``decision-doctor-cc/workers``)
    are split and each segment validated individually so the path joins
    cleanly. Resulting path is asserted to be rooted under
    ``project_memory_root()`` to defend against symlink-based escapes.
    """
    if not project:
        project = "_unscoped"
    # Split on '/' to allow sub-component slugs like "<project>/workers".
    parts = project.split("/")
    for p in parts:
        _safe_project_tag(p)  # raises ValueError on unsafe segment
    candidate_rel = Path(*parts)
    candidate = (project_memory_root() / candidate_rel).resolve()
    root_resolved = project_memory_root().resolve()
    rel = str(candidate)
    root_str = str(root_resolved)
    if not (rel == root_str or rel.startswith(root_str + os.sep)):
        raise ValueError(
            f"project tag {project!r} resolves outside project_memory_root()"
        )
    return project_memory_root() / candidate_rel


def archive_memory_dir(project: str) -> Path:
    """Return ``project_memory_root() / _archive / <project>`` (validated).

    Same safety contract as ``project_memory_dir_for_project`` — used for
    retired projects whose memory should remain queryable.
    """
    if not project:
        raise ValueError("archive_memory_dir requires a non-empty project tag")
    parts = project.split("/")
    for p in parts:
        _safe_project_tag(p)
    candidate_rel = Path("_archive", *parts)
    candidate = (project_memory_root() / candidate_rel).resolve()
    root_resolved = project_memory_root().resolve()
    rel = str(candidate)
    root_str = str(root_resolved)
    if not (rel == root_str or rel.startswith(root_str + os.sep)):
        raise ValueError(
            f"archive project tag {project!r} resolves outside project_memory_root()"
        )
    return project_memory_root() / candidate_rel


def derive_slug_from_cwd(cwd: Path | str | None = None) -> str:
    """Derive the project slug from a working directory.

    Algorithm (deterministic; alignment with Postgres semantic_facts.project
    is guaranteed by routing every slug through ``_safe_project_tag``):

      1. Resolve symlinks via ``Path.resolve()`` so a session running in
         ``~/.claude/plugins/build-loop`` resolves to ``~/dev/git-folder/build-loop``.
      2. Walk up looking for a ``.git`` entry (file OR dir — worktrees use
         a file). The deepest enclosing ``.git`` defines the repo root.
      3. Slug base = ``basename(repo_root)`` lowercased; non-safe chars
         collapsed to ``-``; runs of ``-`` collapsed; leading/trailing
         ``-`` stripped; capped at 64 chars.
      4. If ``cwd`` is N levels below repo_root AND the first level matches
         a ``SUBCOMPONENT_PATTERNS`` entry, append ``/<subcomponent>`` to
         the slug. Today only ``workers`` is recognized.
      5. If no ``.git`` is found in the ancestry, return ``_unscoped``.
      6. Final slug is run through ``_safe_project_tag`` (each segment
         independently for sub-component slugs).

    Returns the slug string. Never raises (returns ``_unscoped`` on
    ambiguity); callers that need to know whether resolution succeeded
    can check for the ``_unscoped`` sentinel.
    """
    if cwd is None:
        cwd = Path.cwd()
    try:
        cwd_resolved = Path(os.path.expanduser(str(cwd))).resolve()
    except (OSError, RuntimeError):
        return "_unscoped"

    # Walk up looking for .git
    repo_root: Path | None = None
    cursor = cwd_resolved
    seen: set[str] = set()
    while True:
        key = str(cursor)
        if key in seen:
            break  # symlink cycle guard
        seen.add(key)
        if (cursor / ".git").exists():
            repo_root = cursor
            break
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent

    if repo_root is None:
        return "_unscoped"

    # Slug base from repo basename
    base = repo_root.name.lower()
    base = re.sub(r"[^a-z0-9._-]", "-", base)
    base = re.sub(r"-{2,}", "-", base).strip("-")
    base = base[:64] or "_unscoped"

    # Sub-component detection: is cwd under <repo_root>/<sub>/...?
    try:
        rel = cwd_resolved.relative_to(repo_root)
    except ValueError:
        rel = Path()
    if rel.parts:
        first = rel.parts[0].lower()
        if first in SUBCOMPONENT_PATTERNS:
            sub_safe = re.sub(r"[^a-z0-9._-]", "-", first).strip("-")[:64]
            if sub_safe:
                slug = f"{base}/{sub_safe}"
                # Validate each segment
                for seg in slug.split("/"):
                    try:
                        _safe_project_tag(seg)
                    except ValueError:
                        return "_unscoped"
                return slug

    try:
        _safe_project_tag(base)
    except ValueError:
        return "_unscoped"
    return base
