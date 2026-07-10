#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Global path/schema resolver for the build-loop memory framework.

This module is the active path contract for durable build-loop memory.
Every writer/reader script should import helpers from here instead of
embedding store paths directly.

Environment variable contract:
- ``$BUILD_LOOP_MEMORY_STORE_ROOT``: override the build-loop-memory root.
- ``$BUILD_LOOP_MEMORY_ROOT``: compatibility override for the same root.
- ``$AGENT_MEMORY_ROOT``     : compatibility override for the same root.

Default resolution (when no env override is set), in order:
  1. If the legacy personal root ``~/dev/git-folder/build-loop-memory``
     EXISTS on disk, keep using it. This makes machines that predate the
     neutral default keep working with zero configuration.
  2. Otherwise use the neutral per-user default ``~/.build-loop-memory``
     (``.<toolname>`` storage convention). Fresh installs land here — no
     author-specific directory layout is ever encoded.
- ``$AGENT_MEMORY_SCHEMA``   : override the default Postgres schema.
                                Defaults to ``personal_memory``.
- ``$AGENT_MEMORY_DUAL_WRITE``: when set to ``"1"``, writers must produce
                                BOTH the legacy artifact (``<repo>/.episodic/decisions/``,
                                ``build_loop_memory.semantic_facts``) AND the
                                new artifact (``<root>/projects/<project>/decisions/``,
                                ``personal_memory.semantic_facts``).

Cutover lock:
- ``/tmp/agent-memory-cutover.lock`` (exists) → ``write_decision.py``
  prints ``cutover in progress, skipping`` and exits 0 with no writes.

These functions are pure and side-effect-free except for environment
inspection. They never mkdir or touch files; callers handle creation.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Legacy fallback constants. These are for migration/archive tooling only.
# Active build-loop readers and writers should use the canonical helpers
# below (`project_decisions_dir`, `project_lessons_dir`, top-level lanes).
# ---------------------------------------------------------------------------
LEGACY_SCHEMA = "build_loop_memory"
LEGACY_DECISIONS_REL = ".episodic/decisions"

# Neutral per-user default for fresh installs. Matches the `.<toolname>`
# storage convention. NOTE: this is the HYPHENATED `~/.build-loop-memory`,
# deliberately NOT the retired `~/.build-loop/memory` (see the legacy-root
# note further down — that exact path has migration tooling that would fight
# a new write there).
NEUTRAL_MEMORY_STORE_ROOT = "~/.build-loop-memory"
# Legacy personal default. Predates the neutral default; kept as the resolved
# root when it already exists on disk so existing machines need zero config.
LEGACY_PERSONAL_MEMORY_STORE_ROOT = "~/dev/git-folder/build-loop-memory"
# Back-compat constant name. `DEFAULT_MEMORY_STORE_ROOT` now names the NEUTRAL
# default (what a fresh install gets). Resolution at call time still prefers an
# existing legacy root — see `memory_store_root()`.
DEFAULT_MEMORY_STORE_ROOT = NEUTRAL_MEMORY_STORE_ROOT
DEFAULT_AGENT_MEMORY_ROOT = DEFAULT_MEMORY_STORE_ROOT
DEFAULT_SCHEMA = "personal_memory"

CUTOVER_LOCK_PATH = "/tmp/agent-memory-cutover.lock"

# Historical name retained as a compatibility alias. The active root is now
# the sibling `build-loop-memory` repository (or the neutral fresh-install
# default), not `~/.build-loop/memory`.
DEFAULT_BUILD_LOOP_MEMORY_ROOT = DEFAULT_MEMORY_STORE_ROOT

# Sub-component patterns: paths under a project that count as a distinct
# slug. Today: just `workers/`. Extend by adding entries; order matters
# (longest match wins).
SUBCOMPONENT_PATTERNS: tuple[str, ...] = ("workers",)


