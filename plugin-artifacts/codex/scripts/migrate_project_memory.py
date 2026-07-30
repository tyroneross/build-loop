#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""migrate_project_memory.py — move project memory into the consolidated tree.

Migrates content from per-repo legacy paths::

    <repo>/.build-loop/memory/*.md
    <repo>/.build-loop/memory/<subcomponent>/*.md    (e.g. workers/)

to the consolidated global tree::

    ~/dev/git-folder/build-loop-memory/projects/<slug>/*.md
    ~/dev/git-folder/build-loop-memory/projects/<slug>/<subcomponent>/*.md

PR 1.5 of the memory-consolidation series. Designed to run BETWEEN PR 1
(read-path tolerance, already merged) and PR 2 (write cutover). The
orchestrator's reads already tolerate both paths during this window;
this script moves the content.

Safety contract:

  1. **Dry-run by default.** ``--apply`` required to write anything.
  2. **Tarball backup before any write.** Single tarball at
     ``~/build-loop-memory-pre-migration-<unix-ts>.tgz`` covering every
     source ``<repo>/.build-loop/memory/`` directory. Refuses to proceed
     if the backup write fails.
  3. **sha256 collision refusal.** If the target file exists with
     different content, refuse and surface the diff. ``--force-overwrite``
     bypasses (operator-controlled).
  4. **Preserve ``workers/`` and other sub-component hierarchies.** Slug
     resolution mirrors ``derive_slug_from_cwd``; sub-component subdirs
     are copied recursively, not flattened.
  5. **Provenance preservation.** Original ``ctime`` is captured into
     ``created_at`` frontmatter via ``memory_writer.migrate`` after the
     content copy completes. The canonical writer remains the single
     source of truth for frontmatter shape.
  6. **Stub at old path.** Leaves ``<repo>/.build-loop/memory/.MOVED.md``
     pointing at the new location. One-release deprecation window.
  7. **Summary markdown.** Writes
     ``<build-loop-memory>/_migrations/<date>-consolidation.md`` with
     Moved / Archived / Collisions refused / Postgres reconciliation /
     Rollback command sections. C-AGENT/no_silent_self_modification
     compliance artifact.
  8. **Rollback subcommand.** ``--rollback`` reads the manifest +
     extracts the tarball over the consolidated tree to undo the move.
     Tested in PR 1.5's F16 acceptance test.

Usage:
  python3 scripts/migrate_project_memory.py --check
  python3 scripts/migrate_project_memory.py --dry-run        # default
  python3 scripts/migrate_project_memory.py --apply
  python3 scripts/migrate_project_memory.py --apply --force-overwrite
  python3 scripts/migrate_project_memory.py --rollback --manifest <path>
  python3 scripts/migrate_project_memory.py --apply --source-root ~/dev/git-folder

Exit codes:
  0 — success (dry-run or apply or rollback)
  1 — backup/copy/write failure (no destructive operation succeeded)
  2 — sha256 collision refusal (operator must --force-overwrite OR resolve)
  3 — invalid arguments
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _paths import (  # type: ignore  # noqa: E402
    SUBCOMPONENT_PATTERNS,
    derive_slug_from_cwd,
    project_memory_dir_for_project,
    build_loop_memory_root,
)

DEFAULT_SOURCE_ROOT = "~/dev/git-folder"
LEGACY_MEMORY_REL = ".build-loop/memory"
STUB_FILENAME = ".MOVED.md"
MANIFEST_DIR_NAME = "_migrations"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def iso_utc(t: float | None = None) -> str:
    """ISO-8601 UTC timestamp, e.g. ``2026-05-13T18:42:31Z``."""
    if t is None:
        t = time.time()
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _strip_frontmatter(text: str) -> str:
    """Return the body after a leading ``---\\n...\\n---\\n`` block.

    If no frontmatter is present, returns the original text. Used so that
    sha256-based collision detection compares actual content, not provenance
    metadata that the canonical writer may have added to the target file.
    """
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + 5 :]


def body_sha256(path: Path) -> str:
    """sha256 of the file's BODY (frontmatter stripped, surrounding whitespace stripped).

    This is the identity-preserving comparison: a file that's been moved
    AND had provenance frontmatter backfilled in the target should still
    compare equal to its source on body content. Leading/trailing whitespace
    is normalized away so that the canonical writer's added blank line
    between frontmatter and body doesn't trip the comparison.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    body = _strip_frontmatter(text).strip()
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def tree_sha256(root: Path) -> dict[str, str]:
    """Map relpath → sha256 for every file in ``root`` (recursive)."""
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                out[str(p.relative_to(root))] = file_sha256(p)
            except (OSError, ValueError):
                continue
    return out


def discover_source_dirs(source_root: Path) -> list[Path]:
    """Find every ``<source_root>/<repo>/.build-loop/memory/`` directory.

    Includes sub-component dirs found via direct descent (one level deep).
    Returns only directories that exist; empty dirs are kept (they may have
    sub-component contents).
    """
    out: list[Path] = []
    if not source_root.is_dir():
        return out
    for repo in sorted(source_root.iterdir()):
        if not repo.is_dir():
            continue
        mem = repo / LEGACY_MEMORY_REL
        if mem.is_dir():
            out.append(mem)
    return out


def discover_subcomponent_dirs(source_root: Path) -> list[Path]:
    """Find sub-component memory dirs like ``<repo>/<sub>/.build-loop/memory/``.

    Iterates ``_paths.SUBCOMPONENT_PATTERNS`` (currently just ``workers``).
    Single source of truth — extends automatically when new sub-component
    patterns are added to ``_paths.py``.
    """
    out: list[Path] = []
    if not source_root.is_dir():
        return out
    for repo in sorted(source_root.iterdir()):
        if not repo.is_dir():
            continue
        for sub in SUBCOMPONENT_PATTERNS:
            sub_mem = repo / sub / LEGACY_MEMORY_REL
            if sub_mem.is_dir():
                out.append(sub_mem)
    return out


def slug_for_source_dir(source_dir: Path) -> str:
    """Map ``<repo>/.build-loop/memory`` → slug via ``derive_slug_from_cwd``.

    Also handles ``<repo>/<sub>/.build-loop/memory`` → ``<repo-slug>/<sub>``.
    The repo root is two parents up from the memory dir.
    """
    repo_root = source_dir.parent.parent
    return derive_slug_from_cwd(repo_root)


def write_tarball_backup(source_dirs: list[Path], dest_tarball: Path) -> None:
    """Tar.gz every source dir to ``dest_tarball`` with the source path as the arcname.

    Refuses to overwrite an existing tarball. Sync flush to disk.
    """
    if dest_tarball.exists():
        raise FileExistsError(f"backup target already exists: {dest_tarball}")
    dest_tarball.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest_tarball, "w:gz") as tf:
        for src in source_dirs:
            # Use a stable arcname so rollback can find each entry
            arc = str(src).lstrip("/")
            tf.add(str(src), arcname=arc, recursive=True)
    # Force flush
    fd = os.open(str(dest_tarball), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_stub(source_dir: Path, target_dir: Path) -> None:
    """Leave a ``.MOVED.md`` stub at the old path pointing at the new path."""
    stub = source_dir / STUB_FILENAME
    body = (
        f"# {LEGACY_MEMORY_REL} — moved 2026-05-13\n\n"
        f"This directory's `.md` content was migrated to:\n\n"
        f"    {target_dir}\n\n"
        f"Read-path tolerance still works during the PR 1/2 transition window. "
        f"PR 3 removes the legacy read shim from `memory_facade.py`. After PR 3 "
        f"lands, this stub (and the rest of this directory) can be deleted.\n\n"
        f"Rollback command:\n\n"
        f"    python3 scripts/migrate_project_memory.py --rollback --manifest <manifest>\n"
    )
    stub.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Core migration
# ---------------------------------------------------------------------------


def plan_migration(source_root: Path) -> dict[str, Any]:
    """Produce a per-source-dir plan describing what would happen on --apply.

    Returns a dict with: ``source_dirs``, ``subcomponent_dirs``, ``targets``,
    ``collisions``, ``empty``, ``total_files``.
    """
    source_dirs = discover_source_dirs(source_root)
    subcomp_dirs = discover_subcomponent_dirs(source_root)
    all_sources = list(source_dirs) + list(subcomp_dirs)

    targets: list[dict[str, Any]] = []
    collisions: list[dict[str, Any]] = []
    total_files = 0
    empty_dirs: list[str] = []

    for src in all_sources:
        slug = slug_for_source_dir(src)
        target = project_memory_dir_for_project(slug)
        files_in_src = [p for p in src.glob("*.md") if p.name != STUB_FILENAME]
        if not files_in_src:
            empty_dirs.append(str(src))
            continue
        for p in files_in_src:
            total_files += 1
            target_file = target / p.name
            collision = None
            if target_file.is_file():
                # Use body sha (frontmatter stripped) so already-migrated files
                # with backfilled provenance don't trigger false collisions on
                # idempotent re-runs.
                src_sha = body_sha256(p)
                tgt_sha = body_sha256(target_file)
                if src_sha != tgt_sha:
                    collision = {
                        "source": str(p),
                        "target": str(target_file),
                        "src_sha256": src_sha,
                        "tgt_sha256": tgt_sha,
                    }
                    collisions.append(collision)
            targets.append({
                "source": str(p),
                "target": str(target_file),
                "slug": slug,
                "collision": collision is not None,
            })

    return {
        "source_root": str(source_root),
        "discovered_source_dirs": [str(p) for p in source_dirs],
        "discovered_subcomponent_dirs": [str(p) for p in subcomp_dirs],
        "targets": targets,
        "collisions": collisions,
        "empty_source_dirs": empty_dirs,
        "total_files": total_files,
    }


def apply_migration(
    plan: dict[str, Any],
    *,
    backup_path: Path,
    force_overwrite: bool = False,
    workdir_for_provenance: Path | None = None,
) -> dict[str, Any]:
    """Execute the migration in-place. Caller must have already taken a tarball.

    Returns a result dict shaped::

        {
          "moved": [{source, target, slug, src_ctime_iso, tgt_ctime_iso}],
          "skipped_identical": [{source, target}],
          "collisions_refused": [{source, target, src_sha256, tgt_sha256}],
          "stubs_written": [paths],
          "errors": [{source, error}],
          "manifest_path": "...",
        }
    """
    moved: list[dict[str, Any]] = []
    skipped_identical: list[dict[str, Any]] = []
    collisions_refused: list[dict[str, Any]] = []
    stubs_written: list[str] = []
    errors: list[dict[str, Any]] = []

    # Group targets by source_dir so we can write a single .MOVED.md per source
    by_source_dir: dict[str, list[dict[str, Any]]] = {}
    for t in plan["targets"]:
        src_dir = str(Path(t["source"]).parent)
        by_source_dir.setdefault(src_dir, []).append(t)

    for src_dir_str, file_plans in by_source_dir.items():
        src_dir = Path(src_dir_str)
        # All files in this group share a slug (they came from one repo memory dir)
        slug = file_plans[0]["slug"]
        target_dir = project_memory_dir_for_project(slug)
        target_dir.mkdir(parents=True, exist_ok=True)

        for fp in file_plans:
            src_file = Path(fp["source"])
            tgt_file = Path(fp["target"])
            try:
                if tgt_file.is_file():
                    # body sha = identity-preserving comparison (ignores
                    # provenance frontmatter the canonical writer may have
                    # added to the target on a prior migration run).
                    src_sha = body_sha256(src_file)
                    tgt_sha = body_sha256(tgt_file)
                    if src_sha == tgt_sha:
                        skipped_identical.append({
                            "source": str(src_file),
                            "target": str(tgt_file),
                        })
                        continue
                    if not force_overwrite:
                        collisions_refused.append({
                            "source": str(src_file),
                            "target": str(tgt_file),
                            "src_sha256": src_sha,
                            "tgt_sha256": tgt_sha,
                        })
                        continue
                # copy2 preserves mtime; we capture ctime separately below
                try:
                    src_ctime = src_file.stat().st_ctime
                except OSError:
                    src_ctime = None
                shutil.copy2(src_file, tgt_file)
                tgt_ctime = tgt_file.stat().st_ctime if tgt_file.exists() else None
                moved.append({
                    "source": str(src_file),
                    "target": str(tgt_file),
                    "slug": slug,
                    "src_ctime_iso": iso_utc(src_ctime) if src_ctime else None,
                    "tgt_ctime_iso": iso_utc(tgt_ctime) if tgt_ctime else None,
                })
            except OSError as e:
                errors.append({"source": str(src_file), "error": str(e)})

        # Write stub only after at least one file landed (avoid stub-without-content)
        # AND only when the source had any non-trivial content. Skip stub for
        # source dirs that were empty pre-migration.
        if any(m["source"].startswith(str(src_dir)) for m in moved):
            try:
                write_stub(src_dir, target_dir)
                stubs_written.append(str(src_dir / STUB_FILENAME))
            except OSError as e:
                errors.append({"source": str(src_dir), "error": f"stub write failed: {e}"})

    # Run memory_writer.migrate over each target dir to backfill provenance
    # frontmatter (created_at, source_workdir, source_run_id, etc.) using the
    # CANONICAL writer (C-MEMORY/canonical_writer). Errors are non-fatal —
    # surfaced in the result but don't roll back the copy.
    try:
        from memory_writer import migrate as canonical_migrate  # type: ignore  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        errors.append({
            "source": "memory_writer.migrate import",
            "error": f"could not load canonical writer: {e}",
        })
        canonical_migrate = None  # type: ignore[assignment]

    provenance_summary: dict[str, Any] = {}
    if canonical_migrate is not None:
        provenance_run_id = f"migrate_project_memory-{int(time.time())}"
        host = socket.gethostname()
        # Group migrated files by target dir, capturing a representative
        # source-repo workdir per group. The `source_workdir` provenance
        # field MUST point at the originating repo (where the lesson was
        # learned), NOT the consolidated target tree — otherwise the audit
        # trail this migration exists to create gets silently corrupted.
        target_to_source_repo: dict[Path, Path] = {}
        for m in moved:
            target_dir = Path(m["target"]).parent
            if target_dir in target_to_source_repo:
                continue
            # Source path shape: <repo>/.build-loop/memory/<file>
            # or for subcomponents: <repo>/<sub>/.build-loop/memory/<file>
            # Walk up until we leave the .build-loop tree to find the repo root.
            src = Path(m["source"])
            cursor = src.parent
            # Skip .build-loop/memory
            while cursor.name in {"memory", ".build-loop"} or cursor.parent.name == ".build-loop":
                cursor = cursor.parent
            # If a subcomponent (e.g. workers/), one more parent up reaches the repo
            if cursor.name in SUBCOMPONENT_PATTERNS:
                cursor = cursor.parent
            target_to_source_repo[target_dir] = cursor

        for td, source_repo in target_to_source_repo.items():
            workdir = workdir_for_provenance or source_repo
            try:
                res = canonical_migrate(
                    td,
                    run_id=provenance_run_id,
                    workdir=str(workdir),
                    host=host,
                    dry_run=False,
                )
                provenance_summary[str(td)] = {
                    "source_repo": str(source_repo),
                    "migrated_count": len(res.get("migrated", [])),
                    "skipped_count": len(res.get("skipped", [])),
                    "errors": res.get("errors", []),
                }
            except Exception as e:  # noqa: BLE001
                provenance_summary[str(td)] = {"error": str(e)}

    return {
        "moved": moved,
        "skipped_identical": skipped_identical,
        "collisions_refused": collisions_refused,
        "stubs_written": stubs_written,
        "provenance_backfill": provenance_summary,
        "errors": errors,
        "backup_tarball": str(backup_path),
    }


def write_summary_markdown(
    result: dict[str, Any],
    plan: dict[str, Any],
    manifest_path: Path,
    backup_path: Path,
) -> None:
    """Emit the operator-readable migration summary at manifest_path."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# Memory consolidation migration — {iso_utc()}")
    lines.append("")
    lines.append(f"Tarball backup: `{backup_path}`")
    lines.append("")
    lines.append(f"Rollback command:")
    lines.append("")
    lines.append("```bash")
    lines.append(f"python3 scripts/migrate_project_memory.py --rollback --manifest {manifest_path}")
    lines.append("```")
    lines.append("")
    moved = result.get("moved", [])
    lines.append(f"## Moved ({len(moved)} files)")
    lines.append("")
    if moved:
        lines.append("| source | target | slug | src_ctime |")
        lines.append("|---|---|---|---|")
        for m in moved:
            lines.append(
                f"| `{m['source']}` | `{m['target']}` | `{m['slug']}` | `{m.get('src_ctime_iso', '—')}` |"
            )
    else:
        lines.append("_(none)_")
    lines.append("")
    lines.append("## Skipped (identical content)")
    lines.append("")
    si = result.get("skipped_identical", [])
    if si:
        for s in si:
            lines.append(f"- `{s['source']}` ≡ `{s['target']}`")
    else:
        lines.append("_(none)_")
    lines.append("")
    lines.append(f"## Collisions refused ({len(result.get('collisions_refused', []))})")
    lines.append("")
    cr = result.get("collisions_refused", [])
    if cr:
        for c in cr:
            lines.append(f"- `{c['source']}` ≠ `{c['target']}` (src `{c['src_sha256'][:12]}…` vs tgt `{c['tgt_sha256'][:12]}…`)")
        lines.append("")
        lines.append("Re-run with `--force-overwrite` to overwrite, OR manually merge each file.")
    else:
        lines.append("_(none)_")
    lines.append("")
    lines.append(f"## Empty source dirs (no content to migrate)")
    lines.append("")
    for d in plan.get("empty_source_dirs", []):
        lines.append(f"- `{d}`")
    if not plan.get("empty_source_dirs"):
        lines.append("_(none)_")
    lines.append("")
    lines.append("## Stubs written")
    lines.append("")
    for s in result.get("stubs_written", []):
        lines.append(f"- `{s}`")
    if not result.get("stubs_written"):
        lines.append("_(none)_")
    lines.append("")
    lines.append("## Provenance backfill (via canonical memory_writer)")
    lines.append("")
    for td, info in result.get("provenance_backfill", {}).items():
        if "error" in info:
            lines.append(f"- `{td}` — ERROR: {info['error']}")
        else:
            lines.append(
                f"- `{td}` — migrated {info['migrated_count']} files; skipped {info['skipped_count']}"
            )
    if not result.get("provenance_backfill"):
        lines.append("_(none — memory_writer unavailable)_")
    lines.append("")
    lines.append("## Errors")
    lines.append("")
    for e in result.get("errors", []):
        lines.append(f"- `{e['source']}`: {e['error']}")
    if not result.get("errors"):
        lines.append("_(none)_")
    lines.append("")
    # Postgres slug reconciliation — best-effort, never fail the migration on it
    lines.append("## Postgres slug reconciliation")
    lines.append("")
    pg = _reconcile_postgres_slugs(plan)
    if pg["available"]:
        lines.append(f"- DB schema queried: `{pg['schema']}`")
        lines.append(f"- DB projects: {sorted(pg['db_projects'])}")
        lines.append(f"- Filesystem slugs: {sorted(pg['fs_slugs'])}")
        if pg["db_only"]:
            lines.append(f"- ⚠️ in DB but not in filesystem: {sorted(pg['db_only'])}")
        if pg["fs_only"]:
            lines.append(f"- ⚠️ in filesystem but not in DB: {sorted(pg['fs_only'])}")
        if not pg["db_only"] and not pg["fs_only"]:
            lines.append("- ✅ slug spaces aligned")
    else:
        lines.append(f"_skipped: {pg['reason']}_")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Generated by `scripts/migrate_project_memory.py`.")
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _reconcile_postgres_slugs(plan: dict[str, Any]) -> dict[str, Any]:
    """Best-effort: compare DB ``semantic_facts.project`` with filesystem slugs.

    Returns ``{available: bool, reason: str, db_projects, fs_slugs, db_only, fs_only}``.
    Never raises; ``available: False`` when Postgres is unreachable, the schema
    doesn't have a ``semantic_facts.project`` column, or the query times out.
    Failure mode is distinguished in the ``reason`` field so the operator can
    tell network-down from schema-mismatch.
    """
    fs_slugs = {t["slug"] for t in plan.get("targets", [])}
    try:
        import psycopg  # type: ignore  # noqa: PLC0415
    except Exception:
        return {"available": False, "reason": "psycopg not installed", "fs_slugs": fs_slugs}
    url = os.environ.get("BUILD_LOOP_DATABASE_URL")
    if not url:
        return {"available": False, "reason": "BUILD_LOOP_DATABASE_URL unset", "fs_slugs": fs_slugs}
    schema = os.environ.get("AGENT_MEMORY_SCHEMA") or "personal_memory"
    # Step 1: connect — labeled distinctly from query failure
    try:
        conn = psycopg.connect(url, connect_timeout=3)  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"db connect failed: {type(e).__name__}: {e}", "fs_slugs": fs_slugs}
    # Step 2: query with statement_timeout to bound slow-DB cases
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = 10000")
                cur.execute(
                    f"SELECT DISTINCT project FROM {schema}.semantic_facts "  # nosec: schema is a validated identifier (^[a-z][a-z0-9_]*$); values bound as params
                    f"WHERE project IS NOT NULL"
                )
                rows = cur.fetchall()
                db_projects = {r[0] for r in rows if r[0]}
    except Exception as e:  # noqa: BLE001
        return {
            "available": False,
            "reason": f"db query failed (likely schema/timeout): {type(e).__name__}: {e}",
            "fs_slugs": fs_slugs,
        }
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return {
        "available": True,
        "schema": schema,
        "db_projects": db_projects,
        "fs_slugs": fs_slugs,
        "db_only": db_projects - fs_slugs,
        "fs_only": fs_slugs - db_projects,
    }


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def rollback(manifest_path: Path) -> dict[str, Any]:
    """Reverse a previous migration using the tarball referenced in manifest.

    Steps:
      1. Parse the manifest to find the backup tarball path.
      2. Extract the tarball back over the original source paths.
      3. Remove the .MOVED.md stubs the migration left.
      4. Remove the files that were copied INTO the target tree.
      5. Emit a rollback-summary markdown.

    Returns ``{restored: [paths], removed_stubs: [paths], removed_targets: [paths], errors: [...]}``.
    """
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    text = manifest_path.read_text(encoding="utf-8")
    # Backup path is in a `Tarball backup:` line near the top.
    backup_path: Path | None = None
    for line in text.splitlines():
        if line.startswith("Tarball backup:"):
            # Format: "Tarball backup: `<path>`"
            after = line.split(":", 1)[1].strip()
            backup_path = Path(after.strip("`"))
            break
    if backup_path is None or not backup_path.is_file():
        raise FileNotFoundError(f"backup tarball missing or unreadable: {backup_path}")

    restored: list[str] = []
    removed_stubs: list[str] = []
    removed_targets: list[str] = []
    errors: list[dict[str, str]] = []

    # Parse the Moved/Stubs lists from the manifest. Format mirrors what
    # write_summary_markdown emits. We tolerate small formatting variation.
    moved_pairs: list[tuple[str, str]] = []
    in_moved = False
    for line in text.splitlines():
        if line.startswith("## Moved"):
            in_moved = True
            continue
        if line.startswith("##") and in_moved:
            in_moved = False
            continue
        if in_moved and line.startswith("| `") and " | " in line:
            # | `source` | `target` | `slug` | ...
            parts = [p.strip().strip("`") for p in line.split("|")[1:3]]
            if len(parts) == 2:
                moved_pairs.append((parts[0], parts[1]))
    stubs: list[str] = []
    in_stubs = False
    for line in text.splitlines():
        if line.startswith("## Stubs written"):
            in_stubs = True
            continue
        if line.startswith("##") and in_stubs:
            in_stubs = False
            continue
        if in_stubs and line.startswith("- `") and line.endswith("`"):
            stubs.append(line[3:-1])

    # Step 1: extract the tarball over the original source paths
    # Python 3.12+ provides `filter="data"` which mitigates CVE-2007-4559
    # (path-traversal in extractall). On older Python we hand-roll the
    # safety check: refuse to extract any member whose resolved path
    # escapes the original source root tree.
    try:
        with tarfile.open(backup_path, "r:gz") as tf:
            try:
                tf.extractall("/", filter="data")  # type: ignore[arg-type]
            except TypeError:
                # Pre-3.12 fallback: validate each member's path before extract.
                safe_members = []
                for member in tf.getmembers():
                    # Reject absolute paths after the leading slash, and any
                    # ".." segments. The tarball was generated by us with
                    # arcname=str(src).lstrip("/"), so members should look like
                    # "Users/tyroneross/dev/git-folder/<repo>/.build-loop/memory/...".
                    norm = os.path.normpath(member.name)
                    if norm.startswith("..") or "/.." in norm or norm.startswith("/"):
                        errors.append({
                            "step": "extract_tarball_safety",
                            "error": f"refused unsafe member: {member.name!r}",
                        })
                        continue
                    safe_members.append(member)
                tf.extractall("/", members=safe_members)
        for source, _ in moved_pairs:
            if Path(source).is_file():
                restored.append(source)
    except Exception as e:  # noqa: BLE001
        errors.append({"step": "extract_tarball", "error": str(e)})

    # Step 2: remove .MOVED.md stubs
    for stub in stubs:
        try:
            sp = Path(stub)
            if sp.is_file():
                sp.unlink()
                removed_stubs.append(stub)
        except OSError as e:
            errors.append({"step": "remove_stub", "stub": stub, "error": str(e)})

    # Step 3: remove the files that landed in the target tree
    for _, target in moved_pairs:
        try:
            tp = Path(target)
            if tp.is_file():
                tp.unlink()
                removed_targets.append(target)
        except OSError as e:
            errors.append({"step": "remove_target", "target": target, "error": str(e)})

    return {
        "restored": restored,
        "removed_stubs": removed_stubs,
        "removed_targets": removed_targets,
        "errors": errors,
        "backup_used": str(backup_path),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_plan(plan: dict[str, Any]) -> None:
    print(f"source_root: {plan['source_root']}")
    print(f"discovered source dirs: {len(plan['discovered_source_dirs'])}")
    for d in plan["discovered_source_dirs"]:
        print(f"  - {d}")
    print(f"discovered subcomponent dirs: {len(plan['discovered_subcomponent_dirs'])}")
    for d in plan["discovered_subcomponent_dirs"]:
        print(f"  - {d}")
    print(f"total files to migrate: {plan['total_files']}")
    print(f"collisions: {len(plan['collisions'])}")
    for c in plan["collisions"]:
        print(f"  - {c['source']} vs {c['target']} (sha mismatch)")
    print(f"empty source dirs: {len(plan['empty_source_dirs'])}")
    for d in plan["empty_source_dirs"]:
        print(f"  - {d}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--source-root",
        default=DEFAULT_SOURCE_ROOT,
        help=f"Where to look for <repo>/.build-loop/memory/ dirs (default: {DEFAULT_SOURCE_ROOT})",
    )
    p.add_argument("--dry-run", action="store_true", default=True, help="(default) plan only")
    p.add_argument("--apply", action="store_true", help="Execute the migration")
    p.add_argument("--check", action="store_true", help="Print the plan and exit (no writes)")
    p.add_argument(
        "--force-overwrite",
        action="store_true",
        help="Overwrite target files with differing content (sha256 mismatch). Default refuses.",
    )
    p.add_argument(
        "--rollback",
        action="store_true",
        help="Undo a previous migration using the manifest's backup tarball",
    )
    p.add_argument(
        "--manifest",
        default=None,
        help="Manifest path (required for --rollback; auto-generated for --apply)",
    )
    args = p.parse_args(argv)

    if args.apply and args.rollback:
        print("migrate: --apply and --rollback are mutually exclusive", file=sys.stderr)
        return 3
    if args.rollback:
        if not args.manifest:
            print("migrate: --rollback requires --manifest <path>", file=sys.stderr)
            return 3
        result = rollback(Path(args.manifest).expanduser())
        print(json.dumps(result, indent=2, default=str))
        return 0 if not result["errors"] else 1

    source_root = Path(os.path.expanduser(args.source_root)).resolve()
    plan = plan_migration(source_root)

    if args.check or (not args.apply and args.dry_run):
        # Dry-run: print plan, exit. No writes.
        _print_plan(plan)
        if plan["collisions"]:
            print("\nNote: --force-overwrite would be needed to override these collisions on --apply.")
        return 2 if plan["collisions"] and not args.force_overwrite else 0

    # APPLY path
    if not plan["targets"]:
        print("migrate: nothing to migrate")
        return 0

    # Tarball backup BEFORE any write.
    # Suffix includes microseconds + random nibble so back-to-back runs in
    # the same second can't collide on the tarball path.
    ts = int(time.time())
    rand_suffix = os.urandom(2).hex()
    backup_path = Path.home() / f"build-loop-memory-pre-migration-{ts}-{rand_suffix}.tgz"
    print(f"migrate: writing backup → {backup_path}")
    try:
        backup_sources = [Path(d) for d in plan["discovered_source_dirs"]] + [
            Path(d) for d in plan["discovered_subcomponent_dirs"]
        ]
        write_tarball_backup(backup_sources, backup_path)
    except FileExistsError as e:
        print(f"migrate: backup tarball already exists at {backup_path}: {e}", file=sys.stderr)
        print("migrate: re-run to generate a fresh suffix", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"migrate: backup failed ({type(e).__name__}): {e}", file=sys.stderr)
        return 1

    # Manifest path
    manifest_path = (
        Path(args.manifest).expanduser()
        if args.manifest
        else (build_loop_memory_root() / MANIFEST_DIR_NAME
              / f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-consolidation.md")
    )

    print(f"migrate: applying {len(plan['targets'])} file moves")
    result = apply_migration(
        plan,
        backup_path=backup_path,
        force_overwrite=args.force_overwrite,
    )

    print(f"migrate: writing summary → {manifest_path}")
    write_summary_markdown(result, plan, manifest_path, backup_path)
    result["manifest_path"] = str(manifest_path)

    summary = {
        "moved": len(result["moved"]),
        "skipped_identical": len(result["skipped_identical"]),
        "collisions_refused": len(result["collisions_refused"]),
        "errors": len(result["errors"]),
        "stubs_written": len(result["stubs_written"]),
        "manifest": str(manifest_path),
        "backup": str(backup_path),
    }
    print(json.dumps(summary, indent=2))
    if result["collisions_refused"] and not args.force_overwrite:
        return 2  # operator must intervene
    if result["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
