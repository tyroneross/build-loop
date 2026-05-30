#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Prune stale plugin cache versions for Claude Code and Codex.

Both hosts keep versioned plugin cache directories:

    ~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/
    ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/

Dry-run is the default. Pass ``--apply`` to delete stale verified directories.
The script only deletes directories whose host-specific plugin manifest confirms
the same plugin name.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Literal

Host = Literal["codex", "claude"]

# Env vars a live host sets to the plugin's active cache version dir
# (.../cache/<marketplace>/<plugin>/<version>). The version dir a running
# session is loaded from MUST never be pruned — deleting it removes the
# session's plugin root and every one of its hooks fails mid-run with
# "Plugin directory does not exist".
IN_USE_ENV_VARS: tuple[str, ...] = ("CLAUDE_PLUGIN_ROOT", "CODEX_PLUGIN_ROOT")

HOST_CONFIG: dict[Host, dict[str, str]] = {
    "codex": {
        "manifest": ".codex-plugin/plugin.json",
        "cache": ".codex/plugins/cache",
    },
    "claude": {
        "manifest": ".claude-plugin/plugin.json",
        "cache": ".claude/plugins/cache",
    },
}


def load_manifest(source: Path, host: Host) -> dict[str, Any]:
    manifest_path = source / HOST_CONFIG[host]["manifest"]
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing {host} manifest: {manifest_path}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not data.get("name") or not data.get("version"):
        raise ValueError(f"{manifest_path} must contain name and version")
    return data


def load_cached_manifest(version_dir: Path, host: Host) -> dict[str, Any] | None:
    manifest_path = version_dir / HOST_CONFIG[host]["manifest"]
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def default_cache_root(host: Host) -> Path:
    return Path.home() / HOST_CONFIG[host]["cache"]


def iter_version_dirs(
    *,
    cache_root: Path,
    plugin_name: str,
    marketplace: str | None,
) -> list[Path]:
    if marketplace:
        plugin_root = cache_root / marketplace / plugin_name
        if not plugin_root.is_dir():
            return []
        return sorted(p for p in plugin_root.iterdir() if p.is_dir())

    if not cache_root.is_dir():
        return []

    versions: list[Path] = []
    for market_dir in sorted(p for p in cache_root.iterdir() if p.is_dir()):
        plugin_root = market_dir / plugin_name
        if not plugin_root.is_dir():
            continue
        versions.extend(p for p in plugin_root.iterdir() if p.is_dir())
    return sorted(versions)


def detect_in_use_versions(*, plugin_name: str) -> set[str]:
    """Cache version dir name(s) a live host session is currently loaded from.

    Read from the host plugin-root env vars. The path is intentionally NOT
    resolved: an in-use version dir may itself be a symlink (e.g. a local-dev
    override pointing at a working tree), and we must protect its *cache* name,
    not the symlink target's name. Only a path whose parent is this plugin's
    cache dir counts, so an unrelated env value can't over-protect.
    """
    names: set[str] = set()
    for var in IN_USE_ENV_VARS:
        root = os.environ.get(var)
        if not root:
            continue
        p = Path(root)
        if p.parent.name == plugin_name:
            names.add(p.name)
    return names


def classify_versions(
    *,
    version_dirs: list[Path],
    host: Host,
    plugin_name: str,
    keep_version: str,
    protected_names: frozenset[str] = frozenset(),
) -> tuple[list[Path], list[Path], list[Path]]:
    keep: list[Path] = []
    stale: list[Path] = []
    skipped: list[Path] = []

    for version_dir in version_dirs:
        # In-use / explicitly-protected dirs are kept by NAME, before any manifest
        # check — a live session's root may be a symlink whose manifest reads a
        # different version, but deleting it still breaks that session's hooks.
        if version_dir.name in protected_names:
            keep.append(version_dir)
            continue
        cached_manifest = load_cached_manifest(version_dir, host)
        if cached_manifest is None or cached_manifest.get("name") != plugin_name:
            skipped.append(version_dir)
            continue
        if cached_manifest.get("version") == keep_version and version_dir.name == keep_version:
            keep.append(version_dir)
            continue
        stale.append(version_dir)

    return keep, stale, skipped


