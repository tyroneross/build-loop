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
import re
import shutil
import subprocess
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


def detect_in_use_versions(*, plugin_name: str, scan_peers: bool = True) -> set[str]:
    """Cache version dir name(s) a live host session is currently loaded from.

    Reads:
      1. THIS process's env vars (CLAUDE_PLUGIN_ROOT / CODEX_PLUGIN_ROOT) —
         original behaviour; always on.
      2. PEER live host processes' env (when ``scan_peers``, default on) —
         covers the multi-session case (cross-terminal Claude + Claude, or
         Claude + Codex) where another session is bound to a different
         version of the same plugin. The cross-user fix for
         `bl-plugin-cache-gc-selfheal`.

    The path is intentionally NOT resolved: an in-use version dir may itself
    be a symlink (e.g. a local-dev override pointing at a working tree), and
    we must protect its *cache* name, not the symlink target's name. Only a
    path whose parent is this plugin's cache dir counts, so an unrelated env
    value can't over-protect.

    Peer scan is best-effort and fail-open: if ``ps -E`` / ``/proc`` are
    unavailable or return nothing, we fall back to the current-process scan.
    Never raises.
    """
    names: set[str] = set()
    for var in IN_USE_ENV_VARS:
        root = os.environ.get(var)
        if not root:
            continue
        p = Path(root)
        if p.parent.name == plugin_name:
            names.add(p.name)
    if scan_peers:
        try:
            names.update(_detect_peer_in_use_versions(plugin_name=plugin_name))
        except Exception:  # noqa: BLE001 - fail-open on ANY error
            pass
    return names


# Matches IN_USE_ENV_VARS values in a ps -E / /proc/environ blob. Captures
# the path up to the first whitespace or NUL byte. Env-var values can't
# legally contain whitespace via shell export, so this is safe and avoids
# bleeding into an adjacent var.
_ENV_VAR_PATTERN = re.compile(
    r"(?:^|[ \t\0])(" + "|".join(re.escape(v) for v in IN_USE_ENV_VARS) + r")=([^\s\0]+)"
)


def _detect_peer_in_use_versions(*, plugin_name: str) -> set[str]:
    """Scan peer live processes' environment for CLAUDE_PLUGIN_ROOT /
    CODEX_PLUGIN_ROOT pinned to ``plugin_name``'s cache. Returns the version
    dir names found.

    Strategy by platform:
      - Linux:  read /proc/<pid>/environ for every same-uid process.
      - macOS:  ``ps -E`` (BSD) prints env vars after the command, restricted
                to the current user.

    Best-effort. Returns ``set()`` on any failure.
    """
    names: set[str] = set()
    # Linux fast-path: /proc is the most reliable read.
    proc_root = Path("/proc")
    if proc_root.is_dir():
        try:
            my_uid = os.getuid()
        except AttributeError:
            my_uid = None
        try:
            entries = list(proc_root.iterdir())
        except OSError:
            return names
        for entry in entries:
            if not entry.name.isdigit():
                continue
            if entry.name == str(os.getpid()):
                continue
            if my_uid is not None:
                try:
                    if entry.stat().st_uid != my_uid:
                        continue
                except OSError:
                    continue
            environ_path = entry / "environ"
            try:
                blob = environ_path.read_bytes()
            except OSError:
                continue
            try:
                text = blob.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            for match in _ENV_VAR_PATTERN.finditer(text):
                _name = _version_from_root(match.group(2), plugin_name)
                if _name:
                    names.add(_name)
        return names

    # macOS / BSD: `ps -E` shows env after the command. Restrict to current
    # user (-u $USER); env-only for other users requires root.
    user = os.environ.get("USER") or ""
    cmd: list[str]
    if user:
        cmd = ["ps", "-E", "-o", "pid=,command=", "-u", user]
    else:
        cmd = ["ps", "-E", "-o", "pid=,command=", "-ax"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=2, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return names
    my_pid = str(os.getpid())
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if not parts:
            continue
        if parts[0] == my_pid:
            continue
        for match in _ENV_VAR_PATTERN.finditer(line):
            _name = _version_from_root(match.group(2), plugin_name)
            if _name:
                names.add(_name)
    return names


def _version_from_root(root_value: str, plugin_name: str) -> str | None:
    """Extract the version dir name from an env-var value when it points at
    a cache dir for ``plugin_name``. Same un-resolved match rule as the
    current-process detector — protect by NAME, not resolved target.
    """
    if not root_value:
        return None
    p = Path(root_value)
    if p.parent.name == plugin_name:
        return p.name
    return None


def installed_pinned_versions(*, plugin_name: str, host: Host) -> set[str]:
    """Cache version dir names the host has pinned for this plugin across ALL
    scopes/projects, read from its installed_plugins.json. These belong to other
    projects or concurrent sessions and must never be pruned — deleting a pinned
    version breaks that install on next load. Complements detect_in_use_versions
    (this process only) for safe automated/session-start pruning."""
    registry = (Path.home() / HOST_CONFIG[host]["cache"]).parent / "installed_plugins.json"
    names: set[str] = set()
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return names
    for key, entries in (data.get("plugins") or {}).items():
        if key.split("@", 1)[0] != plugin_name:
            continue
        for entry in entries or []:
            install_path = entry.get("installPath")
            if install_path:
                names.add(Path(install_path).name)
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
    scan_peers: bool,
    protect_installed: bool,
    apply: bool,
) -> tuple[dict[str, Any], int]:
    manifest = load_manifest(source, host)
    plugin_name = plugin_override or manifest["name"]
    keep_version = keep_version_override or manifest["version"]
    protected_names: set[str] = set(protect or [])
    if detect_in_use:
        protected_names.update(
            detect_in_use_versions(plugin_name=plugin_name, scan_peers=scan_peers)
        )
    if protect_installed:
        protected_names.update(installed_pinned_versions(plugin_name=plugin_name, host=host))
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
    p.add_argument(
        "--no-scan-peers",
        action="store_true",
        help="Don't scan PEER live host processes' env for in-use versions. "
        "Default-on peer scan protects multi-session pins (Claude + Claude, "
        "Claude + Codex) so a prune in one session doesn't kill another's hooks.",
    )
    p.add_argument(
        "--protect-installed",
        action="store_true",
        help="Protect every cache version pinned in the host's installed_plugins.json "
        "(all scopes/projects), not just this process's in-use dir. Use for "
        "automated/session-start pruning so concurrent or other-project installs "
        "are never deleted.",
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
                scan_peers=not args.no_scan_peers,
                protect_installed=args.protect_installed,
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
