#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""collapse_run.py — End-of-run branch/worktree cleanup for build-loop.

Reads the three ref registries from .build-loop/state.json for the selected
run, bundles all refs for safety, then for each ref:

  - MERGED into main          → delete branch (+ worktree folder if any)
  - UNMERGED + review_hold    → remove worktree folder only; keep branch
  - UNMERGED + no review_hold → remove worktree folder only; surface branch
                                 for operator keep/discard decision

CONTRACT NOTE: this script NEVER runs `git merge`. Merging the winning
branch onto main is the orchestrator's exclusive responsibility (it does so
before calling collapse). After a successful merge, the branch reads as
MERGED here and is deleted cleanly. This separation ensures collapse is
always safe to call as an idempotent cleanup step.

CLI:
  collapse_run.py --workdir <repo> [--run-id latest|<id>] [--dry-run] [--json]

Exit codes:
  0 — success (including fail-soft per-ref errors)
  1 — hard failure: state.json missing/unparseable, or git unavailable
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(workdir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _git_available(workdir: Path) -> bool:
    try:
        r = _git(workdir, "rev-parse", "--git-dir", check=False)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def _current_branch(workdir: Path) -> str | None:
    """Return the branch currently checked out in the primary worktree, or None."""
    try:
        r = _git(workdir, "symbolic-ref", "--short", "HEAD", check=False)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _is_ancestor(workdir: Path, branch: str, target: str = "main") -> bool | None:
    """Return True if branch is an ancestor of target (i.e. fully merged).

    Returns None on error (unknown branch, detached HEAD, etc.).
    """
    try:
        r = _git(workdir, "merge-base", "--is-ancestor", branch, target, check=False)
        if r.returncode == 0:
            return True
        if r.returncode == 1:
            return False
        # rc=128: branch or target unknown
        return None
    except Exception:
        return None


def _delete_branch(workdir: Path, branch: str) -> str | None:
    """Delete branch. Returns None on success, error string on failure."""
    try:
        r = _git(workdir, "branch", "-D", branch, check=False)
        if r.returncode == 0:
            return None
        return (r.stderr or r.stdout).strip() or f"git branch -D {branch} exited {r.returncode}"
    except Exception as exc:
        return str(exc)


def _remove_worktree(workdir: Path, path: str) -> str | None:
    """Remove a worktree folder. Returns None on success, error string on failure."""
    wt_path = Path(path)
    if not wt_path.exists():
        # Idempotent: already gone is fine
        return None
    try:
        r = _git(workdir, "worktree", "remove", "-f", "-f", str(wt_path), check=False)
        if r.returncode == 0:
            return None
        return (r.stderr or r.stdout).strip() or f"git worktree remove exited {r.returncode}"
    except Exception as exc:
        return str(exc)


# ---------------------------------------------------------------------------
# State loading
# ---------------------------------------------------------------------------

def _load_state(workdir: Path) -> dict[str, Any]:
    state_path = workdir / ".build-loop" / "state.json"
    if not state_path.exists():
        raise SystemExit(f"state.json not found at {state_path}")
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"state.json unparseable: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("state.json root must be a JSON object")
    return data


def _write_state(workdir: Path, state: dict[str, Any]) -> None:
    state_path = workdir / ".build-loop" / "state.json"
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _pick_run(state: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    runs = state.get("runs")
    if not isinstance(runs, list) or not runs:
        return None
    if run_id == "latest":
        return runs[-1]
    for run in reversed(runs):
        if isinstance(run, dict) and (run.get("run_id") == run_id or run.get("id") == run_id):
            return run
    return None


# ---------------------------------------------------------------------------
# Ref normalization
# ---------------------------------------------------------------------------

def _default_close_criteria(
    branch: str,
    merge_target: str,
    kind: str,
    review_hold: bool,
) -> list[str]:
    criteria = [f"{branch} is merged into {merge_target}"]
    if kind == "worktree":
        criteria.append("worktree folder is removed from .build-loop/worktrees")
    if review_hold:
        criteria.append("human review disposition is recorded before branch deletion")
    else:
        criteria.append("branch is deleted after merge")
    return criteria


def _ensure_ledger_ref(run: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    created_refs = run.setdefault("createdRefs", [])
    for entry in created_refs:
        if isinstance(entry, dict) and entry.get("branch") == ref["branch"]:
            return entry

    kind = "worktree" if ref.get("path") else "branch"
    entry = {
        "kind": kind,
        "path": ref.get("path"),
        "branch": ref["branch"],
        "merge_target": ref.get("merge_target", "main"),
        "purpose": ref.get("summary", ""),
        "close_criteria": _default_close_criteria(
            ref["branch"],
            ref.get("merge_target", "main"),
            kind,
            bool(ref.get("review_hold", False)),
        ),
        "status": "open",
        "close_reason": None,
        "review_hold": bool(ref.get("review_hold", False)),
        "created_ts": _now_iso(),
        "closed_ts": None,
        "last_status_ts": _now_iso(),
    }
    created_refs.append(entry)
    return entry


def _mark_ref_status(
    run: dict[str, Any],
    ref: dict[str, Any],
    status: str,
    reason: str,
) -> dict[str, Any]:
    entry = _ensure_ledger_ref(run, ref)
    now = _now_iso()
    entry.setdefault("kind", "worktree" if ref.get("path") else "branch")
    if ref.get("path") and not entry.get("path"):
        entry["path"] = ref["path"]
    entry.setdefault("merge_target", ref.get("merge_target", "main"))
    entry.setdefault("purpose", ref.get("summary", ""))
    entry.setdefault(
        "close_criteria",
        _default_close_criteria(
            ref["branch"],
            entry.get("merge_target", "main"),
            entry.get("kind", "branch"),
            bool(entry.get("review_hold", False)),
        ),
    )
    entry.setdefault("review_hold", bool(ref.get("review_hold", False)))
    entry["status"] = status
    entry["close_reason"] = reason
    entry["last_status_ts"] = now
    if status == "closed":
        entry["closed_ts"] = now
    else:
        entry.setdefault("closed_ts", None)
    return {
        "branch": ref["branch"],
        "status": status,
        "reason": reason,
    }


def _normalize_refs(run: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Merge the three registries into one unified ref list, deduped by branch name.

    Each entry has:
      branch         str
      path           str | None   (worktree folder, if known)
      review_hold    bool
      summary        str          (human note for kept_for_review output)
      source         str          (which registry it came from)
    """
    seen: dict[str, dict[str, Any]] = {}

    skipped_protected: list[str] = []
    closed_branches = {
        entry.get("branch")
        for entry in run.get("createdRefs") or []
        if isinstance(entry, dict) and entry.get("status") == "closed"
    }

    def _add(
        branch: str,
        path: str | None,
        review_hold: bool,
        summary: str,
        source: str,
        merge_target: str = "main",
    ) -> None:
        if not branch:
            return
        if branch in closed_branches:
            return
        if branch == "main":
            skipped_protected.append(f"skipped protected branch in {source}: main")
            return
        if branch not in seen:
            seen[branch] = {
                "branch": branch,
                "path": path,
                "review_hold": review_hold,
                "summary": summary,
                "source": source,
                "merge_target": merge_target,
            }
        else:
            # Later registries can upgrade review_hold (risky overrides dispatched)
            if review_hold:
                seen[branch]["review_hold"] = True
            if path and not seen[branch]["path"]:
                seen[branch]["path"] = path
            if merge_target and not seen[branch].get("merge_target"):
                seen[branch]["merge_target"] = merge_target

    # dispatchedWorktrees[] — work already integrated, so review_hold=False
    for entry in run.get("dispatchedWorktrees") or []:
        if not isinstance(entry, dict):
            continue
        _add(
            branch=entry.get("branch", ""),
            path=entry.get("path"),
            review_hold=False,
            summary="dispatch worktree (already integrated)",
            source="dispatchedWorktrees",
            merge_target=entry.get("merge_target", "main"),
        )

    # riskyBranches[] — always review_hold=True
    for entry in run.get("riskyBranches") or []:
        if not isinstance(entry, dict):
            continue
        _add(
            branch=entry.get("branch", ""),
            path=entry.get("path"),
            review_hold=True,
            summary=entry.get("summary", "risky branch"),
            source="riskyBranches",
            merge_target=entry.get("merge_target", "main"),
        )

    # createdRefs[] — use their own review_hold flag
    for entry in run.get("createdRefs") or []:
        if not isinstance(entry, dict):
            continue
        _add(
            branch=entry.get("branch", ""),
            path=entry.get("path"),
            review_hold=bool(entry.get("review_hold", False)),
            summary=entry.get("purpose") or entry.get("summary", ""),
            source="createdRefs",
            merge_target=entry.get("merge_target", "main"),
        )

    return [v for v in seen.values() if v["branch"]], skipped_protected


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

def _create_bundle(workdir: Path, run_id: str) -> tuple[str | None, str | None]:
    """Create a bundle of all refs. Returns (bundle_path_str, error_str)."""
    bundles_dir = workdir / ".build-loop" / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bundles_dir / f"collapse-{run_id}-{_now_utc()}.bundle"
    try:
        r = _git(workdir, "bundle", "create", str(bundle_path), "--all", check=False)
        if r.returncode == 0:
            return str(bundle_path), None
        err = (r.stderr or r.stdout).strip() or f"git bundle create exited {r.returncode}"
        return None, err
    except Exception as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# Core collapse logic
# ---------------------------------------------------------------------------

def collapse(
    workdir: Path,
    run_id: str = "latest",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the collapse for the selected run. Returns the result dict."""

    # 1. Hard-fail gates: git available + state readable
    if not _git_available(workdir):
        raise SystemExit("git is not available or workdir is not a git repo")

    state = _load_state(workdir)

    run = _pick_run(state, run_id)
    actual_run_id: str = (run or {}).get("run_id") or (run or {}).get("id") or run_id

    refs, skipped_notes = _normalize_refs(run) if run else ([], [])

    current_branch = _current_branch(workdir)

    result: dict[str, Any] = {
        "run_id": actual_run_id,
        "bundle_path": None,
        "deleted": [],
        "kept_for_review": [],
        "surfaced_unmerged": [],
        "errors": list(skipped_notes),
        "dry_run": dry_run,
        "ledger_updated": [],
    }

    if not refs:
        return result

    # 2. Bundle first (skip in dry-run; bundle creation is side-effectful)
    bundle_succeeded = False
    if not dry_run:
        bundle_path, bundle_err = _create_bundle(workdir, actual_run_id)
        if bundle_path:
            result["bundle_path"] = bundle_path
            bundle_succeeded = True
        else:
            result["errors"].append(f"bundle failed: {bundle_err}")
            # Fail-soft: continue, but we will not delete unmerged branches
            # (merged branches are safe to delete even without a bundle since
            # their content is already in main).

    # 3. Per-ref processing
    for ref in refs:
        branch = ref["branch"]
        path = ref["path"]
        review_hold = ref["review_hold"]

        # Never operate on the currently-checked-out branch of the main worktree
        # (main itself is already filtered out during normalization)
        if current_branch and branch == current_branch:
            result["errors"].append(f"skipped currently-checked-out branch: {branch}")
            if not dry_run and run:
                result["ledger_updated"].append(
                    _mark_ref_status(
                        run,
                        ref,
                        "error",
                        "skipped because branch is checked out in the main worktree",
                    )
                )
            continue

        # Classify against main
        merge_target = ref.get("merge_target", "main")
        is_merged = _is_ancestor(workdir, branch, merge_target)

        if is_merged is None:
            # Branch likely doesn't exist (already deleted = idempotent) or unknown error
            result["errors"].append(f"could not classify {branch} (may not exist)")
            if not dry_run and run:
                result["ledger_updated"].append(
                    _mark_ref_status(
                        run,
                        ref,
                        "error",
                        f"could not classify branch against {merge_target}",
                    )
                )
            continue

        if dry_run:
            # Classify only, no actions
            if is_merged:
                result["deleted"].append({"branch": branch, "path": path, "action": "would_delete"})
            elif review_hold:
                result["kept_for_review"].append({
                    "branch": branch,
                    "path": path,
                    "summary": ref["summary"],
                    "action": "would_keep_branch_remove_worktree",
                })
            else:
                result["surfaced_unmerged"].append({
                    "branch": branch,
                    "path": path,
                    "action": "would_remove_worktree_surface_branch",
                })
            continue

        # --- Live actions ---
        if is_merged:
            # Remove worktree first (can't delete a branch checked out in a worktree)
            wt_err: str | None = None
            if path:
                wt_err = _remove_worktree(workdir, path)
                if wt_err:
                    result["errors"].append(f"worktree remove failed for {branch}: {wt_err}")
                    result["ledger_updated"].append(
                        _mark_ref_status(
                            run,
                            ref,
                            "error",
                            f"worktree remove failed: {wt_err}",
                        )
                    )
                    continue

            br_err = _delete_branch(workdir, branch)
            if br_err:
                result["errors"].append(f"branch delete failed for {branch}: {br_err}")
                result["ledger_updated"].append(
                    _mark_ref_status(
                        run,
                        ref,
                        "error",
                        f"branch delete failed: {br_err}",
                    )
                )
            else:
                result["deleted"].append({"branch": branch, "path": path})
                result["ledger_updated"].append(
                    _mark_ref_status(
                        run,
                        ref,
                        "closed",
                        f"merged into {merge_target}; branch/worktree cleaned up",
                    )
                )

        elif review_hold:
            # Keep branch, remove worktree folder only
            if path:
                wt_err = _remove_worktree(workdir, path)
                if wt_err:
                    result["errors"].append(f"worktree remove failed for {branch}: {wt_err}")
                    result["ledger_updated"].append(
                        _mark_ref_status(
                            run,
                            ref,
                            "error",
                            f"worktree remove failed: {wt_err}",
                        )
                    )
                    continue
            result["kept_for_review"].append({
                "branch": branch,
                "path": path,
                "summary": ref["summary"],
            })
            result["ledger_updated"].append(
                _mark_ref_status(
                    run,
                    ref,
                    "kept_for_review",
                    "unmerged review_hold branch kept; worktree folder removed",
                )
            )

        else:
            # UNMERGED + no review_hold: remove worktree, surface branch
            # Safety: only skip deletion when unmerged AND bundle failed
            if path:
                wt_err = _remove_worktree(workdir, path)
                if wt_err:
                    result["errors"].append(f"worktree remove failed for {branch}: {wt_err}")
                    result["ledger_updated"].append(
                        _mark_ref_status(
                            run,
                            ref,
                            "error",
                            f"worktree remove failed: {wt_err}",
                        )
                    )
                    continue
            result["surfaced_unmerged"].append({"branch": branch, "path": path})
            result["ledger_updated"].append(
                _mark_ref_status(
                    run,
                    ref,
                    "surfaced_unmerged",
                    "unmerged branch surfaced for operator disposition; worktree folder removed",
                )
            )

    if result["ledger_updated"] and not dry_run:
        _write_state(workdir, state)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workdir", default=".", help="Repo root (must contain .build-loop/state.json)")
    parser.add_argument("--run-id", default="latest", help="Run ID to collapse, or 'latest' (default)")
    parser.add_argument("--dry-run", action="store_true", help="Classify refs but perform no git operations")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print result JSON to stdout")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()

    try:
        result = collapse(workdir, run_id=args.run_id, dry_run=args.dry_run)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Human summary to stderr
    dr_tag = " [DRY RUN]" if result["dry_run"] else ""
    print(
        f"collapse{dr_tag} run={result['run_id']} "
        f"deleted={len(result['deleted'])} "
        f"kept_for_review={len(result['kept_for_review'])} "
        f"surfaced_unmerged={len(result['surfaced_unmerged'])} "
        f"errors={len(result['errors'])}",
        file=sys.stderr,
    )

    if args.json_output:
        print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
