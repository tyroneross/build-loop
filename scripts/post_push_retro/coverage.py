# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Multi-branch / multi-worktree coverage against a per-repo checkpoint.

The retro must cover "work done since the prior retrospective across all
agents/branches/worktrees that advanced" — not just the current push's delta.

Per-REPO, not per-checkout: the checkpoint + batons live under
``git rev-parse --git-common-dir`` (the ``.git`` shared by every worktree of the
repo), so N worktrees of one repo share ONE checkpoint and are counted as ONE
repo. Using the per-worktree ``--show-toplevel`` here would mis-count sibling
worktrees as separate repos and spuriously escalate routine work to the
expensive tier (plan-critic WARN, adopted).

Checkpoint writes are atomic (tmp + ``os.replace``) and read-modify-write UNION
(advance each branch to its newest tip, never regress) so two concurrent pushes
cannot double-cover or lose work.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _git(repo: Path, *args: str) -> str:
    """Run a git command in ``repo``; return stripped stdout ('' on any error)."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), *args],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
        return out.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def git_common_dir(repo: Path) -> Path:
    """Absolute ``--git-common-dir`` — the ``.git`` shared by all worktrees of the
    repo. This is the per-REPO anchor. Falls back to ``<repo>/.git``."""
    raw = _git(repo, "rev-parse", "--git-common-dir")
    if not raw:
        return (repo / ".git").resolve()
    p = Path(raw)
    if not p.is_absolute():
        p = (repo / p).resolve()
    return p.resolve()


def retro_state_dir(repo: Path) -> Path:
    """Per-repo shared state dir for checkpoints, batons, and failure markers."""
    d = git_common_dir(repo) / "build-loop-retro"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def checkpoint_path(repo: Path) -> Path:
    return retro_state_dir(repo) / "checkpoint.json"


def read_checkpoint(repo: Path) -> dict[str, Any]:
    p = checkpoint_path(repo)
    if not p.exists():
        return {"branches": {}, "last_retro_at": None, "last_range": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("branches", {})
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"branches": {}, "last_retro_at": None, "last_range": None}


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".ckpt-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def advanced_refs(repo: Path) -> list[dict[str, str]]:
    """Every local branch and every worktree HEAD, with its current tip sha.

    Returns dicts: ``{"name", "kind" ('branch'|'worktree'), "tip", "path"}``.
    Deduped by (name, tip). Worktrees whose HEAD is detached contribute their
    HEAD sha under a ``worktree:<path>`` pseudo-name."""
    refs: dict[tuple[str, str], dict[str, str]] = {}

    # Local branches (covers branches with no worktree checked out).
    for line in _git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)",
                     "refs/heads").splitlines():
        parts = line.split()
        if len(parts) == 2:
            name, tip = parts
            refs[(name, tip)] = {"name": name, "kind": "branch", "tip": tip, "path": ""}

    # Worktrees (may include the branch tips above; dedup handles overlap).
    cur: dict[str, str] = {}
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines() + [""]:
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):].strip()}
        elif line.startswith("HEAD "):
            cur["tip"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):].strip().replace("refs/heads/", "")
        elif line == "" and cur.get("tip"):
            name = cur.get("branch") or f"worktree:{cur.get('path','')}"
            tip = cur["tip"]
            refs[(name, tip)] = {
                "name": name, "kind": "worktree", "tip": tip, "path": cur.get("path", ""),
            }
            cur = {}

    return list(refs.values())


def compute_coverage(
    repo: Path,
    checkpoint: dict[str, Any],
    *,
    pushed_range: str | None = None,
    cap: int = 50,
) -> dict[str, Any]:
    """Union of commit deltas across every advanced branch/worktree since the
    checkpoint. First run (no checkpoint branches): bound to ``pushed_range`` if
    given, else the last ``cap`` commits on HEAD (never all of history)."""
    ckpt_branches: dict[str, str] = checkpoint.get("branches", {}) or {}
    refs = advanced_refs(repo)

    commits: dict[str, None] = {}  # sha -> None, insertion-ordered dedup
    branches_advanced: list[str] = []
    worktrees_advanced: list[str] = []
    repos_seen: set[str] = {str(git_common_dir(repo))}

    def _add_range(rng: str) -> list[str]:
        out = _git(repo, "rev-list", rng)
        shas = [s for s in out.splitlines() if s]
        for s in shas:
            commits.setdefault(s, None)
        return shas

    have_checkpoint = bool(ckpt_branches)
    for ref in refs:
        name, tip = ref["name"], ref["tip"]
        last = ckpt_branches.get(name)
        if last == tip:
            continue  # not advanced
        if last:
            got = _add_range(f"{last}..{tip}")
        elif have_checkpoint:
            # Known repo, new/unseen branch: cover its unique commits vs all known tips.
            not_args = [f"^{s}" for s in set(ckpt_branches.values())]
            out = _git(repo, "rev-list", tip, *not_args)
            got = [s for s in out.splitlines() if s]
            for s in got:
                commits.setdefault(s, None)
        else:
            got = []  # first-run handled below (bounded), not per-branch
        if got:
            if ref["kind"] == "worktree" and ref.get("path"):
                worktrees_advanced.append(ref["path"])
            else:
                branches_advanced.append(name)
        if ref.get("path") and Path(ref["path"]).exists():
            # Route through git_common_dir() so identity is normalized identically
            # to the initial anchor (macOS /var -> /private/var, relative -> abs).
            # Worktrees of the SAME repo share one common dir => one repo.
            repos_seen.add(str(git_common_dir(Path(ref["path"]))))

    # First run: bounded coverage so we don't retro all of history.
    if not have_checkpoint and not commits:
        if pushed_range:
            _add_range(pushed_range)
        else:
            _add_range(f"HEAD~{cap}..HEAD" if _git(repo, "rev-parse", f"HEAD~{cap}") else "HEAD")

    commit_list = list(commits.keys())
    files = _files_for_commits(repo, commit_list)
    if commit_list:
        newest, oldest = commit_list[0], commit_list[-1]
        range_label = f"{oldest[:9]}..{newest[:9]}"
    else:
        range_label = ""

    return {
        "commits": commit_list,
        "commit_count": len(commit_list),
        "branches_advanced": sorted(set(branches_advanced)),
        "worktrees_advanced": sorted(set(worktrees_advanced)),
        "files_changed": files,
        "repos_touched": len(repos_seen),
        "range_label": range_label,
        "refs": refs,
    }


def _files_for_commits(repo: Path, commits: list[str]) -> list[str]:
    if not commits:
        return []
    files: dict[str, None] = {}
    # Files touched per commit; robust for merges via --name-only. Bounded to 200
    # commits — a union that large is already classified "substantial".
    for sha in commits[:200]:
        names = _git(repo, "show", "--name-only", "--pretty=format:", sha)
        for line in names.splitlines():
            line = line.strip()
            if line:
                files.setdefault(line, None)
    return list(files.keys())


def _is_ancestor(repo: Path, candidate: str, existing: str) -> bool:
    """True when ``candidate`` is an ancestor of (i.e. older than / already
    contained in) ``existing`` — so advancing to ``candidate`` would REGRESS."""
    if not candidate or not existing or candidate == existing:
        return False
    try:
        return subprocess.call(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", candidate, existing],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15) == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def update_checkpoint_from_coverage(repo: Path, coverage: dict[str, Any]) -> dict[str, Any]:
    """Read-modify-write UNION: advance each ref's last-retro'd sha to the tip we
    just covered, but NEVER regress a tip a concurrent push already advanced past
    (ancestry-guarded — a candidate that is an ancestor of the recorded sha is
    skipped). Atomic. Call ONLY after a successful retro so a failed run does not
    mark work as covered."""
    ckpt = read_checkpoint(repo)
    branches = dict(ckpt.get("branches", {}) or {})
    for ref in coverage.get("refs", []):
        existing = branches.get(ref["name"])
        if existing and _is_ancestor(repo, ref["tip"], existing):
            continue  # a peer push already advanced this branch further — don't regress
        branches[ref["name"]] = ref["tip"]
    ckpt["branches"] = branches
    ckpt["last_retro_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ckpt["last_range"] = coverage.get("range_label") or ckpt.get("last_range")
    _atomic_write(checkpoint_path(repo), ckpt)
    return ckpt
