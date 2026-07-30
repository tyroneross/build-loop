# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Report leaked run worktrees and explicitly delegate safe finalization.

This module never mutates Git directly. It discovers only attributable
.build-loop/worktrees/run-* candidates. Default behavior is report-only.
An explicit caller must provide both act=True and owner_released=True; even then,
scripts.collapse_run remains the sole destructive authority.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    from scripts import collapse_run
except ImportError:  # direct __main__.py path inserts scripts/ on sys.path
    import collapse_run  # type: ignore


WORKTREE_ROOT_REL = Path(".build-loop") / "worktrees"
STATE_REL = Path(".build-loop") / "state.json"
DEFAULT_MIN_AGE_HOURS = 2.0


@dataclasses.dataclass
class ReapResult:
    """Aggregate report; legacy result keys remain for API compatibility."""

    candidates: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    bundled_and_removed: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    removed_orphan: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    skipped_active: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    skipped_too_young: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    skipped_not_run: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    skipped_unattributed: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    skipped_unmerged: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    errors: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    dry_run: bool = True
    act: bool = False
    owner_released: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _git(workdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git_available(workdir: Path) -> bool:
    return _git(workdir, "rev-parse", "--git-dir").returncode == 0


def _load_state(workdir: Path) -> dict[str, Any] | None:
    path = workdir / STATE_REL
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return state if isinstance(state, dict) else None


def _identity(row: dict[str, Any] | None) -> str | None:
    if not isinstance(row, dict):
        return None
    for key in ("build_loop_id", "run_id", "id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _identities(row: dict[str, Any] | None) -> set[str]:
    if not isinstance(row, dict):
        return set()
    return {
        value
        for key in ("build_loop_id", "run_id", "id")
        if isinstance((value := row.get(key)), str) and value
    }


def _execution_ref(entry: dict[str, Any]) -> tuple[str | None, str | None]:
    branch = (
        entry.get("run_worktree_branch")
        or entry.get("branch")
        or entry.get("branch_name")
    )
    path = (
        entry.get("run_worktree_path")
        or entry.get("worktree")
        or entry.get("worktree_path")
    )
    return (
        str(branch) if branch else None,
        str(Path(str(path)).resolve()) if path else None,
    )


def _active_branches(state: dict[str, Any]) -> set[str]:
    execution = state.get("execution")
    if not isinstance(execution, dict):
        return set()
    branch, _ = _execution_ref(execution)
    return {branch} if branch else set()


def _attributions(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Map branches to durable run/path owners from runs plus execution history."""
    runs = [row for row in state.get("runs") or [] if isinstance(row, dict)]
    execution_rows: list[dict[str, Any]] = []
    current = state.get("execution")
    if isinstance(current, dict) and current:
        execution_rows.append(current)
    execution_rows.extend(
        row
        for row in state.get("historicalExecutions") or []
        if isinstance(row, dict)
    )

    out: dict[str, list[dict[str, Any]]] = {}

    def add(branch: str | None, path: str | None, run_id: str) -> None:
        if not branch:
            return
        row = {"run_id": run_id, "branch": branch, "path": path}
        bucket = out.setdefault(branch, [])
        if row not in bucket:
            bucket.append(row)

    for run in runs:
        run_id = _identity(run)
        if not run_id:
            continue
        for ref in run.get("createdRefs") or []:
            if not isinstance(ref, dict):
                continue
            path = ref.get("path") or ref.get("worktree")
            add(
                str(ref.get("branch")) if ref.get("branch") else None,
                str(Path(str(path)).resolve()) if path else None,
                run_id,
            )
        for execution in execution_rows:
            if _identities(run) & _identities(execution):
                branch, path = _execution_ref(execution)
                add(branch, path, run_id)
    return out


def _registered_worktrees(workdir: Path) -> tuple[dict[str, str], str | None]:
    r = _git(workdir, "worktree", "list", "--porcelain")
    if r.returncode != 0:
        return {}, (r.stderr or r.stdout).strip() or "git worktree list failed"
    mapping: dict[str, str] = {}
    path: str | None = None
    branch: str | None = None

    def finish() -> None:
        nonlocal path, branch
        if path:
            mapping[str(Path(path).resolve())] = branch or ""
        path = None
        branch = None

    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            finish()
            path = line[len("worktree "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "":
            finish()
    finish()
    return mapping, None


def reap_worktrees(
    workdir: Path | str,
    *,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
    dry_run: bool = True,
    act: bool = False,
    owner_released: bool = False,
    now: float | None = None,
) -> ReapResult:
    """Discover stale run worktrees; delegate only after explicit owner release."""
    workdir = Path(workdir).resolve()
    effective_act = bool(act and owner_released and not dry_run)
    result = ReapResult(
        dry_run=not effective_act,
        act=bool(act),
        owner_released=bool(owner_released),
    )

    if not _git_available(workdir):
        result.errors.append(
            {"reason": "git unavailable or not a repo", "workdir": str(workdir)}
        )
        return result

    root = workdir / WORKTREE_ROOT_REL
    if not root.exists():
        return result

    state = _load_state(workdir)
    if state is None:
        result.errors.append({"reason": "state.json missing or unparseable"})
        return result

    registered, registration_error = _registered_worktrees(workdir)
    if registration_error:
        result.errors.append({"reason": registration_error})
        return result

    active = _active_branches(state)
    owners = _attributions(state)
    now_ts = now if now is not None else time.time()
    cutoff = now_ts - (min_age_hours * 3600.0)

    if act and not owner_released:
        result.errors.append(
            {"reason": "--act requires explicit --owner-released; report-only"}
        )
    if owner_released and not act:
        result.errors.append(
            {"reason": "--owner-released without --act is report-only"}
        )

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
            result.skipped_too_young.append(
                {
                    "path": str(entry),
                    "age_hours": round((now_ts - mtime) / 3600.0, 3),
                    "min_age_hours": min_age_hours,
                }
            )
            continue

        resolved = str(entry.resolve())
        branch = registered.get(resolved)
        if not branch:
            result.skipped_unattributed.append(
                {
                    "path": str(entry),
                    "reason": "folder is not a registered branch worktree",
                }
            )
            continue
        if branch in active:
            result.skipped_active.append({"path": str(entry), "branch": branch})
            continue

        matching = [
            row
            for row in owners.get(branch, [])
            if row.get("path") == resolved
        ]
        run_ids = sorted({str(row["run_id"]) for row in matching})
        if len(run_ids) != 1:
            result.skipped_unattributed.append(
                {
                    "path": str(entry),
                    "branch": branch,
                    "reason": (
                        "no unique durable run attribution"
                        if not run_ids
                        else f"ambiguous run attribution: {run_ids}"
                    ),
                }
            )
            continue
        run_id = run_ids[0]

        merged = collapse_run._is_ancestor(workdir, branch, "main")
        if merged is not True:
            result.skipped_unmerged.append(
                {
                    "path": str(entry),
                    "branch": branch,
                    "run_id": run_id,
                    "reason": "branch is not merged into main",
                }
            )
            continue

        candidate = {"path": str(entry), "branch": branch, "run_id": run_id}
        result.candidates.append(candidate)
        if not effective_act:
            continue

        finalized = collapse_run.collapse(
            workdir,
            run_id=run_id,
            branch=branch,
            strict=True,
            merged_only=True,
            owner_released=True,
            require_run_root=True,
            release_source="worktree-reaper-explicit",
            expected_path=resolved,
        )
        if finalized.get("strict_success"):
            result.bundled_and_removed.append(
                {
                    **candidate,
                    "bundle": finalized.get("bundle_path"),
                    "receipt": finalized.get("receipt_path"),
                }
            )
        else:
            result.errors.append(
                {
                    **candidate,
                    "reason": "strict finalizer did not complete",
                    "details": finalized.get("errors") or [],
                }
            )

    return result