def remove_cache_entry(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    shutil.rmtree(path)


def prune_host(
    *,
    host: Host,
    source: Path,
    cache_root: Path,
    marketplace: str | None,
    plugin_override: str | None,
    keep_version_override: str | None,
    protect: list[str] | None,
    detect_in_use: bool,
    apply: bool,
) -> tuple[dict[str, Any], int]:
    manifest = load_manifest(source, host)
    plugin_name = plugin_override or manifest["name"]
    keep_version = keep_version_override or manifest["version"]
    protected_names: set[str] = set(protect or [])
    if detect_in_use:
        protected_names.update(detect_in_use_versions(plugin_name=plugin_name))
    version_dirs = iter_version_dirs(
        cache_root=cache_root,
        plugin_name=plugin_name,
        marketplace=marketplace,
    )
    keep, stale, skipped = classify_versions(
        version_dirs=version_dirs,
        host=host,
        plugin_name=plugin_name,
        keep_version=keep_version,
        protected_names=frozenset(protected_names),
    )

    deleted: list[Path] = []
    errors: list[dict[str, str]] = []
    if apply:
        for path in stale:
            try:
                remove_cache_entry(path)
                deleted.append(path)
            except OSError as exc:
                errors.append({"path": str(path), "error": str(exc)})

    return {
        "host": host,
        "plugin": plugin_name,
        "keep_version": keep_version,
        "protected": sorted(protected_names),
        "cache_root": str(cache_root),
        "marketplace": marketplace,
        "dry_run": not apply,
        "kept": [str(p) for p in keep],
        "stale": [str(p) for p in stale],
        "deleted": [str(p) for p in deleted],
        "skipped_unverified": [str(p) for p in skipped],
        "errors": errors,
    }, 1 if errors else 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Prune stale plugin cache versions.")
    p.add_argument("--source", default=".", help="Plugin source repo root. Defaults to cwd.")
    p.add_argument("--host", choices=["codex", "claude", "all"], default="all")
    p.add_argument("--plugin", default=None, help="Plugin name override. Defaults to host manifest name.")
    p.add_argument("--keep-version", default=None, help="Version to keep. Defaults to host manifest version.")
    p.add_argument(
        "--protect",
        action="append",
        default=None,
        metavar="VERSION",
        help="Cache version dir name to never prune (repeatable). Use for a version "
        "an active session is running but the manifest no longer points at.",
    )
    p.add_argument(
        "--no-detect-in-use",
        action="store_true",
        help="Disable auto-protecting the version dir the running host session is "
        "loaded from (read from CLAUDE_PLUGIN_ROOT / CODEX_PLUGIN_ROOT).",
    )
    p.add_argument("--marketplace", default=None, help="Optional marketplace name for the selected host(s).")
    p.add_argument("--codex-marketplace", default=None, help="Optional Codex marketplace name.")
    p.add_argument("--claude-marketplace", default=None, help="Optional Claude marketplace name.")
    p.add_argument("--cache-root", default=None, help="Cache root override for a single --host run.")
    p.add_argument("--codex-cache-root", default=None, help="Codex cache root override.")
    p.add_argument("--claude-cache-root", default=None, help="Claude cache root override.")
    p.add_argument("--apply", action="store_true", help="Delete stale directories. Default is dry-run.")
    p.add_argument("--json", action="store_true", help="Emit JSON report.")
    return p.parse_args()


def selected_hosts(host_arg: str) -> list[Host]:
    if host_arg == "all":
        return ["codex", "claude"]
    return [host_arg]  # type: ignore[list-item]


def cache_root_for(args: argparse.Namespace, host: Host) -> Path:
    specific = getattr(args, f"{host}_cache_root")
    if args.cache_root and len(selected_hosts(args.host)) > 1:
        raise ValueError("--cache-root can only be used with --host codex or --host claude")
    value = specific or args.cache_root
    if value:
        return Path(value).expanduser().resolve()
    return default_cache_root(host).expanduser().resolve()


def marketplace_for(args: argparse.Namespace, host: Host) -> str | None:
    return getattr(args, f"{host}_marketplace") or args.marketplace


def print_human(reports: list[dict[str, Any]]) -> None:
    for index, report in enumerate(reports):
        if index:
            print()
        mode = "deleted" if not report["dry_run"] else "would delete"
        print(
            f"{report['host']} cache prune for {report['plugin']}; "
            f"keeping {report['keep_version']}"
        )
        print(f"cache root: {report['cache_root']}")
        if report["marketplace"]:
            print(f"marketplace: {report['marketplace']}")
        protected_extra = [v for v in report.get("protected", []) if v != report["keep_version"]]
        if protected_extra:
            print(f"protected (in-use/explicit): {', '.join(protected_extra)}")
        if not report["stale"]:
            print("No stale cache versions found.")
        else:
            print(f"Stale cache versions ({mode}):")
            for path in report["stale"]:
                print(f"  {path}")
        if report["skipped_unverified"]:
            print("Skipped unverified cache directories:")
            for path in report["skipped_unverified"]:
                print(f"  {path}")
        if report["errors"]:
            print("Errors:", file=sys.stderr)
            for error in report["errors"]:
                print(f"  {error['path']}: {error['error']}", file=sys.stderr)


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()

    reports: list[dict[str, Any]] = []
    exit_code = 0
    try:
        for host in selected_hosts(args.host):
            report, code = prune_host(
                host=host,
                source=source,
                cache_root=cache_root_for(args, host),
                marketplace=marketplace_for(args, host),
                plugin_override=args.plugin,
                keep_version_override=args.keep_version,
                protect=args.protect,
                detect_in_use=not args.no_detect_in_use,
                apply=args.apply,
            )
            reports.append(report)
            exit_code = max(exit_code, code)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload: dict[str, Any] = reports[0] if len(reports) == 1 else {"reports": reports}
        print(json.dumps(payload, indent=2))
    else:
        print_human(reports)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
