#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Stable-ID + alias-walking project registry (memory-identity-graph Phase 1).

Replaces the single-value ``memoryProjectSlug`` pin with a real registry:
every project has a STABLE ``id`` that is never re-derived, a display
``canonical_slug``, the repo ``paths`` it lives at, and ``aliases`` (old
slugs / old dirnames that must still resolve to it). A repo rename — single
or chained — never orphans memory, because the old slug walks the alias
edges to the terminal canonical id.

Schema (``build-loop-memory/config/projects.yaml`` v2), BACKWARD-COMPATIBLE
with the v1 ``{path, project}`` shape (a missing ``id`` means ``id`` == the
``project`` value)::

    default: _unscoped
    projects:
      - id: <stable, never re-derived>
        canonical_slug: <slug>
        paths: [<repo paths>]
        aliases: [<old slugs / old dirnames that must resolve here>]
        derived_from: <id or null>     # provenance edge (scaffolding; no behavior yet)
        depends_on: [<ids>]            # dependency edges (scaffolding; no behavior yet)

Public API:
    load_registry(path: Path | None = None) -> dict
    resolve(candidate: str | None, cwd: Path | str | None = None,
            registry: dict | None = None) -> str | None
    register_project(id: str, slug: str | None = None,
                     path: str | Path | None = None, ...) -> bool
    write_registry(registry: dict, path: Path | None = None) -> Path

``resolve`` is DETERMINISTIC, never calls an LLM, and never raises (it
degrades to ``None`` so callers fall back to the candidate/_unscoped path).

Pure stdlib parser + emitter — ``projects.yaml`` is a small file we control.
The registry emits inline flow lists (``[a, b]``); the parser tolerates both
inline and block lists plus the v1 scalar shape.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from _paths import memory_store_root  # type: ignore  # noqa: E402

logger = logging.getLogger("build_loop.project_registry")

DEFAULT_PROJECT_TAG = "_unscoped"
# Bounded alias walk: a rename chain deeper than this is pathological; stop
# and return the current node rather than loop or scan unboundedly.
MAX_ALIAS_WALK_DEPTH = 8
# Keys whose YAML value is a list.
_LIST_KEYS = ("paths", "aliases", "depends_on")


# ---------------------------------------------------------------------------
# YAML path resolution (mirrors project_resolver._projects_yaml_path)
# ---------------------------------------------------------------------------

def registry_yaml_path() -> Path:
    """Return the active ``projects.yaml`` path (``config/`` then ``.config/``)."""
    canonical = memory_store_root() / "config" / "projects.yaml"
    if canonical.exists():
        return canonical
    return memory_store_root() / ".config" / "projects.yaml"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_flow_list(value: str) -> list[str]:
    """Parse an inline flow list ``[a, b, c]`` (or ``[]``) into a list."""
    value = value.strip()
    if value.startswith("["):
        value = value[1:]
    if value.endswith("]"):
        value = value[:-1]
    return [item.strip() for item in value.split(",") if item.strip()]


