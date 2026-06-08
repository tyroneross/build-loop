#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""plugin_dir_heal.py — plugin-relative SessionStart healer.

Heals two failure modes of Claude Code's plugin cache GC:

  A. ARCHIVE-TO-REMOVED case. /plugin install moves the prior versioned dir
     into ~/.claude/plugins/removed/<tag>/... Recovery: MOVE the archived
     copy back to its registered installPath.

  B. HARD-DELETE-AFTER-SUCCESSFUL-UPDATE case (the bug bl-plugin-cache-gc-
     selfheal exists for). CC-core /plugin update of a NEWER version, after
     install succeeds, hard-deletes the OLD versioned dir with no copy under
     removed/. Nothing to move back. Recovery: create a SYMLINK from the
     missing old installPath to the newest sibling version dir in the same
     plugin cache parent. CC's pre-validate-dir-exists check then passes,
     and ${CLAUDE_PLUGIN_ROOT}=.../<old> resolves to the new scripts.

Plugin-relative deployment: this file lives in the plugin tree
(scripts/hooks/), so every install gets next-session recovery without
depending on the dev's global ~/.claude config (portable-automation rule).

Same shape and safety properties as the dev-global healer:
  - Lock file ~/.claude/plugins/.plugin-dir-heal.lock — single-runner.
  - Kill switch ~/.claude/settings.json.disable-plugin-dir-heal — opt-out.
  - Atomic os.rename on POSIX; cross-fs fallback.
  - ALWAYS exit 0; never blocks session start. Wall-clock budget guard.

Honest limit: only the NEXT session is healed. CC pins
${CLAUDE_PLUGIN_ROOT} at session start and validates it before any hook
runs, so the currently-running session that already lost its dir cannot
self-heal.

Entry points
  python3 plugin_dir_heal.py            # session-start mode
  python3 plugin_dir_heal.py --dry-run  # report only, no moves/symlinks
  python3 plugin_dir_heal.py --verbose  # also print summary to stdout
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BUDGET_SECONDS = 10


def _claude_home() -> Path:
    override = os.environ.get("CLAUDE_HOME_OVERRIDE")
    if override:
        return Path(override)
    return Path.home() / ".claude"


def _paths() -> dict:
    home = _claude_home()
    plugins = home / "plugins"
    return {
        "home": home,
        "plugins": plugins,
        "registry": plugins / "installed_plugins.json",
        "removed": plugins / "removed",
        "cache": plugins / "cache",
        "lock": plugins / ".plugin-dir-heal.lock",
        "kill_switch": home / "settings.json.disable-plugin-dir-heal",
        "log_dir": home / "logs",
        "log_file": home / "logs" / "plugin-dir-heal.log",
    }


def _log(msg: str) -> None:
    P = _paths()
    try:
        P["log_dir"].mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}\n"
    try:
        with P["log_file"].open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def acquire_lock() -> bool:
    P = _paths()
    try:
        P["plugins"].mkdir(parents=True, exist_ok=True)
        fd = os.open(str(P["lock"]), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}\n")
        return True
    except FileExistsError:
        return False
    except OSError as e:
        _log(f"WARN: lock acquire failed (treating as unlocked): {e}")
        return True


def release_lock() -> None:
    P = _paths()
    try:
        if P["lock"].exists():
            P["lock"].unlink()
    except OSError as e:
        _log(f"WARN: lock release failed: {e}")


def load_registry() -> Optional[dict]:
    P = _paths()
    if not P["registry"].exists():
        _log(f"INFO: registry not found at {P['registry']} (nothing to heal)")
        return None
    try:
        with P["registry"].open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        _log(f"ERROR: cannot read registry: {e}")
        return None


def _is_plugin_version_dir(path: Path, plugin_name: str, version: str) -> bool:
    """True if path is a copy of <plugin>@<version>. Manifest match wins;
    falls back to dir-name == version."""
    if not path.is_dir():
        return False
    manifest = path / ".claude-plugin" / "plugin.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if data.get("name") == plugin_name and str(data.get("version") or "") == str(version):
            return True
    return path.name == version


