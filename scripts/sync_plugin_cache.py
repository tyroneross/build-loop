#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Sync a local plugin source repo into installed host caches.

Default behavior is intentionally conservative: materialize committed HEAD with
``git archive`` and sync that clean snapshot into the target cache. This keeps
unrelated dirty worktree edits out of Codex and Claude runtime caches.

Use ``--dirty`` only for explicit temporary runtime testing. Use ``--file`` to
copy a targeted slice without deleting the rest of the cache.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check_cache_sync as cache_sync  # type: ignore  # noqa: E402


EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".build-loop"}
HOOK_NAMES = ("post-commit", "post-merge", "post-checkout", "post-rewrite")
HOOK_MARKER = "# --- BEGIN build-loop plugin-cache-sync ---"
HOOK_MARKER_END = "# --- END build-loop plugin-cache-sync ---"
HOOK_SEGMENT_RE = re.compile(
    rf"{re.escape(HOOK_MARKER)}.*?{re.escape(HOOK_MARKER_END)}\n?",
    re.DOTALL,
)


class SyncError(RuntimeError):
    pass


def run_git(source: Path, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(source),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SyncError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def require_git_head(source: Path) -> None:
    inside = run_git(source, ["rev-parse", "--is-inside-work-tree"])
    if inside != "true":
        raise SyncError(f"{source} is not inside a git worktree; use --dirty for non-git sources")
    run_git(source, ["rev-parse", "--verify", "HEAD"])


def safe_extract_tar(tar_bytes: bytes, target: Path) -> None:
    target_resolved = target.resolve()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
        for member in archive.getmembers():
            member_target = (target / member.name).resolve()
            if target_resolved != member_target and target_resolved not in member_target.parents:
                raise SyncError(f"unsafe archive member path: {member.name}")
        archive.extractall(target)


def materialize_head(source: Path) -> tempfile.TemporaryDirectory[str]:
    require_git_head(source)
    top = Path(run_git(source, ["rev-parse", "--show-toplevel"])).resolve()
    try:
        rel = source.resolve().relative_to(top)
    except ValueError as exc:
        raise SyncError(f"{source} is not under git toplevel {top}") from exc
    treeish = "HEAD" if str(rel) == "." else f"HEAD:{rel.as_posix()}"
    proc = subprocess.run(
        ["git", "archive", "--format=tar", treeish],
        cwd=str(top),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise SyncError(f"git archive HEAD failed: {proc.stderr.decode(errors='ignore').strip()}")
    tmp = tempfile.TemporaryDirectory(prefix="build-loop-plugin-head-")
    safe_extract_tar(proc.stdout, Path(tmp.name))
    return tmp


def ignore_dirty_tree(_dir: str, names: list[str]) -> set[str]:
    return {name for name in names if name in EXCLUDE_DIRS}


def copy_tree_to_temp(source: Path, target_parent: Path) -> Path:
    target_parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".build-loop-sync-", dir=str(target_parent)))
    shutil.rmtree(tmp)
    shutil.copytree(source, tmp, ignore=ignore_dirty_tree)
    return tmp


def sync_full_tree(source: Path, cache: Path, dry_run: bool) -> int:
    files = [p for p in source.rglob("*") if p.is_file() and not any(part in EXCLUDE_DIRS for part in p.parts)]
    if dry_run:
        return len(files)
    if cache.resolve() == source.resolve() or cache.resolve() in source.resolve().parents:
        raise SyncError(f"refusing to sync into source path: {cache}")
    parent = cache.parent
    tmp = copy_tree_to_temp(source, parent)
    if cache.exists():
        shutil.rmtree(cache)
    os.replace(tmp, cache)
    return len(files)


def sync_targeted_files(source: Path, cache: Path, files: list[str], dry_run: bool) -> tuple[int, list[str]]:
    missing: list[str] = []
    copied = 0
    for raw in files:
        rel = raw.strip().lstrip("/")
        if not rel or rel.startswith("../") or "/../" in rel:
            missing.append(raw)
            continue
        src = source / rel
        if not src.is_file():
            missing.append(rel)
            continue
        copied += 1
        if dry_run:
            continue
        dst = cache / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return copied, missing


def host_list(host: str) -> list[str]:
    return ["claude", "codex"] if host == "all" else [host]


