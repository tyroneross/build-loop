#!/usr/bin/env python3
"""Rally Point channel path resolver (D1: worktree-aware slug).

The shared per-app channel lives at ``~/.build-loop/apps/<slug>/``. The
slug MUST be identical across a clone, the main checkout, and any
``git worktree`` of the same canonical repo — otherwise concurrent
sessions (the exact scenario Rally Point targets, often under
``isolation: "worktree"``) split the channel.

D1 (amended 2026-05-17): resolve the slug from
``git rev-parse --git-common-dir``. A worktree's git-common-dir points
at the *canonical* repo's ``.git`` (shared by main + every worktree), so
``Path(common_dir).resolve().parent.name`` is identical from anywhere in
the repo family. Note ``--git-common-dir`` returns a path relative to the
invocation cwd from the main checkout and an absolute path from a
worktree; ``.resolve()`` (run with the proper cwd) normalises both.

Fallback: only when NOT in a git repo, delegate to
``scripts/_paths.derive_slug_from_cwd`` (returns ``_unscoped``).

Path-traversal validation reuses memory's ``_safe_project_tag`` from
``scripts/_paths`` — the same guard that defends the decisions store —
so the channel and memory share one normalization, no split-brain.

Pure/side-effect-free except env inspection and the explicit
``ensure_channel_dir`` lazy-create. Never blocks/fails a host action.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# Import the canonical memory-store guards from the sibling
# ``scripts/_paths.py``. We load it by explicit file path under a
# distinct module name so it never collides with THIS module (both are
# basename ``_paths`` — a bare ``import _paths`` would self-shadow when
# ``scripts/rally_point/`` is on sys.path ahead of ``scripts/``).
import importlib.util as _ilu  # noqa: E402

_MEM_PATHS_FILE = Path(__file__).resolve().parent.parent / "_paths.py"
_spec = _ilu.spec_from_file_location("_bl_mem_paths", _MEM_PATHS_FILE)
_mem = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mem)  # type: ignore[union-attr]

SUBCOMPONENT_PATTERNS = _mem.SUBCOMPONENT_PATTERNS
_safe_project_tag = _mem._safe_project_tag
derive_slug_from_cwd = _mem.derive_slug_from_cwd

DEFAULT_APPS_ROOT = "~/.build-loop/apps"


def apps_root() -> Path:
    """Return the apps-channel root (``~/.build-loop/apps`` by default).

    ``$BUILD_LOOP_APPS_ROOT`` overrides (expanded for ``~``). Not required
    to exist; ``ensure_channel_dir`` creates on demand.
    """
    raw = os.environ.get("BUILD_LOOP_APPS_ROOT") or DEFAULT_APPS_ROOT
    return Path(os.path.expanduser(raw))


def _normalize_base(name: str) -> str:
    base = name.lower()
    base = re.sub(r"[^a-z0-9._-]", "-", base)
    base = re.sub(r"-{2,}", "-", base).strip("-")
    return base[:64]


def app_slug(cwd: Path | str | None = None) -> str:
    """Return the worktree-independent app slug for ``cwd``.

    1. ``git rev-parse --git-common-dir`` (cwd-scoped). Resolve to an
       absolute path; the *parent* of the common-dir is the canonical
       repo root; its basename is the slug base.
    2. If ``cwd`` sits under a recognised sub-component dir (today only
       ``workers/``), append ``/<sub>`` (OQ1 — same convention as memory).
    3. Each slug segment is validated via ``_safe_project_tag``.
    4. Not a git repo (rev-parse fails) → delegate to
       ``derive_slug_from_cwd`` (yields ``_unscoped`` outside git).

    Never raises (resolution failure → ``_unscoped``).
    """
    if cwd is None:
        cwd = Path.cwd()
    cwd_path = Path(os.path.expanduser(str(cwd)))
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(cwd_path),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return derive_slug_from_cwd(cwd_path)
    if not out:
        return derive_slug_from_cwd(cwd_path)

    common = Path(out)
    if not common.is_absolute():
        common = (cwd_path / common)
    try:
        repo_root = common.resolve().parent
    except (OSError, RuntimeError):
        return derive_slug_from_cwd(cwd_path)

    base = _normalize_base(repo_root.name) or "_unscoped"
    try:
        _safe_project_tag(base)
    except ValueError:
        return "_unscoped"

    # Sub-component detection relative to the canonical repo root.
    try:
        rel = cwd_path.resolve().relative_to(repo_root.resolve())
    except (ValueError, OSError, RuntimeError):
        rel = Path()
    if rel.parts:
        first = rel.parts[0].lower()
        if first in SUBCOMPONENT_PATTERNS:
            sub = _normalize_base(first)
            if sub:
                try:
                    _safe_project_tag(sub)
                except ValueError:
                    return base
                return f"{base}/{sub}"
    return base


def app_channel_dir(slug: str) -> Path:
    """Return ``apps_root() / <slug>`` with traversal validation.

    Each ``/``-separated segment is run through ``_safe_project_tag``
    (rejects ``..``, separators, unsafe chars). The resolved path is
    asserted to stay rooted under ``apps_root()`` (symlink-escape guard).
    Never creates anything.
    """
    if not slug:
        raise ValueError("app_channel_dir requires a non-empty slug")
    parts = slug.split("/")
    for seg in parts:
        _safe_project_tag(seg)
    candidate = (apps_root() / Path(*parts)).resolve()
    root_resolved = apps_root().resolve()
    cs, rs = str(candidate), str(root_resolved)
    if not (cs == rs or cs.startswith(rs + os.sep)):
        raise ValueError(f"slug {slug!r} resolves outside apps_root()")
    # SEC-009: return the SAME resolved path that was validated, not a
    # freshly re-joined unresolved path. Returning the unresolved form
    # opened a TOCTOU gap — the validated target and the returned target
    # could differ if a path component was a symlink, or be re-resolved
    # later against a mutated filesystem.
    return candidate


def ensure_channel_dir(slug: str) -> Path:
    """Lazy-create and return the channel dir for ``slug`` (idempotent)."""
    d = app_channel_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    return d
