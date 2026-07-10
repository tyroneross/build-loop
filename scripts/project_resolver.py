#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Resolve a current working directory to a project tag.

Reads ``<memory_store_root>/config/projects.yaml`` when present, falling
back to ``<memory_store_root>/.config/projects.yaml`` for migration, and returns the
project tag whose ``path:`` is the longest prefix match against the
given ``cwd``. Falls back to the YAML's ``default:`` key, which itself
defaults to ``_unscoped`` if absent.

Pure stdlib parser — projects.yaml is a small file we control, so we
parse just the subset we emit (top-level ``default:`` scalar plus a
``projects:`` list of ``- path: ...\\n  project: ...`` blocks).

Public API:
    resolve_project(cwd: Path) -> str
    load_projects_yaml(path: Path | None = None) -> dict
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
import sys
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from _paths import derive_slug_from_cwd, memory_store_root  # type: ignore  # noqa: E402
import project_registry  # type: ignore  # noqa: E402

DEFAULT_PROJECT_TAG = "_unscoped"


def _projects_yaml_path() -> Path:
    canonical = memory_store_root() / "config" / "projects.yaml"
    if canonical.exists():
        return canonical
    return memory_store_root() / ".config" / "projects.yaml"


def load_projects_yaml(path: Path | None = None) -> dict[str, Any]:
    """Parse the small subset of YAML we emit.

    Returns ``{"default": <tag>, "projects": [{"path": <abs>, "project": <tag>}, ...]}``.
    Missing file → ``{"default": "_unscoped", "projects": []}``.
    """
    if path is None:
        path = _projects_yaml_path()
    if not path.exists():
        return {"default": DEFAULT_PROJECT_TAG, "projects": []}
    text = path.read_text(encoding="utf-8")
    return _parse_projects_yaml(text)


def _parse_projects_yaml(text: str) -> dict[str, Any]:
    """Parse the subset of YAML used in projects.yaml.

    Recognized shapes:
        default: <scalar>
        projects:
          - path: <scalar>
            project: <scalar>
    Lines starting with ``#`` and blank lines are ignored.
    """
    default_tag = DEFAULT_PROJECT_TAG
    projects: list[dict[str, str]] = []
    in_projects = False
    cur: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        # Strip inline comments (very simple: a `#` not inside quotes).
        # projects.yaml never quotes values, so this is safe.
        if "#" in line:
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            line = line.split("#", 1)[0].rstrip()
        if not line:
            continue
        # Top-level key
        if not line[0].isspace():
            if line.startswith("default:"):
                val = line.split(":", 1)[1].strip()
                if val:
                    default_tag = val
                in_projects = False
                cur = None
            elif line.startswith("projects:"):
                in_projects = True
                cur = None
            else:
                in_projects = False
                cur = None
            continue
        # Indented line under projects:
        if not in_projects:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            # Start of a new project entry. The "- " line might itself
            # carry the first key (e.g. "- path: ~/dev/foo").
            if cur is not None:
                projects.append(cur)
            cur = {}
            after = stripped[2:].strip()
            if ":" in after:
                k, _, v = after.partition(":")
                cur[k.strip()] = v.strip()
        elif ":" in stripped and cur is not None:
            k, _, v = stripped.partition(":")
            cur[k.strip()] = v.strip()
    if cur is not None:
        projects.append(cur)
    # Filter out incomplete entries.
    projects = [p for p in projects if "path" in p and "project" in p]
    return {"default": default_tag or DEFAULT_PROJECT_TAG, "projects": projects}


def _normalize(p: str | Path) -> str:
    """Expand ``~`` and resolve symlinks to an absolute path string."""
    try:
        return os.path.normpath(str(Path(os.path.expanduser(str(p))).resolve()))
    except (OSError, RuntimeError):
        return os.path.normpath(os.path.expanduser(str(p)))


def resolve_project(cwd: Path | str) -> str:
    """Return the canonical project id for ``cwd``.

    Resolution contract (memory-identity-graph Phase 1 — stable-ID + alias
    walking; supersedes the single-value ``memoryProjectSlug`` pin, which
    is now one alias case):

      1. ``candidate = derive_slug_from_cwd(cwd)`` — unchanged deriver:
           1a. A durable ``memoryProjectSlug`` PIN in the enclosing repo's
               ``.build-loop/config.json`` (``_paths._read_pinned_slug``),
               used verbatim when present; the pin still SETS the candidate
               and the registry can still map it further.
           1b. Filesystem derivation: ``basename(git toplevel)`` normalized.
           Returns ``_unscoped`` outside any git repo and unpinned.
      2. Registry lookup (``project_registry.resolve``): find the project
         where ``candidate`` ∈ {id, canonical_slug, *aliases} OR ``cwd``
         matches one of ``paths``, then a BOUNDED, CYCLE-GUARDED alias walk
         returns that project's terminal canonical id. A hit wins.
      3. Else, if ``candidate != _unscoped`` → return ``candidate`` verbatim.
         An unregistered / new / unmigrated repo IS its own id and keeps
         working with zero registry entry.
      4. Else (``_unscoped``, no registry hit) → the registry ``default:``
         (else ``_unscoped``). The registry's ``paths`` seed IS the old
         ``projects.yaml`` path-fallback, consulted inside step 2.

    Deterministic, no LLM, never raises (degrades to the candidate/_unscoped).
    Aligns by construction with Postgres ``semantic_facts.project`` —
    ``derive_slug_from_cwd`` routes through ``_safe_project_tag``.
    """
    # Step 1 — pinned-or-derived candidate slug.
    candidate = derive_slug_from_cwd(cwd)

    # Step 2 — registry lookup + alias walk (returns terminal canonical id).
    registry = project_registry.load_registry()
    hit = project_registry.resolve(candidate, cwd, registry)
    if hit is not None:
        return hit

    # Step 3 — an unregistered git repo is its own id.
    if candidate != DEFAULT_PROJECT_TAG:
        return candidate

    # Step 4 — _unscoped with no registry hit → default.
    return registry.get("default", DEFAULT_PROJECT_TAG)


if __name__ == "__main__":  # pragma: no cover - manual smoke tool
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    print(resolve_project(target))
