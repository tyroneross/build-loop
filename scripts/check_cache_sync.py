#!/usr/bin/env python3
"""Check source repo ↔ plugin cache sync for plugin work.

For Claude Code, greps the source repo for ``${CLAUDE_PLUGIN_ROOT}/`` references
in docs/manifests/scripts, resolves each path in both the source repo and the
cache, and reports files that diverge.

For Codex, checks the installed Codex plugin cache against Codex-visible source
surfaces: .codex-plugin/plugin.json, AGENTS.md, README.md, commands/*.md, and
skills/**/*.md/json/js/mjs/py/sh/ts.

Run from a plugin source repo (or with --source).

Exit codes:
    0 — all referenced files in sync (or cache not installed — nothing to check)
    1 — at least one referenced file diverges, or Codex has only stale versions installed
    2 — usage / filesystem error

Zero deps, Python 3.11+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REF_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_][A-Za-z0-9_./\-]*[A-Za-z0-9_])")
SEARCH_EXTS = {".md", ".json", ".py", ".sh", ".mjs", ".js", ".ts"}
IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".build-loop"}
CODEX_DEFAULT_MARKETPLACE = "ross-labs-local"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(source: Path, host: str) -> dict:
    manifest_dir = ".codex-plugin" if host == "codex" else ".claude-plugin"
    m = source / manifest_dir / "plugin.json"
    if not m.exists():
        print(f"error: no {manifest_dir}/plugin.json under {source}", file=sys.stderr)
        sys.exit(2)
    return json.loads(m.read_text())


def default_marketplace(source: Path) -> str:
    # Heuristic: walk up until we find a .claude-plugin/marketplace.json with this plugin listed
    for parent in [source.parent, source.parent.parent, source.parent.parent.parent]:
        mf = parent / ".claude-plugin" / "marketplace.json"
        if mf.exists():
            try:
                data = json.loads(mf.read_text())
                return data.get("name", "unknown-marketplace")
            except json.JSONDecodeError:
                pass
    return "rosslabs-ai-toolkit"


def find_references(source: Path) -> set[str]:
    refs: set[str] = set()
    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for name in files:
            p = Path(root) / name
            if p.suffix not in SEARCH_EXTS:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in REF_RE.finditer(text):
                refs.add(m.group(1))
    return refs


def find_codex_surfaces(source: Path) -> set[str]:
    refs: set[str] = set()
    for extra in (".codex-plugin/plugin.json", "AGENTS.md", "README.md"):
        if (source / extra).exists():
            refs.add(extra)

    for root_name in ("skills", "commands"):
        root_path = source / root_name
        if not root_path.exists():
            continue
        for root, dirs, files in os.walk(root_path):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
            for name in files:
                p = Path(root) / name
                if p.suffix in SEARCH_EXTS:
                    refs.add(str(p.relative_to(source)))
    return refs


def find_installed_versions(cache_root: Path, name: str) -> list[Path]:
    if not cache_root.exists():
        return []
    versions: list[Path] = []
    for marketplace in cache_root.iterdir():
        plugin_dir = marketplace / name
        if not plugin_dir.is_dir():
            continue
        versions.extend([p for p in plugin_dir.iterdir() if p.is_dir()])
    return sorted(versions, key=lambda p: p.name, reverse=True)


def default_cache(source: Path, host: str, name: str, version: str, marketplace: str | None) -> tuple[Path, list[Path]]:
    if host == "claude":
        chosen_marketplace = marketplace or default_marketplace(source)
        return Path.home() / ".claude" / "plugins" / "cache" / chosen_marketplace / name / version, []

    cache_root = Path.home() / ".codex" / "plugins" / "cache"
    if marketplace:
        return cache_root / marketplace / name / version, []

    exact_matches = [p for p in find_installed_versions(cache_root, name) if p.name == version]
    if exact_matches:
        return exact_matches[0], []

    stale = find_installed_versions(cache_root, name)
    return cache_root / CODEX_DEFAULT_MARKETPLACE / name / version, stale


def check_sync(source: Path, cache: Path, refs: set[str]) -> list[dict]:
    """Only flag problems where source has the file and cache doesn't match.

    Docs often reference illustrative paths (e.g. ``scripts/my-script.sh``) that
    don't exist in the real source tree — those are not sync problems, they're
    just examples. We ignore them. Cache-only files (leftovers from older
    versions) are also not actionable for the current sync check.
    """
    diffs: list[dict] = []
    for ref in sorted(refs):
        src = source / ref
        if not src.exists() or src.is_dir():
            continue  # example ref, non-file, or path that never existed in source
        dst = cache / ref
        if not dst.exists():
            diffs.append({"path": ref, "status": "missing_in_cache", "src": str(src), "dst": str(dst)})
            continue
        if dst.is_dir():
            continue
        if sha256(src) != sha256(dst):
            diffs.append({"path": ref, "status": "diverged", "src": str(src), "dst": str(dst)})
    return diffs


def suggest_fix(source: Path, cache: Path) -> str:
    return (
        f"  rsync -av --delete --exclude=.git --exclude=node_modules --exclude=__pycache__ "
        f"{source}/ {cache}/"
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Check plugin source ↔ cache sync for plugin work.")
    p.add_argument("--host", choices=["claude", "codex"], default="claude", help="Plugin host cache to inspect (default: claude)")
    p.add_argument("--source", default=".", help="Plugin source repo root (default: cwd)")
    p.add_argument("--cache", default=None, help="Plugin cache dir (default: resolve from plugin.json version + marketplace)")
    p.add_argument("--marketplace", default=None, help="Marketplace name (default: autodetect from parent marketplace.json)")
    p.add_argument("--json", action="store_true", help="Emit JSON report instead of human-readable")
    p.add_argument("--fail-on-missing-cache", action="store_true", help="Treat a missing cache dir as exit 1 (default: exit 0)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()
    manifest = load_manifest(source, args.host)
    name = manifest.get("name")
    version = manifest.get("version")
    if not name or not version:
        print("error: plugin.json missing name or version", file=sys.stderr)
        return 2

    if args.cache:
        cache = Path(args.cache).resolve()
        stale_versions: list[Path] = []
    else:
        cache, stale_versions = default_cache(source, args.host, name, version, args.marketplace)

    if not cache.exists():
        if args.host == "codex" and stale_versions:
            print(f"cache for {name}@{version} not installed at {cache}", file=sys.stderr)
            print("found other installed Codex version(s):", file=sys.stderr)
            for stale in stale_versions:
                print(f"  {stale}", file=sys.stderr)
            return 1
        msg = f"cache not installed at {cache} — skipping (use --fail-on-missing-cache to enforce)"
        print(msg, file=sys.stderr)
        return 1 if args.fail_on_missing_cache else 0

    if args.host == "codex":
        refs = find_codex_surfaces(source)
    else:
        refs = find_references(source)
        # Always also check the manifest + hooks even if not referenced via ${CLAUDE_PLUGIN_ROOT}
        for extra in (".claude-plugin/plugin.json", "hooks/hooks.json"):
            if (source / extra).exists():
                refs.add(extra)

    diffs = check_sync(source, cache, refs)

    if args.json:
        print(json.dumps({"host": args.host, "plugin": name, "version": version, "source": str(source), "cache": str(cache), "diffs": diffs}, indent=2))
    else:
        if not diffs:
            print(f"✅ {args.host} {name}@{version}: all {len(refs)} referenced files in sync")
            print(f"   source: {source}")
            print(f"   cache:  {cache}")
        else:
            print(f"❌ {args.host} {name}@{version}: {len(diffs)} file(s) out of sync between source and cache")
            print(f"   source: {source}")
            print(f"   cache:  {cache}")
            print()
            for d in diffs:
                status = d["status"]
                path = d["path"]
                if status == "missing_in_cache":
                    print(f"   [MISSING IN CACHE] {path}")
                elif status == "diverged":
                    print(f"   [DIVERGED]         {path}")
            print()
            print("to resync:")
            print(suggest_fix(source, cache))

    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
