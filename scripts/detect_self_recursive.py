#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Detect whether the working directory IS the runtime executing build-loop.

Self-recursive = plugin developer dogfooding. The detector is the gate that
arms per-commit mode and the self-modification safety machinery; if it
returns false on a real dogfooding session, the machinery stays dormant.

Detection precedence (first signal wins):
  1. ``--runtime-root <path>`` arg → ``self_recursive = (realpath(runtime_root) == realpath(workdir))``.
  2. ``CLAUDE_PLUGIN_ROOT`` env var (set by Claude Code for the loaded plugin) → same check.
  3. ``__file__`` self-location: ``Path(__file__).resolve().parents[1]`` gives the plugin root
     of the *running script copy* (the script lives at ``<plugin_root>/scripts/<name>.py``).
     If that resolves to ``workdir``, this is ground truth — the interpreter actually loaded
     this exact copy, independent of any env var.  Env vars don't propagate to Bash-tool
     subprocesses launched by Claude Code; this tier is the fix for that gap.
     On mismatch, falls through to the symlink walk (``__file__`` is heuristic, not an
     operator assertion).
  4. Legacy fallback: walk ``~/.claude/plugins/`` for a symlink resolving to ``<workdir>``
     (covers ad-hoc dev symlinks under the legacy direct or per-version cache layout).

Regardless of method, the workdir must:
  - have ``<workdir>/.claude-plugin/plugin.json`` with a ``name`` field; AND
  - have ``<workdir>/.git/`` present.

Output JSON keys (stable):
  self_recursive, plugin_name, runtime_symlink_path, working_copy_branch
  (null on detached HEAD), working_copy_sha, reason_if_false, detection_method.

``reason_if_false`` taxonomy (unchanged): not_a_plugin | no_runtime_link |
not_a_git_repo | symlink_check_failed.

``detection_method`` values: runtime_root_arg | plugin_root_env |
self_location | cache_symlink | none.

