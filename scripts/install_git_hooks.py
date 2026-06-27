#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""install_git_hooks.py — install / uninstall the build-loop git hooks.

Installs:

- ``pre-push`` — the path-agnostic push-HOLD gate (whole-file Python hook from
  ``hooks/git/pre-push``).
- a marker-fenced ``pre-commit`` **segment** that auto-regenerates the
  architecture diagram when a diagram-source file is staged (delegates to
  ``scripts/architecture_diagram/regen_hook.py``). It is a chained SEGMENT, not
  a whole-file hook: the rally-point installer
  (``scripts/rally_point/install_git_hook.py``) owns its own private-slug-guard
  segment in the same ``.git/hooks/pre-commit``. Each installer replaces only
  its OWN fenced segment and preserves the rest verbatim, so the two coexist.

Both are idempotent and auto-run on session start via
``hooks/session-start-git-hooks.sh``.

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
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HOOK_MARKER = "# build-loop:pre-push-hold-gate"
HOOKS_RELPATH = ("hooks", "git")

# ---------------------------------------------------------------------------
# pre-commit arch-regen segment
# ---------------------------------------------------------------------------
# A marker-fenced segment chained into ``.git/hooks/pre-commit`` (NOT a
# whole-file hook — the rally-point installer owns its own private-slug-guard
# segment in the same file; each installer replaces only its OWN fenced segment
# and preserves the rest verbatim, so they coexist). When a commit stages a
# diagram-source file, this runs scripts/architecture_diagram/regen_hook.py to
# regenerate + stage the diagram, so the freshness gate can't go red on a
# forgotten manual ``generate.py``. The segment is fail-open: regen_hook.py
# always exits 0, and the `|| true` is belt-and-suspenders so a hook crash can
# never block a commit (the CI gate is the fail-closed backstop).
ARCH_REGEN_MARKER = "# --- BEGIN build-loop arch-regen pre-commit ---"
ARCH_REGEN_MARKER_END = "# --- END build-loop arch-regen pre-commit ---"
ARCH_REGEN_SEGMENT = f'''{ARCH_REGEN_MARKER}
BL_TOPLEVEL="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -n "$BL_TOPLEVEL" ] && [ -f "$BL_TOPLEVEL/scripts/architecture_diagram/regen_hook.py" ]; then
  python3 "$BL_TOPLEVEL/scripts/architecture_diagram/regen_hook.py" --repo "$BL_TOPLEVEL" || true
fi
{ARCH_REGEN_MARKER_END}
'''


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
    """Resolve the effective hooks dir, deferring to git itself.

    ``git rev-parse --git-path hooks`` is authoritative: it returns the COMMON
    ``.git/hooks`` for a worktree (git does NOT use a per-worktree hooks dir by
    default) and honours ``core.hooksPath``. Hand-parsing the ``.git`` file got
    this wrong for worktrees (it pointed at ``.git/worktrees/<name>/hooks``,
    which git never executes), so a hook installed from inside a worktree was a
    silent no-op. ``GIT_HOOKS_DIR`` still wins for explicit test overrides.
    """
    env_override = os.environ.get("GIT_HOOKS_DIR")
    if env_override:
        return Path(env_override)
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
            text=True, stderr=subprocess.DEVNULL).strip()
        if out:
            p = Path(out)
            return p if p.is_absolute() else (repo / p).resolve()
    except Exception:
        pass
    # Fallback (git unavailable): the common hooks dir for a normal repo.
    return repo / ".git" / "hooks"


def _source_hook(repo: Path, name: str) -> Path:
    return repo / Path(*HOOKS_RELPATH) / name


# ---------------------------------------------------------------------------
# pre-commit segment chaining (coexists with the rally-point segment)
# ---------------------------------------------------------------------------

_SEGMENT_RE = re.compile(
    re.escape(ARCH_REGEN_MARKER) + r".*?" + re.escape(ARCH_REGEN_MARKER_END) + r"\n?",
    re.DOTALL,
)


def _install_arch_regen_segment(repo: Path) -> dict[str, Any]:
    """Chain the arch-regen segment into ``.git/hooks/pre-commit`` idempotently.

    - File absent/empty  -> write shebang + segment + ``exit 0``.
    - Segment present     -> replace it in place (re-run is a no-op-equivalent).
    - Other content       -> append the segment before any trailing ``exit 0``,
      preserving the rest verbatim (the rally private-slug segment lives here).
    """
    hooks_dir = _git_hooks_dir(repo)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dst = hooks_dir / "pre-commit"

    existing = dst.read_text(encoding="utf-8") if dst.exists() else ""

    if not existing.strip():
        body = "#!/bin/sh\n" + ARCH_REGEN_SEGMENT + "exit 0\n"
        already = False
    elif ARCH_REGEN_MARKER in existing:
        body = _SEGMENT_RE.sub(ARCH_REGEN_SEGMENT, existing, count=1)
        already = body == existing
    else:
        # Insert before a trailing bare `exit 0` if present, else append.
        lines = existing.splitlines(keepends=True)
        insert_at = len(lines)
        for idx in range(len(lines) - 1, -1, -1):
            if lines[idx].strip() == "exit 0":
                insert_at = idx
                break
        seg = ARCH_REGEN_SEGMENT if existing.endswith("\n") else "\n" + ARCH_REGEN_SEGMENT
        lines.insert(insert_at, seg)
        body = "".join(lines)
        already = False

    if body != existing:
        dst.write_text(body, encoding="utf-8")
    current = dst.stat().st_mode
    dst.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {"name": "pre-commit:arch-regen", "installed": True, "skipped": False,
            "already_present": already, "path": str(dst)}


def _uninstall_arch_regen_segment(repo: Path) -> dict[str, Any]:
    dst = _git_hooks_dir(repo) / "pre-commit"
    if not dst.exists():
        return {"name": "pre-commit:arch-regen", "removed": False, "reason": "not installed"}
    existing = dst.read_text(encoding="utf-8")
    if ARCH_REGEN_MARKER not in existing:
        return {"name": "pre-commit:arch-regen", "removed": False, "reason": "segment not present"}
    body = _SEGMENT_RE.sub("", existing, count=1)
    dst.write_text(body, encoding="utf-8")
    return {"name": "pre-commit:arch-regen", "removed": True, "path": str(dst)}


def _status_arch_regen_segment(repo: Path) -> dict[str, Any]:
    dst = _git_hooks_dir(repo) / "pre-commit"
    present = dst.exists() and ARCH_REGEN_MARKER in dst.read_text(encoding="utf-8")
    return {"name": "pre-commit:arch-regen", "installed": present, "path": str(dst)}


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
        results.append(_install_arch_regen_segment(repo))
    elif args.uninstall:
        for name in _HOOKS_MANAGED:
            results.append(_uninstall_hook(repo, name))
        results.append(_uninstall_arch_regen_segment(repo))
    elif args.status:
        for name in _HOOKS_MANAGED:
            results.append(_status_hook(repo, name))
        results.append(_status_arch_regen_segment(repo))

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
