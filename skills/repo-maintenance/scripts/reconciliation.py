"""Commit-pinned review records for the existing maintenance audit.

This checks evidence coverage and freshness, not semantic truth or authority.
It never merges, deletes, or treats patch equivalence as retirement proof.
"""
from __future__ import annotations

from itertools import combinations
from pathlib import Path
from typing import Any, Callable


def draft(report: dict[str, Any], git: Callable[..., Any]) -> dict[str, Any]:
    repo = Path(report["repo_root"])
    target = report["base_head"]
    if not target:
        raise ValueError("reconciliation requires an existing target branch")
    candidates = []
    for branch in report["branches"]:
        if branch["name"] == report["base"]:
            continue
        head = branch["head"]
        bases = git(repo, "merge-base", "--all", target, head, check=False)
        base_list = bases.stdout.splitlines() if bases.returncode == 0 else []
        base = base_list[0] if len(base_list) == 1 else None

        def paths(left: str, right: str) -> list[str]:
            return sorted(filter(None, git(repo, "diff", "--name-only", "--no-renames", "-z",
                                           left, right, "--").stdout.split("\0")))

        source_paths = paths(base, head) if base else []
        target_paths = paths(base, target) if base else []
        candidates.append({
            "source_ref": branch["name"], "source_head": head,
            "merge_base": base, "source_paths": source_paths,
            "target_paths": target_paths,
            "tip_difference_paths": paths(target, head),
            "ancestor_of_target": branch["merged_into_base"],
            "ownership": "unknown", "ownership_evidence": "",
            "units": [{
                "paths": source_paths, "behavior": "", "rationale": "",
                "disposition": "unverified", "ui": "unreviewed",
                "evidence": [],
            }],
        })
    overlaps = []
    for a, b in combinations(candidates, 2):
        common = sorted(set(a["source_paths"]) & set(b["source_paths"]))
        if common:
            overlaps.append({"sources": [a["source_ref"], b["source_ref"]], "paths": common})
    # Pin again after all comparisons so a concurrent ref move cannot go unnoticed.
    for ref, expected in [(report["base"], target), *[
        (c["source_ref"], c["source_head"]) for c in candidates
    ]]:
        if git(repo, "rev-parse", "--verify", f"refs/heads/{ref}").stdout.strip() != expected:
            raise ValueError(f"ref moved during comparison: {ref}; rerun the audit")
    return {
        "schema_version": 1, "repo_root": str(repo),
        "target_ref": report["base"], "target_head": target,
        "candidates": candidates, "overlaps": overlaps,
        "comparison_evidence": "", "goals": report["goals"],
        "retained_state": {
            "stashes": report["stashes"],
            "detached_worktrees": [w for w in report["worktrees"] if not w.get("branch")],
            "dirty_worktrees": [w for w in report["worktrees"] if w.get("dirty")],
        },
    }


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check(record: Any, current: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Return per-unit next steps; a complete record grants no mutation authority."""
    errors: list[str] = []
    actions: list[dict[str, Any]] = []
    if not isinstance(record, dict):
        return {"review_complete": False, "errors": ["record must be an object"], "actions": []}
    for key in ("schema_version", "repo_root", "target_ref", "target_head", "overlaps", "retained_state", "goals"):
        if record.get(key) != current[key]:
            errors.append(f"stale or altered {key}; refresh the comparison")
    rows = record.get("candidates")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return {"review_complete": False, "errors": errors + ["candidates must be objects"], "actions": []}
    by_ref = {row.get("source_ref"): row for row in rows if isinstance(row.get("source_ref"), str)}
    expected = {row["source_ref"] for row in current["candidates"]}
    if len(by_ref) != len(rows) or set(by_ref) != expected:
        errors.append("candidate coverage changed, duplicated or incomplete; refresh the comparison")
    if len(expected) > 1 and not _text(record.get("comparison_evidence")):
        errors.append("compare candidate implementations with each other and cite the evidence")

    target_worktrees = [w for w in report["worktrees"] if w.get("branch") == report["base"]]
    # Rebase detaches HEAD, so porcelain cannot attribute that operation to its
    # original branch. Preserve all candidates until ambiguous ownership clears.
    detached_operation = any(not w.get("branch") and w.get("operations_in_progress")
                             for w in report["worktrees"])
    global_hold = bool(report["operations_in_progress"] or detached_operation or any(
        w.get("dirty") or w.get("locked") or w.get("operations_in_progress") or not w.get("git_available") for w in target_worktrees
    ))
    for fresh in current["candidates"]:
        ref = fresh["source_ref"]
        row = by_ref.get(ref)
        if row is None:
            continue
        for key in ("source_head", "merge_base", "source_paths", "target_paths",
                    "tip_difference_paths", "ancestor_of_target"):
            if row.get(key) != fresh[key]:
                errors.append(f"{ref}: stale or altered {key}")
        from ref_goals import closure_error
        goal_rows = [g for g in current["goals"] if g["branch"] == ref and g["path"] is None]
        goal = goal_rows[0] if len(goal_rows) == 1 else None
        goal_missing = goal is None or goal["goal_status"] != "recorded"
        goal_close_error = (goal.get("reason") if goal else "missing_goal") if goal_missing else closure_error(
            goal["records"][0]["contract"], fresh["source_head"], current["target_head"])
        ownership = row.get("ownership")
        source_worktrees = [w for w in report["worktrees"] if w.get("branch") == ref]
        held = global_hold or ownership != "released" or not _text(row.get("ownership_evidence")) or any(
            w.get("dirty") or w.get("locked") or w.get("operations_in_progress") or not w.get("git_available") for w in source_worktrees
        )
        if not isinstance(ownership, str) or ownership not in {"unknown", "active", "released"}:
            errors.append(f"{ref}: ownership must be unknown, active or released")
        units = row.get("units")
        if not isinstance(units, list) or not units or any(not isinstance(u, dict) for u in units):
            errors.append(f"{ref}: at least one change unit is required")
            continue
        covered: set[str] = set()
        for index, unit in enumerate(units):
            label = f"{ref}[{index}]"
            paths = unit.get("paths")
            if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
                errors.append(f"{label}: paths must be a list of strings")
                continue
            if len(set(paths)) != len(paths) or set(paths) - set(fresh["source_paths"]):
                errors.append(f"{label}: paths must belong to the source diff without duplicates")
            covered.update(paths)
            disposition, ui = unit.get("disposition"), unit.get("ui")
            if not isinstance(disposition, str) or disposition not in {"additive", "represented", "superseded", "competing", "incomplete", "unverified"}:
                errors.append(f"{label}: invalid disposition")
            if not isinstance(ui, str) or ui not in {"none", "compatible", "competing", "unreviewed"}:
                errors.append(f"{label}: invalid UI review")
            if not _text(unit.get("behavior")) or not _text(unit.get("rationale")):
                errors.append(f"{label}: describe the behavior and disposition rationale")
            evidence = unit.get("evidence")
            if not isinstance(evidence, list) or not evidence or any(not _text(e) for e in evidence):
                errors.append(f"{label}: cite inspected code/diffs and verification evidence")
            if disposition == "competing" or ui == "competing":
                action = "needs_user_choice"
            elif goal_missing or disposition == "unverified" or ui == "unreviewed" or not fresh["merge_base"]:
                action = "needs_review"
            elif held or disposition == "incomplete":
                action = "preserve"
            elif disposition == "additive":
                action = "integration_checks" if paths else "needs_review"
            else:
                action = "needs_review" if goal_close_error else "retirement_checks"
            actions.append({"source_ref": ref, "unit": index, "paths": paths, "next_step": action, "goal_closure_hold": goal_close_error})
        if covered != set(fresh["source_paths"]):
            errors.append(f"{ref}: units must cover every source diff path")
    if errors:
        for action in actions:
            if action["next_step"] in {"integration_checks", "retirement_checks"}:
                action["next_step"] = "needs_review"
    return {
        "review_complete": not errors and all(a["next_step"] != "needs_review" for a in actions),
        "errors": errors, "actions": actions,
        "review_scope": "local branch diffs only; remote-only branches and retained state require separate review",
        "retained_state": current["retained_state"], "goals": current["goals"],
        "whole_branch_steps": {
            ref: ("integration_checks" if steps == {"integration_checks"}
                  else "retirement_checks" if steps == {"retirement_checks"}
                  else "preserve")
            for ref in sorted(expected)
            for steps in [{a["next_step"] for a in actions if a["source_ref"] == ref}]
        },
        "boundary": "Evidence completeness only. Recheck ownership, recovery, merge risk and exact-target validation before mutation. Never execute a whole mixed branch from a unit verdict.",
    }
