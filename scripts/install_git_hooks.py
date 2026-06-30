#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""install_git_hooks.py — install / uninstall the build-loop git hooks.

Currently installs:

- ``pre-push`` — the path-agnostic push choke point (see ``hooks/git/pre-push``).
  It runs TWO composed stages: the deploy-HOLD gate (``push_hold``) AND the
  deterministic pre-push TEST gate (``prepush_test_gate`` — added per the
  2026-06-29 RCA so a red commit cannot reach origin/main).  Both stages ship in
  the single hook body copied verbatim below, so installing the hook installs the
  test gate too — no separate wiring, and ``prepush_test_gate`` resolves off the
  ``<repo>/scripts`` path the hook already adds to ``sys.path`` at runtime.

The shell hook chain at ``hooks/pre-commit`` is installed separately (it's
managed by ``scripts/install_hooks.sh`` and the plugin's session-start
guardian).  This installer is scoped to hooks that need a Python entry point
under ``.git/hooks/`` AND that fire on a git operation other than commit.

Design
------
Idempotent.  The source hook at ``hooks/git/<name>`` is wrapped in fence
markers when an existing hook is present:

::

    # --- BEGIN build-loop pre-push gate ---
    <body>
    # --- END build-loop pre-push gate ---

so that a user's pre-existing hook is preserved and re-runs cleanly.  When
``.git/hooks/<name>`` is empty or absent, we write a fresh file with the body
+ fence markers + a tiny shebang/dispatch wrapper.

For Python hooks we cannot trivially append-into-shell, so the installed file
IS the Python hook from ``hooks/git/<name>``, no fence (a Python hook can't
host another shell hook inline).  If the user already has a non-build-loop
``pre-push``, we refuse to overwrite unless ``--force`` is given.

CLI
---

::

    install_git_hooks.py --install   [--repo PATH] [--force] [--json]
    install_git_hooks.py --uninstall [--repo PATH] [--json]
    install_git_hooks.py --status    [--repo PATH] [--json]

Exit codes
----------
- 0 on success / hook already in desired state.
- 1 on refused overwrite (user has a foreign hook; ``--force`` to override).
- 2 on argparse error.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HOOK_MARKER = "# build-loop:pre-push-hold-gate"
HOOKS_RELPATH = ("hooks", "git")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _resolve_repo(arg_repo: str | None) -> Path:
    if arg_repo:
        return Path(arg_repo).resolve()
    # Default: walk up from this script's location until we find .git.
    here = Path(__file__).resolve().parent
    for parent in [here] + list(here.parents):
        if (parent / ".git").exists() or (parent / ".git").is_file():
            return parent
    return Path.cwd().resolve()


def _git_hooks_dir(repo: Path) -> Path:
    """Resolve the effective hooks dir (handles worktrees + core.hooksPath).

    - In a normal repo: ``<repo>/.git/hooks``
    - In a worktree: ``<repo>/.git`` is a file pointing to the main repo's
      ``.git/worktrees/<name>``; we still install into ``<repo>/.git/hooks``
      because that's the path git resolves for the active worktree by
      default.  ``core.hooksPath`` overrides; we honour that when set.
    """
    # Check core.hooksPath via env or git config — best-effort.
    env_override = os.environ.get("GIT_HOOKS_DIR")
    if env_override:
        return Path(env_override)
    dot_git = repo / ".git"
    if dot_git.is_file():
        # Worktree: read "gitdir: <path>" and append /hooks
        try:
            line = dot_git.read_text().strip()
            if line.startswith("gitdir:"):
                gd = Path(line.split(":", 1)[1].strip())
                if not gd.is_absolute():
                    gd = (repo / gd).resolve()
                return gd / "hooks"
        except OSError:
            pass
    return dot_git / "hooks"


def _source_hook(repo: Path, name: str) -> Path:
    return repo / Path(*HOOKS_RELPATH) / name


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------


def _is_buildloop_hook(path: Path) -> bool:
    """Return True iff the installed hook is ours (contains the marker)."""
    if not path.exists():
        return False
    try:
        head = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return HOOK_MARKER in head


