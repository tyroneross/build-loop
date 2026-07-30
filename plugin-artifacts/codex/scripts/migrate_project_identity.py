#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Doctor: migrate a build-loop-memory store to the v2 project registry.

Idempotent, one-time (re-runnable) pass that upgrades ``config/projects.yaml``
from the v1 ``{path, project}`` shape to the v2 stable-ID + alias schema:

  * For each ``projects/<slug>/`` folder, ensure a v2 entry with ``id == slug``
    (the current slug is FROZEN as the stable id). Folders DO NOT move — the
    store folder stays at ``projects/<id>/``.
  * Seed ``paths`` from the existing ``projects.yaml`` (and pin detection).
  * Convert any repo's ``memoryProjectSlug`` pin into the registry: the repo
    path is attached to the pinned project, and the repo's dirname-derived
    slug (what it WOULD derive to without the pin) becomes an ALIAS — the pin
    becomes one alias case.
  * Seed the known rename ``ai-assistant`` → alias of canonical id
    ``rosslabs-ai-assistant``.

DEFAULT MODE = ``--dry-run``: print the exact v2 ``projects.yaml`` that WOULD
be written plus the per-project actions. Only ``--apply`` writes.

CLI::

    python3 scripts/migrate_project_identity.py [--dry-run | --apply]
        [--store-root <path>] [--repo-scan-root <path>] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import project_registry  # type: ignore  # noqa: E402
from _paths import (  # type: ignore  # noqa: E402
    _read_pinned_slug,
    _safe_project_tag,
    memory_store_root,
)

# (alias_old_slug, canonical_id) — folded into the canonical's aliases.
KNOWN_RENAMES: tuple[tuple[str, str], ...] = (
    ("ai-assistant", "rosslabs-ai-assistant"),
)

# projects/<name> entries that are NOT standalone project identities.
_SKIP_PREFIXES = ("_",)


def _dirname_slug(repo_path: Path) -> str | None:
    """Return the slug a repo dirname WOULD derive to (no pin). ``None`` if unsafe.

    Mirrors ``_paths.derive_slug_from_cwd`` step 3 (basename normalization),
    deliberately WITHOUT the pin check — this is the pre-pin candidate that
    becomes an alias.
    """
    base = repo_path.name.lower()
    base = re.sub(r"[^a-z0-9._-]", "-", base)
    base = re.sub(r"-{2,}", "-", base).strip("-")
    base = base[:64]
    if not base:
        return None
    try:
        _safe_project_tag(base)
    except ValueError:
        return None
    return base


def _iter_project_folders(projects_dir: Path) -> list[str]:
    """Return sorted ``projects/<slug>/`` folder names that are project ids."""
    if not projects_dir.is_dir():
        return []
    out: list[str] = []
    for child in sorted(projects_dir.iterdir()):
        name = child.name
        if not child.is_dir():
            continue
        if any(name.startswith(p) for p in _SKIP_PREFIXES):
            continue
        try:
            _safe_project_tag(name)
        except ValueError:
            continue
        out.append(name)
    return out


def _ensure_entry(by_id: dict[str, dict[str, Any]], entry_id: str) -> dict[str, Any]:
    """Return the registry entry for ``entry_id``, creating a bare one if absent."""
    e = by_id.get(entry_id)
    if e is None:
        e = {
            "id": entry_id,
            "canonical_slug": entry_id,
            "paths": [],
            "aliases": [],
            "derived_from": None,
            "depends_on": [],
        }
        by_id[entry_id] = e
    return e


def _scan_pins(
    scan_root: Path, extra_paths: list[Path], seen: set[str]
) -> list[tuple[Path, str]]:
    """Return ``[(repo_path, pinned_slug), ...]`` for repos carrying a pin.

    Scans ``<scan_root>/*/.build-loop/config.json`` plus any ``extra_paths``
    (registered repo paths). Read-only; never raises.
    """
    out: list[tuple[Path, str]] = []
    candidates: list[Path] = list(extra_paths)
    try:
        if scan_root.is_dir():
            candidates += [c for c in scan_root.iterdir() if c.is_dir()]
    except OSError:
        pass
    for repo in candidates:
        key = str(repo.resolve()) if repo.exists() else str(repo)
        if key in seen:
            continue
        seen.add(key)
        try:
            pinned = _read_pinned_slug(repo)
        except Exception:  # noqa: BLE001 — best-effort
            pinned = None
        if pinned:
            out.append((repo, pinned))
    return out


