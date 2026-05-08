#!/usr/bin/env python3
"""Detect whether the working directory IS the runtime executing build-loop.

Self-recursive = plugin developer dogfooding. Three checks must all pass:
  1. ``<workdir>/.claude-plugin/plugin.json`` exists with a ``name`` field.
  2. Some ``~/.claude/plugins/`` entry is a symlink resolving to ``<workdir>``
     (legacy ``plugins/<name>`` or ``plugins/cache/<marketplace>/<name>/*``).
  3. ``<workdir>/.git/`` exists.

Output JSON keys: self_recursive, plugin_name, runtime_symlink_path,
working_copy_branch (null on detached HEAD), working_copy_sha,
reason_if_false (not_a_plugin | no_runtime_link | not_a_git_repo |
symlink_check_failed). Pure stdlib. OSError during symlink walk degrades
to ``self_recursive: false`` + ``symlink_check_failed`` — never raises.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def _read_plugin_name(workdir: Path) -> str | None:
    try:
        data = json.loads((workdir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    name = data.get("name")
    return name if isinstance(name, str) and name else None


def _candidate_symlinks(plugins_root: Path, plugin_name: str):
    direct = plugins_root / plugin_name
    if direct.exists() or direct.is_symlink():
        yield direct
    cache = plugins_root / "cache"
    if not cache.is_dir():
        return
    try:
        marketplaces = list(cache.iterdir())
    except OSError:
        return
    for marketplace in marketplaces:
        plugin_dir = marketplace / plugin_name
        if not plugin_dir.is_dir():
            continue
        try:
            for version_entry in plugin_dir.iterdir():
                yield version_entry
        except OSError:
            continue


def _find_runtime_symlink(workdir: Path, plugin_name: str, plugins_root: Path) -> Path | None:
    workdir_resolved = workdir.resolve()
    for candidate in _candidate_symlinks(plugins_root, plugin_name):
        if not candidate.is_symlink():
            continue
        try:
            if candidate.resolve() == workdir_resolved:
                return candidate
        except OSError:
            continue
    return None


def _git_head(workdir: Path) -> tuple[str | None, str | None]:
    def _run(args):
        try:
            out = subprocess.run(["git", "-C", str(workdir), *args],
                                 capture_output=True, text=True, check=False, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None
    return (_run(["symbolic-ref", "--short", "-q", "HEAD"]) or None,
            _run(["rev-parse", "HEAD"]) or None)


def detect(workdir: Path, plugins_root: Path | None = None) -> dict:
    plugins_root = plugins_root or (Path.home() / ".claude" / "plugins")
    result = {"self_recursive": False, "plugin_name": None, "runtime_symlink_path": None,
              "working_copy_branch": None, "working_copy_sha": None, "reason_if_false": None}
    name = _read_plugin_name(workdir)
    if name is None:
        result["reason_if_false"] = "not_a_plugin"
        return result
    result["plugin_name"] = name
    try:
        link = _find_runtime_symlink(workdir, name, plugins_root)
    except OSError:
        result["reason_if_false"] = "symlink_check_failed"
        return result
    if link is None:
        result["reason_if_false"] = "no_runtime_link"
        return result
    result["runtime_symlink_path"] = str(link)
    if not (workdir / ".git").exists():
        result["reason_if_false"] = "not_a_git_repo"
        return result
    branch, sha = _git_head(workdir)
    result["working_copy_branch"] = branch
    result["working_copy_sha"] = sha
    result["self_recursive"] = True
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Detect self-recursive plugin build.")
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = p.parse_args(argv)
    result = detect(args.workdir.expanduser())
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"self_recursive: {'yes' if result['self_recursive'] else 'no'}")
        for k in ("plugin_name", "runtime_symlink_path",
                  "working_copy_branch", "working_copy_sha", "reason_if_false"):
            if result[k] is not None:
                print(f"{k}: {result[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