Pure stdlib. Never raises — OSError during any path/symlink op degrades to
``self_recursive: false`` + ``symlink_check_failed``.
"""
from __future__ import annotations

import argparse
import json
import os
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


def _runtime_root_matches(runtime_root: Path, workdir_resolved: Path) -> bool:
    try:
        return runtime_root.expanduser().resolve() == workdir_resolved
    except OSError:
        return False


def detect(workdir: Path, plugins_root: Path | None = None,
           runtime_root: Path | None = None,
           env: dict | None = None,
           self_path: Path | None = None) -> dict:
    """Detect self-recursion.

    Precedence: runtime_root arg > CLAUDE_PLUGIN_ROOT env > __file__ self-location >
    symlink walk.

    ``self_path`` defaults to ``Path(__file__)`` and is injectable for testing so tests
    can drive the self-location tier without monkeypatching globals.
    """
    plugins_root = plugins_root or (Path.home() / ".claude" / "plugins")
    env = env if env is not None else os.environ
    result = {"self_recursive": False, "plugin_name": None, "runtime_symlink_path": None,
              "working_copy_branch": None, "working_copy_sha": None,
              "reason_if_false": None, "detection_method": "none"}
    name = _read_plugin_name(workdir)
    if name is None:
        result["reason_if_false"] = "not_a_plugin"
        return result
    result["plugin_name"] = name

    try:
        workdir_resolved = workdir.resolve()
    except OSError:
        result["reason_if_false"] = "symlink_check_failed"
        return result

    matched = False
    # Empty-string args/env vars (common when ``$CLAUDE_PLUGIN_ROOT`` is unset in
    # the calling shell and the caller passes it unquoted-or-quoted-but-unset)
    # are treated as "not provided" — otherwise ``Path("").resolve()`` would
    # equal ``Path.cwd().resolve()`` and produce a false positive when CWD
    # happens to be the workdir.
    # ``Path("")`` stringifies as ``"."`` (which then resolves to CWD), so we
    # must treat both the literal empty string AND ``Path("")`` as "not
    # provided". The shell case that motivates this: ``--runtime-root
    # "$CLAUDE_PLUGIN_ROOT"`` when the env var is unset expands to an empty
    # string, which would otherwise false-positive whenever CWD is the workdir.
    # A user who genuinely wants "current directory" should pass an absolute
    # path or use ``$PWD``.
    arg_provided = runtime_root is not None and str(runtime_root) != "" \
        and not (isinstance(runtime_root, Path) and runtime_root == Path(""))

    # 1. --runtime-root arg (explicit override; empty string ignored — see guard above)
    if arg_provided:
        if _runtime_root_matches(runtime_root, workdir_resolved):
            result["detection_method"] = "runtime_root_arg"
            result["runtime_symlink_path"] = str(runtime_root)
            matched = True
        else:
            # Arg present but mismatch → workdir is NOT the runtime. Don't fall through
            # to symlink scanning: the explicit signal already answered the question.
            result["reason_if_false"] = "no_runtime_link"
            return result

    # 2. CLAUDE_PLUGIN_ROOT env (only when arg wasn't provided)
    if not matched:
        env_root = env.get("CLAUDE_PLUGIN_ROOT")
        if env_root:  # already excludes empty string
            if _runtime_root_matches(Path(env_root), workdir_resolved):
                result["detection_method"] = "plugin_root_env"
                result["runtime_symlink_path"] = env_root
                matched = True
            else:
                result["reason_if_false"] = "no_runtime_link"
                return result

    # 3. __file__ self-location (injectable via self_path for tests)
    # Rationale: CLAUDE_PLUGIN_ROOT is NOT propagated to Bash-tool subprocesses that
    # Claude Code spawns.  Path(__file__) is always the path the interpreter loaded,
    # so parents[1] of the script (scripts/<name>.py) gives the plugin root regardless
    # of env propagation.  On mismatch we fall through — this is a heuristic, not an
    # operator assertion, so mismatches don't short-circuit the symlink walk.
    if not matched:
        resolved_self_path = self_path if self_path is not None else Path(__file__)
        try:
            self_plugin_root = resolved_self_path.resolve().parents[1]
            if self_plugin_root == workdir_resolved:
                result["detection_method"] = "self_location"
                result["runtime_symlink_path"] = str(self_plugin_root)
                matched = True
            # Mismatch → fall through to symlink walk (no early return)
        except (OSError, IndexError):
            pass  # Degrade gracefully; continue to symlink walk

    # 4. Legacy symlink walk under ~/.claude/plugins/
    if not matched:
        try:
            link = _find_runtime_symlink(workdir, name, plugins_root)
        except OSError:
            result["reason_if_false"] = "symlink_check_failed"
            return result
        if link is None:
            result["reason_if_false"] = "no_runtime_link"
            return result
        result["detection_method"] = "cache_symlink"
        result["runtime_symlink_path"] = str(link)

    if not (workdir / ".git").exists():
        result["reason_if_false"] = "not_a_git_repo"
        result["self_recursive"] = False
        return result
    branch, sha = _git_head(workdir)
    result["working_copy_branch"] = branch
    result["working_copy_sha"] = sha
    result["self_recursive"] = True
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Detect self-recursive plugin build.")
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--runtime-root", type=str, default=None,
                   help="Path to the currently-loaded plugin root (typically $CLAUDE_PLUGIN_ROOT). "
                        "When supplied, takes precedence over the env var and the symlink walk. "
                        "An empty string (common when $CLAUDE_PLUGIN_ROOT is unset) is ignored.")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = p.parse_args(argv)
    rr = Path(args.runtime_root) if args.runtime_root else None
    result = detect(args.workdir.expanduser(), runtime_root=rr)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"self_recursive: {'yes' if result['self_recursive'] else 'no'}")
        for k in ("plugin_name", "runtime_symlink_path",
                  "working_copy_branch", "working_copy_sha",
                  "reason_if_false", "detection_method"):
            if result[k] is not None:
                print(f"{k}: {result[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