def memory_store_root() -> Path:
    """Return the canonical build-loop-memory root.

    Resolution order:
      1. ``$BUILD_LOOP_MEMORY_STORE_ROOT`` / ``$BUILD_LOOP_MEMORY_ROOT`` /
         ``$AGENT_MEMORY_ROOT`` env override (first set wins, ``~`` expanded).
      2. The legacy personal default ``~/dev/git-folder/build-loop-memory``
         when it EXISTS on disk — keeps pre-neutral-default machines working
         with zero config.
      3. The neutral per-user default ``~/.build-loop-memory`` for fresh
         installs.

    The returned path is not required to exist (only the legacy branch checks
    existence); callers that need the directory should create it.
    """
    env_raw = (
        os.environ.get("BUILD_LOOP_MEMORY_STORE_ROOT")
        or os.environ.get("BUILD_LOOP_MEMORY_ROOT")
        or os.environ.get("AGENT_MEMORY_ROOT")
    )
    if env_raw:
        return Path(os.path.expanduser(env_raw))

    legacy = Path(os.path.expanduser(LEGACY_PERSONAL_MEMORY_STORE_ROOT))
    if legacy.exists():
        return legacy

    return Path(os.path.expanduser(NEUTRAL_MEMORY_STORE_ROOT))


def agent_memory_root() -> Path:
    """Compatibility alias for ``memory_store_root()``."""
    return memory_store_root()


def decisions_root() -> Path:
    """Return the legacy pre-cutover decisions root.

    This helper exists for migration/archive tooling that still needs to
    inventory ``<memory_store_root()>/decisions``. Active writers should use
    ``project_decisions_dir(project)``.
    """
    return memory_store_root() / "decisions"


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


def _safe_project_relpath(project: str) -> Path:
    """Return a validated relative path for a project tag.

    Empty strings collapse to ``_unscoped``. Slash-separated subcomponent
    slugs are supported, with every segment validated independently.
    """
    if not project:
        project = "_unscoped"
    parts = project.split("/")
    for part in parts:
        _safe_project_tag(part)
    return Path(*parts)


def _safe_child(root: Path, relpath: Path, label: str) -> Path:
    """Return ``root / relpath`` after checking it resolves below root."""
    candidate = (root / relpath).resolve()
    root_resolved = root.resolve()
    # Path.is_relative_to was added in 3.9; fall back to startswith on str.
    rel = str(candidate)
    root_str = str(root_resolved)
    if not (rel == root_str or rel.startswith(root_str + os.sep)):
        raise ValueError(f"{label} resolves outside {root}")
    return root / relpath


def project_root(project: str) -> Path:
    """Return ``<memory_store_root()>/projects/<project>`` (validated)."""
    return _safe_child(memory_store_root() / "projects", _safe_project_relpath(project), "project")


def project_decisions_dir(project: str) -> Path:
    """Return ``<project_root(project)>/decisions``."""
    return project_root(project) / "decisions"


def project_lessons_dir(project: str) -> Path:
    """Return ``<project_root(project)>/lessons``."""
    return project_root(project) / "lessons"


def project_research_dir(project: str) -> Path:
    """Return ``<project_root(project)>/research`` for pre-decision research packets."""
    return project_root(project) / "research"


def project_raw_dir(project: str) -> Path:
    """Return ``<project_root(project)>/raw`` for verbatim source material."""
    return project_root(project) / "raw"


def project_raw_documents_dir(project: str) -> Path:
    """Return ``<project_raw_dir(project)>/documents``."""
    return project_raw_dir(project) / "documents"


def project_raw_files_dir(project: str) -> Path:
    """Return ``<project_raw_dir(project)>/files``."""
    return project_raw_dir(project) / "files"


def project_raw_artifacts_dir(project: str) -> Path:
    """Return ``<project_raw_dir(project)>/artifacts``."""
    return project_raw_dir(project) / "artifacts"


def project_debugging_dir(project: str) -> Path:
    """Return ``<project_root(project)>/debugging``."""
    return project_root(project) / "debugging"


