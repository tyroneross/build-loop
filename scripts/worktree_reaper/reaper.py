# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Core reaper logic: detect leaked run worktrees and bundle-then-remove them.

Separate from ``__init__`` so that the package layout (folder-per-capability)
keeps the public surface (``__init__``) tiny while the implementation lives
in a dedicated module. Tests live in ``tests/test_reaper.py``.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import subprocess
import time
from pathlib import Path
from typing import Any


WORKTREE_ROOT_REL = Path(".build-loop") / "worktrees"
BUNDLE_DIR_REL = Path(".build-loop") / "bundles"
STATE_REL = Path(".build-loop") / "state.json"
DEFAULT_MIN_AGE_HOURS = 2.0


@dataclasses.dataclass
class ReapResult:
    """Aggregate of a reap pass. JSON-serialisable via ``dataclasses.asdict``."""

    bundled_and_removed: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    removed_orphan: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    skipped_active: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    skipped_too_young: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    skipped_not_run: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    errors: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Git helpers (kept tiny and explicit — never raise)
# ---------------------------------------------------------------------------

def _git(workdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git_available(workdir: Path) -> bool:
    r = _git(workdir, "rev-parse", "--git-dir")
    return r.returncode == 0


def _branch_exists(workdir: Path, branch: str) -> bool:
    r = _git(workdir, "rev-parse", "--verify", f"refs/heads/{branch}")
    return r.returncode == 0


def _bundle(workdir: Path, bundle_path: Path, branch: str) -> str | None:
    """Create a git bundle of the branch. Returns error string on failure."""
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    r = _git(workdir, "bundle", "create", str(bundle_path), branch)
    if r.returncode == 0:
        return None
    return (r.stderr or r.stdout).strip() or f"git bundle exited {r.returncode}"


def _remove_worktree(workdir: Path, path: Path) -> str | None:
    """Remove a worktree folder. Returns error string on failure."""
    r = _git(workdir, "worktree", "remove", "-f", "-f", str(path))
    if r.returncode == 0:
        return None
    return (r.stderr or r.stdout).strip() or f"git worktree remove exited {r.returncode}"


def _delete_branch(workdir: Path, branch: str) -> str | None:
    r = _git(workdir, "branch", "-D", branch)
    if r.returncode == 0:
        return None
    return (r.stderr or r.stdout).strip() or f"git branch -D exited {r.returncode}"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _load_state(workdir: Path) -> dict[str, Any]:
    state_path = workdir / STATE_REL
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _active_branches(state: dict[str, Any]) -> set[str]:
    """Branches we MUST NOT reap.

    Includes ``state.execution.run_worktree_branch`` plus any branch listed
    on a run in ``runs[].createdRefs[]`` with status == "open" or
    "kept_for_review".
    """
    active: set[str] = set()

    execution = state.get("execution") or {}
    if isinstance(execution, dict):
        b = execution.get("run_worktree_branch")
        if b:
            active.add(str(b))

    runs = state.get("runs") or []
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            for ref in run.get("createdRefs") or []:
                if not isinstance(ref, dict):
                    continue
                if ref.get("status") in ("open", "kept_for_review"):
                    b = ref.get("branch")
                    if b:
                        active.add(str(b))
    return active


def _registered_worktrees(workdir: Path) -> dict[str, str]:
    """Return ``{absolute_worktree_path: branch_short_name}`` from git.

    Parses ``git worktree list --porcelain`` so we know which folders git
    considers a real worktree (vs a stray directory under
    ``.build-loop/worktrees/``). Empty dict on any failure.
    """
    r = _git(workdir, "worktree", "list", "--porcelain")
    if r.returncode != 0:
        return {}
    mapping: dict[str, str] = {}
    current_path: str | None = None
    current_branch: str | None = None
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            if current_path:
                mapping[current_path] = current_branch or ""
            current_path = line[len("worktree "):].strip()
            current_branch = None
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            # Strip refs/heads/ prefix if present.
            if ref.startswith("refs/heads/"):
                ref = ref[len("refs/heads/"):]
            current_branch = ref
        elif line == "" and current_path:
            mapping[current_path] = current_branch or ""
            current_path = None
            current_branch = None
    if current_path:
        mapping[current_path] = current_branch or ""
    return mapping


def _branch_from_worktree_dir(
    workdir: Path,
    wt_dir: Path,
    registered: dict[str, str],
) -> str | None:
    """Return the branch this worktree folder is on, or None if it's an orphan.

    A *real* worktree appears in ``git worktree list --porcelain``. Anything
    that doesn't is a stray folder; we never call ``git symbolic-ref`` inside
    it because that resolves up to the parent repo's HEAD (which would
    misidentify orphans as being "on main").
    """
    resolved = str(wt_dir.resolve())
    if resolved in registered:
        branch = registered[resolved]
        return branch or None
    # Stray folder — no git linkage. Return None so the caller treats it as
    # an orphan (folder-only removal, no bundle).
    return None


# ---------------------------------------------------------------------------
# Core reaper
# ---------------------------------------------------------------------------

def reap_worktrees(
    workdir: Path | str,
    *,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
    dry_run: bool = False,
    now: float | None = None,
) -> ReapResult:
    """Scan ``.build-loop/worktrees/run-*`` and bundle-then-remove any leaks.

    Parameters
    ----------
    workdir
        Repo root (must be a git repo).
    min_age_hours
        Only reap worktrees whose folder mtime is older than this many hours.
        Default 2h — short enough to catch crashed runs from the same day,
        long enough that an active run mid-iteration is never touched.
    dry_run
        When True, classify everything but perform no destructive actions
        (no bundle, no remove, no branch delete).
    now
        Override the wall-clock (POSIX timestamp). Tests inject this to
        simulate aged folders without ``os.utime`` gymnastics.
    """
    result = ReapResult(dry_run=dry_run)
    workdir = Path(workdir)

    if not _git_available(workdir):
        result.errors.append({"reason": "git unavailable or not a repo", "workdir": str(workdir)})
        return result

    root = workdir / WORKTREE_ROOT_REL
    if not root.exists():
        return result  # nothing to do

    state = _load_state(workdir)
    active = _active_branches(state)
    registered = _registered_worktrees(workdir)

    cutoff = (now if now is not None else time.time()) - (min_age_hours * 3600.0)

    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if not entry.name.startswith("run-"):
            result.skipped_not_run.append({"path": str(entry)})
            continue

        try:
            mtime = entry.stat().st_mtime
        except OSError as exc:
            result.errors.append({"path": str(entry), "reason": f"stat failed: {exc}"})
            continue

        if mtime > cutoff:
            result.skipped_too_young.append({
                "path": str(entry),
                "age_hours": round((time.time() - mtime) / 3600.0, 3),
                "min_age_hours": min_age_hours,
            })
            continue

        branch = _branch_from_worktree_dir(workdir, entry, registered)
        if branch and branch in active:
            result.skipped_active.append({"path": str(entry), "branch": branch})
            continue

        # If we couldn't infer a branch, treat as an orphan folder.
        if branch is None:
            if dry_run:
                result.removed_orphan.append({"path": str(entry), "action": "would_remove_orphan"})
                continue
            # No backing branch — just blow the folder away. There is nothing
            # to bundle. We DO NOT use rm -rf because that would skip git's
            # worktree bookkeeping; `git worktree remove --force --force`
            # cleans both the folder and any stale entry in `worktrees/`.
            wt_err = _remove_worktree(workdir, entry)
            if wt_err and entry.exists():
                # Final fallback: shutil.rmtree to break the leak.
                import shutil
                try:
                    shutil.rmtree(entry)
                except OSError as exc:
                    result.errors.append({
                        "path": str(entry),
                        "reason": f"orphan remove failed: {wt_err}; rmtree: {exc}",
                    })
                    continue
            result.removed_orphan.append({"path": str(entry)})
            continue

        # Branch exists (or we believe it does): bundle then remove.
        if dry_run:
            result.bundled_and_removed.append({
                "path": str(entry),
                "branch": branch,
                "action": "would_bundle_and_remove",
            })
            continue

        # Bundle (skip silently if the branch doesn't actually exist in refs).
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_branch = branch.replace("/", "-")
        bundle_path = workdir / BUNDLE_DIR_REL / f"reaped-{safe_branch}-{ts}.bundle"
        bundle_err: str | None = None
        if _branch_exists(workdir, branch):
            bundle_err = _bundle(workdir, bundle_path, branch)
            if bundle_err:
                result.errors.append({
                    "path": str(entry),
                    "branch": branch,
                    "reason": f"bundle failed: {bundle_err}",
                })
                # Safety: do NOT remove the worktree if the bundle failed.
                continue
        # else: no backing branch ref — treat as orphan-with-name.

        wt_err = _remove_worktree(workdir, entry)
        if wt_err:
            result.errors.append({
                "path": str(entry),
                "branch": branch,
                "reason": f"worktree remove failed: {wt_err}",
            })
            continue

        # Delete the branch ref now that the work is bundled. Skip when no
        # ref existed in the first place (orphan-with-name).
        if _branch_exists(workdir, branch):
            br_err = _delete_branch(workdir, branch)
            if br_err:
                # Non-fatal: bundle was made, worktree gone; surface the err.
                result.errors.append({
                    "path": str(entry),
                    "branch": branch,
                    "reason": f"branch delete failed: {br_err}",
                })

        result.bundled_and_removed.append({
            "path": str(entry),
            "branch": branch,
            "bundle": str(bundle_path) if bundle_path.exists() else None,
        })

    return result