def plan_migration(
    store_root: Path, repo_scan_root: Path | None
) -> tuple[dict[str, Any], list[str]]:
    """Compute the target v2 registry + a human-readable action list."""
    projects_dir = store_root / "projects"
    registry_path = store_root / "config" / "projects.yaml"
    if not registry_path.exists():
        alt = store_root / ".config" / "projects.yaml"
        if alt.exists():
            registry_path = alt

    existing = project_registry.load_registry(registry_path)
    by_id: dict[str, dict[str, Any]] = {p["id"]: p for p in existing["projects"]}
    actions: list[str] = []

    # 1. Freeze each projects/<slug>/ folder as a stable id.
    for slug in _iter_project_folders(projects_dir):
        if slug not in by_id:
            _ensure_entry(by_id, slug)
            actions.append(f"create-entry   id={slug} (folder frozen as stable id)")

    # 2. Seed known renames as aliases of the canonical id.
    for old_slug, canonical in KNOWN_RENAMES:
        canon = _ensure_entry(by_id, canonical)
        if old_slug in by_id and old_slug != canonical:
            # A standalone node for the old slug should fold into the canonical.
            folded = by_id.pop(old_slug)
            for pth in folded.get("paths", []):
                if pth not in canon["paths"]:
                    canon["paths"].append(pth)
            actions.append(f"fold-node      id={old_slug} -> aliases of {canonical}")
        if old_slug not in canon["aliases"]:
            canon["aliases"].append(old_slug)
            actions.append(f"seed-alias     {old_slug} -> alias of {canonical} (known rename)")

    # 3. Convert memoryProjectSlug pins into the registry.
    extra = [Path(p) for e in by_id.values() for p in e.get("paths", [])]
    scan_root = repo_scan_root if repo_scan_root is not None else store_root.parent
    for repo_path, pinned in _scan_pins(scan_root, extra, set()):
        entry = _ensure_entry(by_id, pinned)
        norm_repo = project_registry._normalize_path(repo_path)
        if norm_repo not in {project_registry._normalize_path(x) for x in entry["paths"]}:
            entry["paths"].append(norm_repo)
            actions.append(f"pin-path       {repo_path.name} -> paths of {pinned}")
        dirname_slug = _dirname_slug(repo_path)
        if (
            dirname_slug
            and dirname_slug != pinned
            and dirname_slug not in entry["aliases"]
            and dirname_slug not in {p["id"] for p in by_id.values()}
        ):
            entry["aliases"].append(dirname_slug)
            actions.append(
                f"pin-alias      {dirname_slug} -> alias of {pinned} "
                f"(dirname of pinned repo {repo_path.name})"
            )

    target = {"default": existing.get("default", "_unscoped"),
              "projects": list(by_id.values())}
    if not actions:
        actions.append("no changes — registry already migrated")
    return target, actions


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="Print the v2 projects.yaml that WOULD be written (default)")
    mode.add_argument("--apply", action="store_true",
                      help="Write the migrated v2 projects.yaml")
    p.add_argument("--store-root", default=None,
                   help="build-loop-memory store root (default: memory_store_root())")
    p.add_argument("--repo-scan-root", default=None,
                   help="Root to scan for repo memoryProjectSlug pins "
                        "(default: store_root parent)")
    p.add_argument("--json", action="store_true", help="Emit a JSON envelope")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    store_root = Path(args.store_root).expanduser() if args.store_root else memory_store_root()
    scan_root = Path(args.repo_scan_root).expanduser() if args.repo_scan_root else None
    apply = bool(args.apply)

    target, actions = plan_migration(store_root, scan_root)
    yaml_text = project_registry.dump_registry(target)
    registry_path = store_root / "config" / "projects.yaml"

    if args.json:
        print(json.dumps({
            "mode": "apply" if apply else "dry-run",
            "store_root": str(store_root),
            "registry_path": str(registry_path),
            "actions": actions,
            "projects_count": len(target["projects"]),
            "yaml": yaml_text,
        }, indent=2))
    else:
        header = "APPLY" if apply else "DRY-RUN (no files written; pass --apply to write)"
        print(f"=== migrate_project_identity — {header} ===")
        print(f"store_root:    {store_root}")
        print(f"registry_path: {registry_path}")
        print("\n--- actions ---")
        for a in actions:
            print(f"  {a}")
        print("\n--- projects.yaml (v2) that WOULD be written ---")
        print(yaml_text)

    if apply:
        written = project_registry.write_registry(target, registry_path)
        if not args.json:
            print(f"WROTE {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
