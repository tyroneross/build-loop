#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""cleanup_legacy_memory_stubs.py — safely remove migration .MOVED.md stubs.

After the memory-consolidation series (PR 1 → PR 1.5 → PR 2 → PR 3) lands,
the `.MOVED.md` stubs left at `<repo>/.build-loop/memory/` by the migration
script are inert — nothing reads them. They can be cleaned up.

The cleanup is non-trivial: the migration script COPIES files (preserves
originals) and writes a stub alongside. If the operator (or a parallel
agent) added content at the legacy path AFTER the migration ran, those
files would be lost by a naive `rm -rf $d`. This script enforces the
safety contract:

  1. Refuse to remove a directory that contains anything other than
     `.MOVED.md` (and macOS noise like .DS_Store).
  2. Default to --dry-run; --apply required to actually remove.
  3. Print what's in each non-empty directory so the operator can
     decide whether to migrate the extra content first or accept the
     loss.

Usage:
  python3 scripts/cleanup_legacy_memory_stubs.py                # dry-run
  python3 scripts/cleanup_legacy_memory_stubs.py --apply        # remove stub-only dirs
  python3 scripts/cleanup_legacy_memory_stubs.py --apply --force-unsafe
                                                                # remove dirs with extra
                                                                # content (DANGEROUS)
  python3 scripts/cleanup_legacy_memory_stubs.py --source-root ~/dev/git-folder

Exit codes:
  0 — all directories were stub-only OR all removals succeeded
  1 — some directories had unmigrated content and were skipped (default)
      OR --apply failed on at least one removal
  2 — invalid arguments
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

DEFAULT_SOURCE_ROOT = "~/dev/git-folder"
LEGACY_MEMORY_REL = ".build-loop/memory"
STUB_FILENAME = ".MOVED.md"

# Filenames that are noise, not real content. Cleanup tolerates these.
NOISE = {STUB_FILENAME, ".DS_Store", "Thumbs.db"}


def _discover_legacy_dirs(source_root: Path) -> list[Path]:
    """Find every legacy memory dir under source_root/*/."""
    out: list[Path] = []
    if not source_root.is_dir():
        return out
    for repo in sorted(source_root.iterdir()):
        if not repo.is_dir():
            continue
        mem = repo / LEGACY_MEMORY_REL
        if mem.is_dir():
            out.append(mem)
        # Also probe well-known sub-component dirs (workers/)
        for sub in ("workers",):
            sub_mem = repo / sub / LEGACY_MEMORY_REL
            if sub_mem.is_dir():
                out.append(sub_mem)
    return out


def _classify_dir(path: Path) -> tuple[bool, list[str]]:
    """Return (is_stub_only, extra_files).

    is_stub_only: True iff the directory contains ONLY .MOVED.md + noise.
    extra_files: filenames present that would be lost on removal.
    """
    if not path.is_dir():
        return False, []
    entries = [p.name for p in path.iterdir()]
    extras = [n for n in entries if n not in NOISE]
    has_stub = STUB_FILENAME in entries
    return (has_stub and not extras), extras


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--source-root",
        default=DEFAULT_SOURCE_ROOT,
        help=f"Where to look for legacy dirs (default: {DEFAULT_SOURCE_ROOT})",
    )
    p.add_argument("--apply", action="store_true", help="Execute removals (default: dry-run)")
    p.add_argument(
        "--force-unsafe",
        action="store_true",
        help="Also remove directories with unmigrated content (DANGEROUS — data loss)",
    )
    args = p.parse_args(argv)

    source_root = Path(os.path.expanduser(args.source_root)).resolve()
    legacy_dirs = _discover_legacy_dirs(source_root)
    if not legacy_dirs:
        print(f"cleanup: no legacy memory directories under {source_root}")
        return 0

    print(f"cleanup: scanning {len(legacy_dirs)} legacy memory dir(s) under {source_root}")
    print(f"  mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    if args.apply and args.force_unsafe:
        print("  ⚠️  --force-unsafe — will remove dirs even with unmigrated content")
    print()

    safe_to_remove: list[Path] = []
    unsafe: list[tuple[Path, list[str]]] = []
    no_stub: list[Path] = []

    for d in legacy_dirs:
        is_safe, extras = _classify_dir(d)
        if not (d / STUB_FILENAME).is_file():
            no_stub.append(d)
            continue
        if is_safe:
            safe_to_remove.append(d)
        else:
            unsafe.append((d, extras))

    if safe_to_remove:
        print(f"✅ Safe to remove ({len(safe_to_remove)} dirs — contain only stub + noise):")
        for d in safe_to_remove:
            print(f"  - {d}")
        print()

    if unsafe:
        print(f"⚠️  Unmigrated content ({len(unsafe)} dirs):")
        for d, extras in unsafe:
            print(f"  - {d}  (extras: {extras[:5]}{'...' if len(extras) > 5 else ''})")
        print()
        print("  Migrate this content first with:")
        print("    python3 scripts/migrate_project_memory.py --apply")
        print("  Or remove anyway with --apply --force-unsafe (DATA LOSS).")
        print()

    if no_stub:
        print(f"– No .MOVED.md stub ({len(no_stub)} dirs — not from a recent migration):")
        for d in no_stub:
            print(f"  - {d}")
        print()

    if not args.apply:
        print("(dry-run — re-run with --apply to execute removals)")
        return 0 if not unsafe else 1

    # APPLY mode
    failed: list[tuple[Path, str]] = []
    removed: list[Path] = []
    for d in safe_to_remove:
        try:
            shutil.rmtree(d)
            removed.append(d)
        except OSError as e:
            failed.append((d, str(e)))
    if args.force_unsafe:
        for d, _ in unsafe:
            try:
                shutil.rmtree(d)
                removed.append(d)
            except OSError as e:
                failed.append((d, str(e)))

    print(f"cleanup: removed {len(removed)} dir(s)")
    if failed:
        print(f"cleanup: failed {len(failed)}:")
        for d, err in failed:
            print(f"  - {d}: {err}")
        return 1
    # Exit 1 if there were unsafe dirs the operator did NOT --force-unsafe
    if unsafe and not args.force_unsafe:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