def find_archived_match(plugin_name: str, version: str) -> Optional[Path]:
    """Search ~/.claude/plugins/removed/ for an archived <plugin>@<version>.

    Recognises:
      A) removed/<tag>/<version-or-root>/
      B) removed/<tag>/<plugin>/<version-or-root>/

    Picks the most-recently-mtimed candidate.
    """
    P = _paths()
    removed = P["removed"]
    if not removed.is_dir():
        return None

    candidates: list[Path] = []
    try:
        tags = sorted([d for d in removed.iterdir() if d.is_dir()], reverse=True)
    except OSError:
        return None

    for tag_dir in tags:
        try:
            children = list(tag_dir.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            if _is_plugin_version_dir(child, plugin_name, version):
                candidates.append(child)
                continue
            try:
                grand = list(child.iterdir())
            except OSError:
                continue
            for gc in grand:
                if gc.is_dir() and _is_plugin_version_dir(gc, plugin_name, version):
                    candidates.append(gc)

    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def find_live_sibling(install_path: Path, plugin_name: str) -> Optional[Path]:
    """Find the newest sibling version dir of `install_path`'s parent that has
    a valid plugin.json for `plugin_name`. Used for the HARD-DELETE-AFTER-
    UPDATE case where nothing in removed/ matches but a newer install of the
    same plugin already exists in cache (CC-core just installed it).

    Returns the live sibling's path, or None if no eligible sibling found.
    Skips `install_path.name` itself (it's the missing dir) and any path
    that is already a symlink (don't chain symlinks).
    """
    parent = install_path.parent
    if not parent.is_dir():
        return None
    candidates: list[Path] = []
    try:
        siblings = list(parent.iterdir())
    except OSError:
        return None
    for sib in siblings:
        if sib.name == install_path.name:
            continue
        if sib.is_symlink():
            # Skip already-symlinked entries; we want a real dir as target.
            continue
        if not sib.is_dir():
            continue
        manifest = sib / ".claude-plugin" / "plugin.json"
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("name") != plugin_name:
            continue
        candidates.append(sib)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def restore_dir(src: Path, dest: Path, dry_run: bool) -> bool:
    """Move src to dest. Creates parent. Refuses to clobber existing dest."""
    if dest.exists() or dest.is_symlink():
        _log(f"REFUSE: destination already exists: {dest}")
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _log(f"ERROR: cannot create parent {dest.parent}: {e}")
        return False
    if dry_run:
        _log(f"DRY-RUN would move {src} -> {dest}")
        return True
    try:
        os.rename(src, dest)
    except OSError:
        try:
            shutil.copytree(src, dest, symlinks=True)
            shutil.rmtree(src)
        except (OSError, shutil.Error) as e:
            _log(f"ERROR: cross-fs restore failed {src} -> {dest}: {e}")
            return False
    return True


def symlink_to_sibling(sibling: Path, dest: Path, dry_run: bool) -> bool:
    """Create a symlink from dest -> sibling. Prefer a RELATIVE link (just the
    sibling's basename) so the link stays valid if the cache root moves on
    disk. Refuses to clobber existing dest.
    """
    if dest.exists() or dest.is_symlink():
        _log(f"REFUSE: symlink dest already exists: {dest}")
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _log(f"ERROR: cannot create parent {dest.parent}: {e}")
        return False
    # Relative link (sibling.name) when both live under the same parent;
    # falls back to absolute if for some reason parents differ.
    if sibling.parent == dest.parent:
        target = Path(sibling.name)
    else:
        target = sibling
    if dry_run:
        _log(f"DRY-RUN would symlink {dest} -> {target}")
        return True
    try:
        os.symlink(str(target), str(dest), target_is_directory=True)
    except OSError as e:
        _log(f"ERROR: symlink failed {dest} -> {target}: {e}")
        return False
    return True


def heal_once(dry_run: bool) -> dict:
    counts = {
        "healthy": 0,
        "restored_from_removed": 0,
        "symlinked_to_sibling": 0,
        "no_recovery_path": 0,
        "destination_conflict": 0,
        "errors": 0,
        "symlink_skip": 0,
        "missing_installpath_field": 0,
    }

    registry = load_registry()
    if registry is None:
        return counts

    plugins_section = registry.get("plugins") or {}
    if not plugins_section:
        return counts

    started = time.monotonic()
    deadline = started + BUDGET_SECONDS

    for plugin_key, entries in plugins_section.items():
        if "@" not in plugin_key:
            continue
        plugin_name, _marketplace = plugin_key.split("@", 1)
        if not isinstance(entries, list):
            entries = [entries]

        for entry in entries:
            if time.monotonic() > deadline:
                _log("DEFER: budget exhausted mid-scan")
                return counts

            install_path_str = entry.get("installPath", "")
            version = str(entry.get("version") or "")
            if not install_path_str or not version:
                counts["missing_installpath_field"] += 1
                continue

            install_path = Path(install_path_str)

            if install_path.is_symlink():
                # Already symlinked — fine, even if target now differs.
                # (Pruner protects by NAME, not by resolved target.)
                counts["symlink_skip"] += 1
                continue

            if install_path.exists():
                counts["healthy"] += 1
                continue

            # Missing. Try A) restore from removed/ first.
            archived = find_archived_match(plugin_name, version)
            if archived is not None:
                ok = restore_dir(archived, install_path, dry_run=dry_run)
                if ok:
                    counts["restored_from_removed"] += 1
                    _log(
                        f"RESTORED {plugin_key}@{version}: "
                        f"{archived} -> {install_path}"
                        + (" [dry-run]" if dry_run else "")
                    )
                else:
                    if install_path.exists() or install_path.is_symlink():
                        counts["destination_conflict"] += 1
                    else:
                        counts["errors"] += 1
                continue

            # B) Symlink to a live sibling version of the same plugin.
            sibling = find_live_sibling(install_path, plugin_name)
            if sibling is not None:
                ok = symlink_to_sibling(sibling, install_path, dry_run=dry_run)
                if ok:
                    counts["symlinked_to_sibling"] += 1
                    _log(
                        f"SYMLINKED {plugin_key}@{version}: "
                        f"{install_path} -> {sibling}"
                        + (" [dry-run]" if dry_run else "")
                    )
                else:
                    if install_path.exists() or install_path.is_symlink():
                        counts["destination_conflict"] += 1
                    else:
                        counts["errors"] += 1
                continue

            # Neither path works — registry entry is genuinely dead.
            counts["no_recovery_path"] += 1
            _log(
                f"NO-RECOVERY {plugin_key}@{version}: installPath missing "
                f"({install_path}); no archive under removed/, no live sibling"
            )

    return counts


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Heal dangling plugin install dirs (restore from removed/ or symlink to live sibling)"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be healed without acting")
    parser.add_argument("--verbose", action="store_true",
                        help="Also print summary to stdout")
    args = parser.parse_args(argv)

    P = _paths()
    if P["kill_switch"].exists():
        if args.verbose:
            print(f"kill-switch present: {P['kill_switch']}")
        return 0

    if not args.dry_run and not acquire_lock():
        _log(f"SKIP RUN: lock present at {P['lock']} (concurrent run?)")
        if args.verbose:
            print(f"locked: {P['lock']}")
        return 0

    try:
        started = time.monotonic()
        _log(f"RUN-START dry_run={args.dry_run} registry={P['registry']}")
        counts = heal_once(dry_run=args.dry_run)
        elapsed = time.monotonic() - started
        summary = " ".join(f"{k}={v}" for k, v in counts.items() if v)
        _log(f"RUN-END elapsed_s={elapsed:.2f} " + (summary or "no-action"))
        if args.verbose:
            print(f"done: {summary or 'no-action'} elapsed={elapsed:.2f}s")
        return 0
    finally:
        if not args.dry_run:
            release_lock()


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:  # noqa: BLE001
        _log(f"FATAL: unhandled exception: {e!r}")
        try:
            release_lock()
        except Exception:
            pass
        sys.exit(0)