def _normalize_entry(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a parsed entry (v1 or v2) to the canonical v2 dict.

    v1 ``{path, project}`` → ``id`` == ``project`` (frozen-schema rule).
    Returns ``None`` for an entry with no derivable id (incomplete).
    """
    id_ = raw.get("id") or raw.get("project") or raw.get("canonical_slug")
    if not id_:
        return None
    canonical = raw.get("canonical_slug") or raw.get("project") or id_
    paths: list[str] = list(raw.get("paths") or [])
    legacy_path = raw.get("path")
    if legacy_path and legacy_path not in paths:
        paths.insert(0, legacy_path)
    derived_from = raw.get("derived_from")
    if derived_from in ("", "null", "~", None):
        derived_from = None
    return {
        "id": id_,
        "canonical_slug": canonical,
        "paths": paths,
        "aliases": list(raw.get("aliases") or []),
        "derived_from": derived_from,
        "depends_on": list(raw.get("depends_on") or []),
    }


def _apply_kv(entry: dict[str, Any], content: str) -> str | None:
    """Apply a ``key: value`` pair to ``entry``.

    Returns the key name when it opened a *block* list (empty value on a list
    key) so the caller can attach subsequent ``- item`` lines; else ``None``.
    """
    key, _, value = content.partition(":")
    key = key.strip()
    value = value.strip()
    if key in _LIST_KEYS:
        if value == "":
            entry[key] = []
            return key  # pending block list
        entry[key] = _parse_flow_list(value)
        return None
    entry[key] = value
    return None


def _parse_registry_yaml(text: str) -> dict[str, Any]:
    """Parse the v2 (and v1) subset of ``projects.yaml``.

    Line-oriented, indent-aware. Supports scalar keys, inline flow lists, and
    block lists. Comments (``#``) and blank lines are ignored.
    """
    default_tag = DEFAULT_PROJECT_TAG
    entries: list[dict[str, Any]] = []
    in_projects = False
    entry_indent: int | None = None
    cur: dict[str, Any] | None = None
    pending_list_key: str | None = None

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            norm = _normalize_entry(cur)
            if norm is not None:
                entries.append(norm)
        cur = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        # Strip comment-only + inline comments (values never contain '#').
        stripped_full = line.strip()
        if not stripped_full or stripped_full.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
        indent = len(line) - len(line.lstrip())
        content = line.strip()

        if indent == 0:
            flush()
            in_projects = False
            entry_indent = None
            pending_list_key = None
            if content.startswith("default:"):
                val = content.split(":", 1)[1].strip()
                if val:
                    default_tag = val
            elif content.startswith("projects:"):
                in_projects = True
            continue

        if not in_projects:
            continue

        if content.startswith("- "):
            # Block-list item (deeper than the entry marker) vs. new entry.
            if (
                pending_list_key is not None
                and entry_indent is not None
                and indent > entry_indent
                and cur is not None
            ):
                cur.setdefault(pending_list_key, []).append(content[2:].strip())
                continue
            # New entry.
            flush()
            if entry_indent is None:
                entry_indent = indent
            cur = {}
            pending_list_key = None
            after = content[2:].strip()
            if after:
                pending_list_key = _apply_kv(cur, after)
            continue

        # A plain ``key: value`` line inside the current entry.
        if cur is None:
            continue
        if ":" in content:
            pending_list_key = _apply_kv(cur, content)

    flush()
    return {"default": default_tag or DEFAULT_PROJECT_TAG, "projects": entries}


def load_registry(path: Path | None = None) -> dict[str, Any]:
    """Load + normalize the project registry.

    Missing file → ``{"default": "_unscoped", "projects": []}``. Never raises;
    a parse/read error degrades to the empty registry so resolution keeps
    working (callers fall back to the derived candidate).
    """
    if path is None:
        path = registry_yaml_path()
    try:
        if not path.exists():
            return {"default": DEFAULT_PROJECT_TAG, "projects": []}
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"default": DEFAULT_PROJECT_TAG, "projects": []}
    try:
        return _parse_registry_yaml(text)
    except Exception:  # noqa: BLE001 — resolution must never raise
        logger.warning("project registry parse failed for %s; using empty", path)
        return {"default": DEFAULT_PROJECT_TAG, "projects": []}


# ---------------------------------------------------------------------------
# Resolution (alias walk)
# ---------------------------------------------------------------------------

def _normalize_path(p: str | Path) -> str:
    """Expand ``~`` and resolve symlinks to an absolute path string."""
    try:
        return os.path.normpath(str(Path(os.path.expanduser(str(p))).resolve()))
    except (OSError, RuntimeError):
        return os.path.normpath(os.path.expanduser(str(p)))


def _forward_edges(projects: list[dict[str, Any]]) -> dict[str, str]:
    """Map ``old_slug -> superseding project id``.

    When project ``F`` lists ``a`` in its ``aliases``, ``F`` supersedes ``a``,
    so a node whose id is ``a`` walks forward to ``F``. First writer wins on a
    collision (deterministic by registry order).
    """
    forward: dict[str, str] = {}
    for f in projects:
        for alias in f.get("aliases", []):
            forward.setdefault(alias, f["id"])
    return forward


def _walk_to_terminal(start_id: str, forward: dict[str, str]) -> str:
    """Follow forward (rename) edges to the terminal canonical id.

    Bounded (``MAX_ALIAS_WALK_DEPTH``) and cycle-guarded: on a cycle or a
    depth overrun, return the current node and log — never loop, never raise.
    """
    seen: set[str] = set()
    cur = start_id
    depth = 0
    while True:
        if cur in seen:
            logger.warning("project registry alias cycle detected at %r", cur)
            return cur
        seen.add(cur)
        nxt = forward.get(cur)
        if nxt is None or nxt == cur:
            return cur
        depth += 1
        if depth > MAX_ALIAS_WALK_DEPTH:
            logger.warning(
                "project registry alias walk exceeded depth %d from %r (stopped at %r)",
                MAX_ALIAS_WALK_DEPTH, start_id, cur,
            )
            return cur
        cur = nxt


def resolve(
    candidate: str | None,
    cwd: Path | str | None = None,
    registry: dict[str, Any] | None = None,
) -> str | None:
    """Resolve ``candidate``/``cwd`` to a canonical project id, or ``None``.

    Contract:
      1. Key match — the project where ``candidate`` ∈ {id, canonical_slug,
         *aliases}. Wins over path match.
      2. Path match — else the project whose ``paths`` contain ``cwd``
         (longest-prefix wins).
      3. From the matched node, a BOUNDED, CYCLE-GUARDED alias walk follows
         rename edges to the terminal canonical id.
      4. No match → ``None`` (caller keeps the candidate verbatim / falls back
         to default).

    Deterministic, never raises.
    """
    if registry is None:
        registry = load_registry()
    projects = registry.get("projects") or []
    if not projects:
        return None

    start_id: str | None = None

    # 1. Key match.
    if candidate:
        for p in projects:
            keys = {p["id"], p["canonical_slug"], *p.get("aliases", [])}
            if candidate in keys:
                start_id = p["id"]
                break

    # 2. Path match (longest prefix).
    if start_id is None and cwd is not None:
        cwd_norm = _normalize_path(cwd)
        best: tuple[int, str] | None = None
        for p in projects:
            for pth in p.get("paths", []):
                pn = _normalize_path(pth)
                if cwd_norm == pn or cwd_norm.startswith(pn + os.sep):
                    length = len(pn)
                    if best is None or length > best[0]:
                        best = (length, p["id"])
        if best is not None:
            start_id = best[1]

    if start_id is None:
        return None

    # 3. Alias walk to terminal canonical.
    return _walk_to_terminal(start_id, _forward_edges(projects))


# ---------------------------------------------------------------------------
# Emitting + registration
# ---------------------------------------------------------------------------

_HEADER = (
    "# Canonical project registry for build-loop-memory (v2 — stable-ID + aliases).\n"
    "#\n"
    "# Each project has a STABLE `id` that is never re-derived. A repo rename adds\n"
    "# the old slug/dirname to `aliases` (or a chained rename node); resolution\n"
    "# walks alias edges to the terminal canonical id, so old references never\n"
    "# orphan memory. `derived_from`/`depends_on` are provenance/dependency edges\n"
    "# (Phase 2 scaffolding — stored, no behavior yet). Backward-compatible with\n"
    "# the v1 `{path, project}` shape (a missing `id` means id == project).\n"
)


def _dump_flow_list(items: list[str]) -> str:
    return "[]" if not items else "[" + ", ".join(items) + "]"


def dump_registry(registry: dict[str, Any]) -> str:
    """Serialize a registry to v2 YAML text (inline flow lists; id-sorted)."""
    lines = [_HEADER, f"default: {registry.get('default', DEFAULT_PROJECT_TAG)}", "", "projects:"]
    for p in sorted(registry.get("projects", []), key=lambda e: e["id"]):
        derived = p.get("derived_from")
        lines.append(f"  - id: {p['id']}")
        lines.append(f"    canonical_slug: {p.get('canonical_slug', p['id'])}")
        lines.append(f"    paths: {_dump_flow_list(list(p.get('paths', [])))}")
        lines.append(f"    aliases: {_dump_flow_list(list(p.get('aliases', [])))}")
        lines.append(f"    derived_from: {derived if derived else 'null'}")
        lines.append(f"    depends_on: {_dump_flow_list(list(p.get('depends_on', [])))}")
    return "\n".join(lines) + "\n"


def write_registry(registry: dict[str, Any], path: Path | None = None) -> Path:
    """Write ``registry`` as v2 YAML to ``path`` (default: active registry path)."""
    if path is None:
        path = memory_store_root() / "config" / "projects.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_registry(registry), encoding="utf-8")
    return path


def register_project(
    id: str,  # noqa: A002 — matches the frozen helper signature
    slug: str | None = None,
    path: str | Path | None = None,
    *,
    registry_path: Path | None = None,
    write: bool = True,
) -> bool:
    """Register a stable project id (idempotent). Returns True iff it changed.

    The stable-ID-at-init hook: on first scaffold of ``projects/<slug>/`` a
    writer calls this to freeze ``id == slug`` in the registry. Re-registering
    an existing id only appends a new ``path``. Best-effort by contract — a
    write failure returns False rather than raising.
    """
    slug = slug or id
    if registry_path is None:
        registry_path = memory_store_root() / "config" / "projects.yaml"
    registry = load_registry(registry_path)
    path_str = _normalize_path(path) if path else None

    existing = next((p for p in registry["projects"] if p["id"] == id), None)
    changed = False
    if existing is None:
        registry["projects"].append({
            "id": id,
            "canonical_slug": slug,
            "paths": [path_str] if path_str else [],
            "aliases": [],
            "derived_from": None,
            "depends_on": [],
        })
        changed = True
    elif path_str and path_str not in {_normalize_path(x) for x in existing["paths"]}:
        existing["paths"].append(path_str)
        changed = True

    if changed and write:
        try:
            write_registry(registry, registry_path)
        except OSError:
            logger.warning("register_project: failed to write registry at %s", registry_path)
            return False
    return changed


if __name__ == "__main__":  # pragma: no cover — manual smoke tool
    _cand = sys.argv[1] if len(sys.argv) > 1 else None
    _cwd = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()
    print(resolve(_cand, _cwd))