def project_design_dir(project: str) -> Path:
    """Return ``<project_root(project)>/design``."""
    return project_root(project) / "design"


def project_product_dir(project: str) -> Path:
    """Return ``<project_root(project)>/product``."""
    return project_root(project) / "product"


def project_architecture_dir(project: str) -> Path:
    """Return ``<project_root(project)>/architecture``."""
    return project_root(project) / "architecture"


def top_level_lessons_dir() -> Path:
    """Return ``<memory_store_root()>/lessons``."""
    return memory_store_root() / "lessons"


def top_level_debugging_dir() -> Path:
    """Return ``<memory_store_root()>/debugging``."""
    return memory_store_root() / "debugging"


def top_level_design_dir() -> Path:
    """Return ``<memory_store_root()>/design``."""
    return memory_store_root() / "design"


def top_level_product_dir() -> Path:
    """Return ``<memory_store_root()>/product``."""
    return memory_store_root() / "product"


def top_level_architecture_dir() -> Path:
    """Return ``<memory_store_root()>/architecture``."""
    return memory_store_root() / "architecture"


def memory_indexes_dir() -> Path:
    """Return ``<memory_store_root()>/indexes``."""
    return memory_store_root() / "indexes"


def decisions_dir_for_project(project: str) -> Path:
    """Compatibility alias for ``project_decisions_dir(project)``."""
    return project_decisions_dir(project)


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
# HISTORICAL (the `~/.build-loop/memory/` paths below are RETIRED — do not
# reintroduce them as a default). The memory-consolidation PR series
# (2026-05-13) originally rooted the store at `~/.build-loop/memory/`:
#   - ~/.build-loop/memory/                   global root
#   - ~/.build-loop/memory/projects/<slug>/   project-scoped lessons
#   - ~/.build-loop/memory/projects/_archive/<slug>/   retired projects
#
# PR 1 (read-path tolerance, 2026-05-13) added the projects/<slug>/ tier
# while still reading the legacy per-repo location. PR 2 cut writes over.
# PR 3 (2026-05-13) removed the legacy read shim — the consolidated tree
# is now the only path read. Slug derivation reuses _safe_project_tag so
# the same normalization applies to filesystem dirs AND the Postgres
# semantic_facts.project column (no split-brain by construction).
#
# The ACTIVE root is whatever `memory_store_root()` returns: an env override,
# else the legacy personal `~/dev/git-folder/build-loop-memory` if present,
# else the neutral fresh-install default `~/.build-loop-memory` (hyphenated,
# distinct from the retired `~/.build-loop/memory`). All helpers below build
# on `memory_store_root()`, so they follow that resolution automatically.
# ---------------------------------------------------------------------------


def build_loop_memory_root() -> Path:
    """Compatibility alias for ``memory_store_root()``."""
    return memory_store_root()


def project_memory_root() -> Path:
    """Return ``<memory_store_root()>/projects``."""
    return memory_store_root() / "projects"


def project_memory_dir_for_project(project: str) -> Path:
    """Compatibility alias for ``project_root(project)``."""
    return project_root(project)


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


