#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Merge-risk scoring that grades the BASE, not the test result.

A branch's own green test suite is evidence about the branch's own tree at
its own merge-base -- it says nothing about mergeability once ``target``
(usually ``main``) has moved on. This script computes, read-only via git
plumbing, whether a branch's evidence is still valid against the CURRENT
target, and whether a naive merge is likely to conflict.

Read-only contract: this script never merges, rebases, checks out, or
writes any ref. Conflict prediction uses ``git merge-tree`` (the
``--write-tree`` form when available, falling back to the legacy 3-arg
form), never a trial merge or working-tree checkout.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# git plumbing helpers (read-only)
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _git_out(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def _merge_base(repo: Path, branch: str, target: str) -> str:
    return _git_out(repo, "merge-base", branch, target)


def _rev_parse(repo: Path, rev: str) -> str:
    return _git_out(repo, "rev-parse", rev)


def _rev_list_count(repo: Path, range_expr: str, pathspec: Iterable[str] | None = None) -> int:
    args = ["rev-list", "--count", range_expr]
    if pathspec:
        args += ["--", *pathspec]
    out = _git_out(repo, *args)
    return int(out) if out else 0


def _rev_list_shas(repo: Path, range_expr: str, pathspec: Iterable[str]) -> list[str]:
    """Commits in range_expr touching any path in pathspec, newest first."""
    if not pathspec:
        return []
    out = _git_out(repo, "rev-list", range_expr, "--", *pathspec)
    return [line for line in out.splitlines() if line]


def _diff_files(repo: Path, a: str, b: str) -> list[str]:
    out = _git_out(repo, "diff", "--name-only", f"{a}..{b}")
    return [line for line in out.splitlines() if line]


def _committer_date(repo: Path, rev: str) -> datetime:
    out = _git_out(repo, "show", "-s", "--format=%cI", rev)
    return _parse_iso8601(out)


def _list_local_branches(repo: Path) -> list[str]:
    out = _git_out(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    return [b for b in out.splitlines() if b]


def _parse_iso8601(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Conflict prediction -- read-only, never a trial merge
# ---------------------------------------------------------------------------

def _count_write_tree_conflicts(stdout: str) -> int:
    """Count distinct conflicted paths in `git merge-tree --write-tree` output.

    Format: first line is the resulting tree OID; then, only when there are
    conflicts, a stage-listing block (``<mode> <oid> <stage>\\t<path>``) for
    every unmerged path, followed by a blank line and free-text messages.
    Any path appearing in the stage-listing block is conflicted.
    """
    lines = stdout.splitlines()
    if len(lines) <= 1:
        return 0
    paths: set[str] = set()
    for line in lines[1:]:
        if not line.strip():
            break
        parts = line.split("\t", 1)
        if len(parts) == 2:
            paths.add(parts[1])
    return len(paths)


def probe_conflicts(repo: Path, target: str, branch: str, merge_base_sha: str) -> tuple[int | None, str]:
    """Predict merge conflicts between target and branch. Read-only.

    Never performs a trial merge, checkout, or ref write. Tries the modern
    `git merge-tree --write-tree <target> <branch>` form (git >= 2.38) and
    falls back to the legacy 3-arg `git merge-tree <base> <branch> <target>`
    form, counting `<<<<<<<` markers in its diff3-style output. Returns
    (None, "unavailable") if neither form can be used -- never guesses.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "merge-tree", "--write-tree", target, branch],
            capture_output=True,
            text=True,
        )
    except OSError:
        proc = None

    if proc is not None:
        stderr_lower = (proc.stderr or "").lower()
        unsupported = "unknown option" in stderr_lower or "unknown switch" in stderr_lower
        if not unsupported and proc.returncode in (0, 1):
            return _count_write_tree_conflicts(proc.stdout), "write-tree"

    # Fallback: legacy 3-arg form. Also read-only -- prints a diff3-style
    # merge result to stdout without touching the working tree or refs.
    try:
        legacy = subprocess.run(
            ["git", "-C", str(repo), "merge-tree", merge_base_sha, branch, target],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None, "unavailable"

    legacy_stderr = (legacy.stderr or "").lower()
    if "usage: git merge-tree" in legacy_stderr or legacy.returncode not in (0,):
        return None, "unavailable"
    return legacy.stdout.count("<<<<<<<"), "legacy"


# ---------------------------------------------------------------------------
# Core scoring
# ---------------------------------------------------------------------------

def score(
    repo: str | Path,
    branch: str,
    target: str = "main",
    evidence_ref: str | None = None,
    evidence_time: str | datetime | None = None,
    evidence: dict[str, str] | None = None,
    strict: bool = False,
    allow_unprobed: bool = False,
) -> dict:
    """Score merge risk of `branch` against `target`. Read-only.

    See module docstring: this never merges, rebases, checks out, or writes
    any ref. It computes the intersection of what the branch changed and
    what target changed since their merge-base (`contested_files`), and
    refuses to let passing test evidence downgrade the risk when that
    evidence was produced at a base that no longer reflects target.

    Two related fail-closed rules:

    * A conflict probe that could not run (`predicted_conflicts is None`)
      is NOT evidence of mergeability. `None` is falsy just like `0`, so
      without an explicit check it would silently fall through to the
      "mergeable" verdict -- a run we could not observe is not a pass.
      Set `allow_unprobed=True` to opt into treating an unprobed branch
      as a clean exit (the verdict is still recorded either way).
    * An explicit `evidence={"label": "fail"}` claim must never be
      ignored -- the CLI's `--evidence label=pass|fail` metavar promises
      the fail case is read, not just parsed.
    """
    repo = Path(repo)
    evidence = dict(evidence or {})

    merge_base_sha = _merge_base(repo, branch, target)
    behind = _rev_list_count(repo, f"{branch}..{target}")
    ahead = _rev_list_count(repo, f"{target}..{branch}")

    base_dt = _committer_date(repo, merge_base_sha)
    now = datetime.now(timezone.utc)
    base_age_days = (now - base_dt).total_seconds() / 86400.0

    branch_files = _diff_files(repo, merge_base_sha, branch)
    target_files = _diff_files(repo, merge_base_sha, target)
    contested_files = sorted(set(branch_files) & set(target_files))

    target_churn_on_contested = {
        f: _rev_list_count(repo, f"{merge_base_sha}..{target}", pathspec=[f])
        for f in contested_files
    }

    predicted_conflicts, conflict_probe = probe_conflicts(repo, target, branch, merge_base_sha)

    # Evidence provenance: default evidence_ref is the branch tip.
    resolved_evidence_ref = _rev_parse(repo, evidence_ref if evidence_ref else branch)

    eff_evidence_time: datetime | None = None
    if evidence_time is not None:
        eff_evidence_time = _parse_iso8601(evidence_time)
    elif evidence_ref is not None:
        eff_evidence_time = _committer_date(repo, resolved_evidence_ref)

    newest_touch_sha = None
    newest_touch_dt = None
    touching_shas = _rev_list_shas(repo, f"{merge_base_sha}..{target}", branch_files)
    if touching_shas:
        newest_touch_sha = touching_shas[0]
        newest_touch_dt = _committer_date(repo, newest_touch_sha)

    time_stale = bool(eff_evidence_time and newest_touch_dt and eff_evidence_time < newest_touch_dt)

    # true only when behind == 0, or target's changes since the base are
    # disjoint from branch_files (i.e. no contested files); false whenever
    # contested_files is non-empty or the supplied evidence-time predates
    # the newest target commit touching a branch-touched file.
    evidence_valid_against_target = (not contested_files) and (not time_stale)

    # An explicit fail claim in --evidence must never be silently dropped:
    # the CLI promises "label=pass|fail" is read, not just parsed.
    evidence_has_failure = any(status == "fail" for status in evidence.values())

    # ---- verdict, in priority order --------------------------------------
    evidence_ignored_reason = None
    if not evidence_valid_against_target:
        verdict = "stale_base_evidence_invalid"
        risk = "high"
        if evidence:
            # A green suite recorded against a stale base is NOT evidence of
            # mergeability -- it must never downgrade the verdict. A stale
            # base invalidates the evidence entirely, pass or fail, so this
            # verdict outranks the failing-evidence check below; the reason
            # names both when both are present.
            evidence_ignored_reason = (
                "produced_at_stale_base_and_failing_evidence"
                if evidence_has_failure
                else "produced_at_stale_base"
            )
    elif evidence_has_failure:
        # A failing suite is a positive signal of non-mergeability -- it
        # must never be ignored just because contested_files is empty.
        verdict = "evidence_failing"
        risk = "high"
    elif predicted_conflicts:
        verdict = "conflict_likely"
        risk = "high"
    elif behind > 0 and not contested_files:
        verdict = "behind_but_disjoint"
        risk = "medium"
    elif predicted_conflicts is None:
        # The probe never ran (git too old, corrupt object, unrelated
        # histories, etc). `None` is falsy exactly like `0` -- without this
        # branch the `elif predicted_conflicts:` check above would let an
        # unobserved probe fall through to "mergeable". It never gets here.
        verdict = "conflict_probe_unavailable"
        risk = "medium"
    else:
        verdict = "mergeable_evidence_current"
        risk = "low"

    if verdict == "mergeable_evidence_current":
        exit_code = 0
    elif verdict == "behind_but_disjoint":
        exit_code = 1 if strict else 0
    elif verdict == "conflict_probe_unavailable":
        exit_code = 0 if allow_unprobed else 1
    else:
        exit_code = 1

    return {
        "branch": branch,
        "target": target,
        "merge_base": merge_base_sha,
        "behind": behind,
        "ahead": ahead,
        "base_age_days": round(base_age_days, 2),
        "branch_files": branch_files,
        "target_files": target_files,
        "contested_files": contested_files,
        "target_churn_on_contested": target_churn_on_contested,
        "predicted_conflicts": predicted_conflicts,
        "conflict_probe": conflict_probe,
        "evidence_ref": resolved_evidence_ref,
        "evidence": evidence,
        "evidence_valid_against_target": evidence_valid_against_target,
        "evidence_ignored_reason": evidence_ignored_reason,
        "verdict": verdict,
        "risk": risk,
        "exit_code": exit_code,
    }


_RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


def sweep(
    repo: str | Path,
    target: str = "main",
    strict: bool = False,
    allow_unprobed: bool = False,
) -> list[dict]:
    """Score every local branch (except target) against target. Read-only."""
    repo = Path(repo)
    branches = [b for b in _list_local_branches(repo) if b != target]
    results = []
    for b in branches:
        try:
            results.append(score(repo, b, target=target, strict=strict, allow_unprobed=allow_unprobed))
        except subprocess.CalledProcessError as exc:
            results.append({
                "branch": b,
                "target": target,
                "verdict": "error",
                "risk": "unknown",
                "exit_code": 1,
                "error": str(exc),
            })
    results.sort(key=lambda r: _RISK_ORDER.get(r.get("risk"), 3))
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_evidence_args(raw: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise SystemExit(f"invalid --evidence value (expected label=pass|fail): {item!r}")
        label, _, status = item.partition("=")
        parsed[label.strip()] = status.strip().lower()
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score merge risk by grading the base, not the test result. Read-only.",
    )
    parser.add_argument("--branch", help="branch to score")
    parser.add_argument("--target", default="main", help="merge target (default: main)")
    parser.add_argument("--repo", default=".", help="repo path (default: cwd)")
    parser.add_argument("--evidence-ref", help="commit the test evidence was produced at (default: branch tip)")
    parser.add_argument("--evidence-time", help="ISO8601 timestamp the evidence was produced")
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        metavar="label=pass|fail",
        help="repeatable evidence claim, e.g. --evidence \"cargo test=pass\"",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--strict", action="store_true", help="behind_but_disjoint also exits 1")
    parser.add_argument("--all-branches", action="store_true", help="sweep every local branch, risk-first")
    parser.add_argument(
        "--allow-unprobed",
        action="store_true",
        help=(
            "opt into exit 0 when the conflict probe could not run "
            "(verdict stays conflict_probe_unavailable either way)"
        ),
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    evidence = _parse_evidence_args(args.evidence)

    if args.all_branches:
        results = sweep(repo, target=args.target, strict=args.strict, allow_unprobed=args.allow_unprobed)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            for r in results:
                print(f"{r.get('risk', '?'):6} {r.get('verdict', '?'):32} {r.get('branch')}")
        return 1 if any(r.get("exit_code", 0) != 0 for r in results) else 0

    if not args.branch:
        parser.error("--branch is required unless --all-branches is given")

    try:
        result = score(
            repo,
            args.branch,
            target=args.target,
            evidence_ref=args.evidence_ref,
            evidence_time=args.evidence_time,
            evidence=evidence,
            strict=args.strict,
            allow_unprobed=args.allow_unprobed,
        )
    except subprocess.CalledProcessError as exc:
        print(f"error: git command failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"verdict: {result['verdict']} (risk={result['risk']})")
        print(f"merge_base: {result['merge_base']}  behind={result['behind']} ahead={result['ahead']}")
        if result["contested_files"]:
            print(f"contested_files: {', '.join(result['contested_files'])}")
            print(f"target_churn_on_contested: {result['target_churn_on_contested']}")
        if result["evidence_ignored_reason"]:
            print(f"evidence_ignored_reason: {result['evidence_ignored_reason']}")
        if result["predicted_conflicts"] is not None:
            print(f"predicted_conflicts: {result['predicted_conflicts']} ({result['conflict_probe']})")
        else:
            print("predicted_conflicts: unavailable")

    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