def load_host_manifest(source: Path, host: str, explicit: bool) -> dict[str, Any] | None:
    manifest_dir = ".codex-plugin" if host == "codex" else ".claude-plugin"
    if not (source / manifest_dir / "plugin.json").exists():
        if explicit:
            raise SyncError(f"missing {manifest_dir}/plugin.json under {source}")
        return None
    return cache_sync.load_manifest(source, host)


def resolve_cache(
    *,
    source: Path,
    host: str,
    manifest: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[Path, list[Path]]:
    if host == "claude" and args.claude_cache:
        return Path(args.claude_cache).expanduser().resolve(), []
    if host == "codex" and args.codex_cache:
        return Path(args.codex_cache).expanduser().resolve(), []
    if args.cache:
        if args.host == "all":
            raise SyncError("--cache can only be used with --host claude or --host codex")
        return Path(args.cache).expanduser().resolve(), []
    name = manifest.get("name")
    version = manifest.get("version")
    if not name or not version:
        raise SyncError(f"{host} plugin.json missing name or version")
    return cache_sync.default_cache(source, host, str(name), str(version), args.marketplace)


def refs_for_host(source: Path, host: str, manifest: dict[str, Any], files: list[str]) -> set[str]:
    if files:
        return {item.strip().lstrip("/") for item in files if item.strip()}
    if host == "codex":
        return cache_sync.find_codex_surfaces(source, manifest)
    refs = cache_sync.find_references(source)
    for extra in (".claude-plugin/plugin.json", "hooks/hooks.json"):
        if (source / extra).exists():
            refs.add(extra)
    return refs


def verify_host(source: Path, host: str, manifest: dict[str, Any], cache: Path, files: list[str]) -> list[dict[str, Any]]:
    return cache_sync.check_sync(source, cache, refs_for_host(source, host, manifest, files))


def sync_one_host(
    *,
    host: str,
    explicit_host: bool,
    source_for_sync: Path,
    source_for_manifest: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    manifest = load_host_manifest(source_for_manifest, host, explicit_host)
    if manifest is None:
        return {"host": host, "action": "skipped", "reason": "missing_manifest"}
    cache, stale_versions = resolve_cache(source=source_for_manifest, host=host, manifest=manifest, args=args)
    if args.files:
        copied, missing = sync_targeted_files(source_for_sync, cache, args.files, args.dry_run)
        action = "dry_run" if args.dry_run else "targeted_sync"
    else:
        copied = sync_full_tree(source_for_sync, cache, args.dry_run)
        missing = []
        action = "dry_run" if args.dry_run else "full_sync"

    diffs: list[dict[str, Any]] = []
    if not args.no_verify and not args.dry_run:
        diffs = verify_host(source_for_sync, host, manifest, cache, args.files)

    return {
        "host": host,
        "action": action,
        "plugin": manifest.get("name"),
        "version": manifest.get("version"),
        "cache": str(cache),
        "files_copied": copied,
        "missing_files": missing,
        "stale_versions": [str(path) for path in stale_versions],
        "verification_diffs": diffs,
        "ok": not missing and not diffs,
    }


def repo_toplevel(source: Path) -> Path:
    try:
        return Path(run_git(source, ["rev-parse", "--show-toplevel"])).resolve()
    except SyncError as exc:
        raise SyncError(f"cannot install hooks outside a git repo: {exc}") from exc


def hook_segment(script_path: Path, host: str) -> str:
    return f'''{HOOK_MARKER}
BUILD_LOOP_PLUGIN_CACHE_SYNC_TOPLEVEL="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -n "$BUILD_LOOP_PLUGIN_CACHE_SYNC_TOPLEVEL" ] && [ "${{BUILD_LOOP_PLUGIN_CACHE_SYNC_DISABLE:-}}" != "1" ]; then
  python3 {str(script_path)!r} --source "$BUILD_LOOP_PLUGIN_CACHE_SYNC_TOPLEVEL" --host {host} --quiet >/dev/null 2>&1 || true
fi
{HOOK_MARKER_END}
'''


def append_before_terminal_exit(script: str, segment: str) -> str:
    lines = script.rstrip().splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].strip() == "exit 0":
            before = "\n".join(lines[:idx]).rstrip()
            after = "\n".join(lines[idx:]).rstrip()
            return f"{before}\n\n{segment.rstrip()}\n{after}\n"
    return f"{script.rstrip()}\n\n{segment.rstrip()}\n"


def install_hook(hooks_dir: Path, name: str, segment: str) -> str:
    hook = hooks_dir / name
    existing = hook.read_text(encoding="utf-8") if hook.exists() else "#!/bin/sh\n"
    if not existing.startswith("#!"):
        existing = "#!/bin/sh\n" + existing
    cleaned = HOOK_SEGMENT_RE.sub("", existing)
    hook.write_text(append_before_terminal_exit(cleaned, segment), encoding="utf-8")
    mode = hook.stat().st_mode
    hook.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(hook)


def install_git_hooks(source: Path, host: str) -> dict[str, Any]:
    top = repo_toplevel(source)
    hooks_dir = top / ".git" / "hooks"
    if not hooks_dir.is_dir():
        raise SyncError(f"hooks directory not found: {hooks_dir}")
    segment = hook_segment(Path(__file__).resolve(), host)
    installed = [install_hook(hooks_dir, name, segment) for name in HOOK_NAMES]
    return {"action": "installed_hooks", "hooks": installed, "host": host, "source": str(top)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["claude", "codex", "all"], default="all")
    parser.add_argument("--source", default=".", help="Plugin source repo root.")
    parser.add_argument("--cache", default=None, help="Cache dir for a single explicit host.")
    parser.add_argument("--claude-cache", default=None, help="Claude cache dir for --host all or --host claude.")
    parser.add_argument("--codex-cache", default=None, help="Codex cache dir for --host all or --host codex.")
    parser.add_argument("--marketplace", default=None, help="Marketplace name override.")
    parser.add_argument("--dirty", action="store_true", help="Sync the dirty working tree instead of committed HEAD.")
    parser.add_argument("--file", action="append", dest="files", default=[], help="Target one relative file. Repeatable.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-verify", action="store_true", help="Skip post-sync cache verification.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--install-git-hooks",
        action="store_true",
        help="Install idempotent post-commit/merge/checkout/rewrite sync hooks and exit.",
    )
    return parser.parse_args(argv)


def print_human(results: list[dict[str, Any]], source_mode: str, materialized_source: Path) -> None:
    for result in results:
        host = result.get("host")
        action = result.get("action")
        if action == "skipped":
            print(f"- {host}: skipped ({result.get('reason')})")
            continue
        ok = "OK" if result.get("ok", True) else "WARN"
        print(f"- {host}: {ok} {action} {result.get('plugin')}@{result.get('version')}")
        if result.get("cache"):
            print(f"  cache: {result['cache']}")
        print(f"  source_mode: {source_mode}")
        print(f"  verified_source: {materialized_source}")
        print(f"  files_copied: {result.get('files_copied', 0)}")
        for path in result.get("missing_files") or []:
            print(f"  missing: {path}")
        diffs = result.get("verification_diffs") or []
        if diffs:
            print(f"  verification_diffs: {len(diffs)}")
            for diff in diffs[:20]:
                print(f"    [{diff.get('status')}] {diff.get('path')}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = Path(args.source).expanduser().resolve()
    if not source.is_dir():
        print(f"error: source is not a directory: {source}", file=sys.stderr)
        return 2

    try:
        if args.install_git_hooks:
            installed = install_git_hooks(source, args.host)
            if args.json:
                print(json.dumps(installed, indent=2, sort_keys=True))
            elif not args.quiet:
                print(f"installed plugin cache sync hooks for {args.host}:")
                for hook in installed["hooks"]:
                    print(f"  {hook}")
            return 0

        tmp: tempfile.TemporaryDirectory[str] | None = None
        if args.dirty:
            source_for_sync = source
            source_mode = "dirty"
        else:
            tmp = materialize_head(source)
            source_for_sync = Path(tmp.name)
            source_mode = "head"

        explicit_host = args.host != "all"
        results = [
            sync_one_host(
                host=host,
                explicit_host=explicit_host,
                source_for_sync=source_for_sync,
                source_for_manifest=source_for_sync,
                args=args,
            )
            for host in host_list(args.host)
        ]
        payload = {
            "ok": all(result.get("ok", True) for result in results),
            "source": str(source),
            "source_mode": source_mode,
            "materialized_source": str(source_for_sync),
            "results": results,
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        elif not args.quiet:
            print_human(results, source_mode, source_for_sync)
        if tmp is not None:
            tmp.cleanup()
        return 0 if payload["ok"] else 1
    except SyncError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