def _canonical_repo_root(cwd_path: Path) -> Path | None:
    """Return the canonical (worktree-independent) repo root for ``cwd_path``.

    Runs ``git rev-parse --git-common-dir`` scoped to ``cwd_path``. A worktree's
    git-common-dir points at the *canonical* repo's ``.git`` (shared by the main
    checkout + every ``git worktree``), so the parent of the resolved common-dir
    is identical from anywhere in the repo family. Returns ``None`` when the
    invocation fails, output is empty, or resolution raises. Never raises.

    DRY note: ``rally_point/channel_paths.py`` carries a sibling copy used for the
    channel slug. ``_paths`` is the lower-level module (``channel_paths`` imports
    FROM it), so the two cannot share without a circular import; consolidating is
    tracked as a follow-up. Behaviour is identical.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(cwd_path),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    if not out:
        return None
    common = Path(out)
    if not common.is_absolute():
        common = cwd_path / common
    try:
        return common.resolve().parent
    except (OSError, RuntimeError):
        return None


MEMORY_SLUG_PIN_KEY = "memoryProjectSlug"


def _read_pinned_slug(repo_root: Path) -> str | None:
    """Read the durable memory-project-slug PIN from a repo's config.

    Field: ``<repo_root>/.build-loop/config.json`` -> top-level
    ``"memoryProjectSlug"`` (camelCase, matching the existing ``selfReview``
    / ``deploymentPolicy`` top-level keys in that file).

    This is the anchor that survives a ``basename(repo_root)`` rename: the
    2026-07-09 control-plane RCA (P0-4) found renaming
    ``RossLabs-AI-Assistant`` silently orphaned 7 lessons under the old
    ``ai-assistant`` slug, because the slug was derived purely from the
    directory name with no durable pin. When this field is set, callers use
    it verbatim instead of deriving a slug from ``repo_root.name``.

    Returns ``None`` (never raises) when: the config file is absent,
    unreadable, not valid JSON, not a JSON object, the field is missing or
    not a non-empty string, or the value fails ``_safe_project_tag``
    validation — every one of those falls through to the existing
    dirname-derivation path, so a malformed pin degrades gracefully instead
    of breaking resolution.
    """
    config_path = repo_root / ".build-loop" / "config.json"
    if not config_path.is_file():
        return None
    try:
        raw = config_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    pinned = data.get(MEMORY_SLUG_PIN_KEY)
    if not isinstance(pinned, str):
        return None
    pinned = pinned.strip()
    if not pinned:
        return None
    try:
        _safe_project_tag(pinned)
    except ValueError:
        return None
    return pinned


def derive_slug_from_cwd(cwd: Path | str | None = None) -> str:
    """Derive the project slug from a working directory.

    Algorithm (deterministic; alignment with Postgres semantic_facts.project
    is guaranteed by routing every slug through ``_safe_project_tag``):

      1. Resolve symlinks via ``Path.resolve()`` so a session running in
         ``~/.claude/plugins/build-loop`` resolves to ``~/dev/git-folder/build-loop``.
      2. Walk up looking for a ``.git`` entry (file OR dir — worktrees use
         a file). The deepest enclosing ``.git`` defines the repo root.
      2.5. **Slug PIN check** (durable-slug-pin, added post P0-4 RCA):
         if the canonical repo root (or, when that lookup fails, the
         worktree-local repo root) has a valid ``memoryProjectSlug`` pin in
         ``.build-loop/config.json`` (see ``_read_pinned_slug``), return it
         verbatim and skip steps 3-4 entirely. A rename of the repo
         directory no longer changes the slug once pinned.
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

    # Slug base from the CANONICAL repo root so every `git worktree` of one repo
    # shares a single memory project (else each worktree — e.g. an
    # `isolation: "worktree"` build-orchestrator dispatch — gets its own split
    # project). A worktree's `.git` is a FILE pointing at the main repo's gitdir;
    # the main checkout's `.git` is a DIR. Only shell out to git for the worktree
    # case so the common main-checkout path stays subprocess-free. Mirrors the
    # channel slug's D1 fix in `rally_point/channel_paths.py`.
    base_root = repo_root
    if (repo_root / ".git").is_file():
        canonical = _canonical_repo_root(cwd_resolved)
        if canonical is not None:
            base_root = canonical

    # Step 2.5 — durable slug PIN. Check the canonical root first (the
    # preferred place to set the pin — shared by every worktree), then fall
    # back to the worktree-local root. A hit here bypasses dirname
    # derivation entirely, so a `basename(repo_root)` rename cannot change
    # the resolved slug once pinned.
    pinned = _read_pinned_slug(base_root)
    if pinned is None and base_root != repo_root:
        pinned = _read_pinned_slug(repo_root)
    if pinned is not None:
        return pinned

    base = base_root.name.lower()
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