def _install_hook(repo: Path, name: str, *, force: bool) -> dict[str, Any]:
    src = _source_hook(repo, name)
    if not src.exists():
        return {
            "name": name,
            "installed": False,
            "skipped": True,
            "reason": f"source hook not found at {src}",
        }
    hooks_dir = _git_hooks_dir(repo)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dst = hooks_dir / name

    body = src.read_text(encoding="utf-8")
    # Ensure the marker is in the file body (helps `_is_buildloop_hook` work
    # against the committed source unchanged).  We embed it as a comment line
    # AFTER the shebang so it survives editing.
    if HOOK_MARKER not in body:
        lines = body.splitlines(keepends=True)
        if lines and lines[0].startswith("#!"):
            lines.insert(1, f"{HOOK_MARKER}\n")
        else:
            lines.insert(0, f"{HOOK_MARKER}\n")
        body = "".join(lines)

    if dst.exists() and not _is_buildloop_hook(dst):
        if not force:
            return {
                "name": name,
                "installed": False,
                "skipped": True,
                "reason": (
                    f"refusing to overwrite foreign {name} at {dst} — "
                    "rerun with --force to replace"
                ),
                "path": str(dst),
            }
        # Backup the foreign hook before overwriting.
        backup = dst.with_suffix(dst.suffix + ".pre-buildloop.bak")
        if not backup.exists():
            shutil.copy2(dst, backup)

    dst.write_text(body, encoding="utf-8")
    # chmod +x — preserve existing read/write bits if the umask was tight.
    current = dst.stat().st_mode
    dst.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {
        "name": name,
        "installed": True,
        "skipped": False,
        "path": str(dst),
    }


def _uninstall_hook(repo: Path, name: str) -> dict[str, Any]:
    hooks_dir = _git_hooks_dir(repo)
    dst = hooks_dir / name
    if not dst.exists():
        return {"name": name, "removed": False, "reason": "not installed"}
    if not _is_buildloop_hook(dst):
        return {
            "name": name,
            "removed": False,
            "reason": f"foreign hook at {dst} — not removing (no marker)",
        }
    # If a backup exists from a previous --force install, restore it.
    backup = dst.with_suffix(dst.suffix + ".pre-buildloop.bak")
    try:
        dst.unlink()
    except OSError as exc:
        return {"name": name, "removed": False, "reason": f"unlink failed: {exc}"}
    restored = False
    if backup.exists():
        try:
            shutil.move(str(backup), str(dst))
            restored = True
        except OSError:
            pass
    return {"name": name, "removed": True, "restored_backup": restored, "path": str(dst)}


def _status_hook(repo: Path, name: str) -> dict[str, Any]:
    hooks_dir = _git_hooks_dir(repo)
    dst = hooks_dir / name
    if not dst.exists():
        return {"name": name, "installed": False, "path": str(dst)}
    return {
        "name": name,
        "installed": _is_buildloop_hook(dst),
        "path": str(dst),
        "foreign": not _is_buildloop_hook(dst),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_HOOKS_MANAGED = ("pre-push",)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install build-loop git hooks.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--install", action="store_true")
    mode.add_argument("--uninstall", action="store_true")
    mode.add_argument("--status", action="store_true")
    parser.add_argument("--repo", type=str, default=None,
                        help="Repo root (default: detect from this script's location).")
    parser.add_argument("--force", action="store_true",
                        help="With --install: overwrite a foreign hook (backed up).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo = _resolve_repo(args.repo)
    if not (repo / ".git").exists():
        msg = f"{repo} is not a git repo (no .git/)"
        if args.json:
            sys.stdout.write(json.dumps({"error": msg}) + "\n")
        else:
            sys.stderr.write(msg + "\n")
        return 1

    results: list[dict[str, Any]] = []
    if args.install:
        for name in _HOOKS_MANAGED:
            results.append(_install_hook(repo, name, force=args.force))
    elif args.uninstall:
        for name in _HOOKS_MANAGED:
            results.append(_uninstall_hook(repo, name))
    elif args.status:
        for name in _HOOKS_MANAGED:
            results.append(_status_hook(repo, name))

    out = {"repo": str(repo), "results": results}
    if args.json:
        sys.stdout.write(json.dumps(out, indent=2, sort_keys=True) + "\n")
    else:
        for r in results:
            sys.stdout.write(json.dumps(r, sort_keys=True) + "\n")

    # Exit non-zero if any install was refused (without --force).
    if args.install and any(r.get("skipped") and "refusing" in (r.get("reason") or "") for r in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
