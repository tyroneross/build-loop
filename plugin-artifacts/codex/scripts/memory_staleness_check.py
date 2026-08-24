#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
memory_staleness_check.py — detect when project memory has drifted behind the repo's git HEAD.

Reads the LATEST line of the project's milestone log (append-only JSONL at
``<memory-root>/projects/<slug>/milestones.jsonl``).  Each line has a ``commit``
field recording the repo HEAD sha at milestone-write time.  The check counts
commits that landed in the repo AFTER that sha; when the count reaches the
configured threshold the run is flagged stale.

CLI
---
    memory_staleness_check.py --workdir <repo> [--project <slug>]
        [--memory-root <path>] [--commits-threshold N] [--json]

Exit code: always 0 (fail-soft).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import memory_update_ledger as mul  # type: ignore  # noqa: E402
from closeout.status import run_evidence  # type: ignore  # noqa: E402

DEFAULT_COMMITS_THRESHOLD = 5
# Resolved lazily via _paths.memory_store_root(); no literal default (see
# scripts/test_portable_paths.py for the guard that keeps it that way).


# ---------------------------------------------------------------------------
# Git helpers — reused pattern from stale_context_check.py
# ---------------------------------------------------------------------------

def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _repo_head(workdir: Path) -> str | None:
    """Return the full sha of HEAD, or None on failure."""
    r = _run_git(["rev-parse", "HEAD"], workdir)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.strip()


def _commits_since(workdir: Path, commit_hash: str) -> int | None:
    """Return count of commits reachable from HEAD but not from commit_hash."""
    r = _run_git(["rev-list", "--count", f"{commit_hash}..HEAD"], workdir)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _is_ancestor(workdir: Path, commit_hash: str) -> bool | None:
    """Return whether candidate is reachable from HEAD; None means git error."""
    r = _run_git(["merge-base", "--is-ancestor", commit_hash, "HEAD"], workdir)
    if r.returncode == 0:
        return True
    if r.returncode == 1:
        return False
    return None


def _is_git_repo(workdir: Path) -> bool:
    r = _run_git(["rev-parse", "--git-dir"], workdir)
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Milestone helpers
# ---------------------------------------------------------------------------

def _last_milestone_commit(milestones_path: Path) -> str | None:
    """Return the ``commit`` field from the LAST line of the JSONL file.

    Returns None when the file is absent, empty, or the last line has no
    ``commit`` key.
    """
    if not milestones_path.exists():
        return None
    last_line: str | None = None
    try:
        with milestones_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last_line = line
    except OSError:
        return None
    if last_line is None:
        return None
    try:
        obj = json.loads(last_line)
    except json.JSONDecodeError:
        return None
    return obj.get("commit") or None


def _baseline_candidates(memory_root: Path, slug: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    latest_update = mul.latest_project_update(memory_root, slug, require_commit=True)
    if latest_update and latest_update.get("source_commit"):
        candidates.append(("updates_ledger", str(latest_update["source_commit"])))

    milestones_path = memory_root / "projects" / slug / "milestones.jsonl"
    milestone_commit = _last_milestone_commit(milestones_path)
    if milestone_commit and ("milestones", milestone_commit) not in candidates:
        candidates.append(("milestones", milestone_commit))
    return candidates


def _latest_run_evidence(workdir: Path) -> dict[str, Any] | None:
    """Explain which terminal run should have refreshed memory, if observable."""
    state_path = workdir / ".build-loop" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    runs = state.get("runs") if isinstance(state, dict) else None
    if not isinstance(runs, list):
        return None
    for row in reversed(runs):
        if not isinstance(row, dict):
            continue
        evidence = run_evidence(row)
        if not evidence["shipped"]:
            continue
        run_id = str(row.get("run_id") or row.get("build_loop_id") or row.get("id") or "")
        marker = workdir / ".build-loop" / "closeout-pending" / f"milestone-owed-{run_id}.md"
        return {
            "run_id": run_id or None,
            "date": row.get("date"),
            "outcome": row.get("outcome"),
            "commit": evidence["commit"],
            "commit_source": evidence["commit_source"],
            "run_commit_present": evidence["run_commit_present"],
            "branch_closeout_status": evidence["branch_closeout_status"],
            "milestone_owed_marker": str(marker) if marker.exists() else None,
        }
    return None


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def check(
    workdir: Path,
    slug: str,
    memory_root: Path,
    commits_threshold: int,
) -> dict[str, Any]:
    """Run the memory-staleness check and return the result dict."""

    # --- guard: non-git workdir ---
    if not _is_git_repo(workdir):
        return {
            "slug": slug,
            "memory_as_of_commit": None,
            "repo_head": None,
            "commits_stale": 0,
            "stale": False,
            "reason": "workdir is not a git repository",
        }

    baselines = _baseline_candidates(memory_root, slug)

    # --- no baseline yet ---
    if not baselines:
        return {
            "slug": slug,
            "memory_as_of_commit": None,
            "repo_head": _repo_head(workdir),
            "commits_stale": 0,
            "stale": False,
            "reason": "no milestone baseline yet; no update ledger baseline yet",
        }

    repo_head = _repo_head(workdir)
    if repo_head is None:
        return {
            "slug": slug,
            "memory_as_of_commit": baselines[0][1],
            "baseline_source": baselines[0][0],
            "repo_head": None,
            "commits_stale": 0,
            "stale": False,
            "reason": "could not read repo HEAD",
        }

    baseline_source = baselines[0][0]
    memory_as_of_commit = baselines[0][1]
    reachable: list[tuple[int, str, str]] = []
    candidate_evidence: list[dict[str, Any]] = []
    for candidate_source, candidate_commit in baselines:
        ancestor = _is_ancestor(workdir, candidate_commit)
        if ancestor is not True:
            candidate_evidence.append({
                "source": candidate_source,
                "commit": candidate_commit,
                "reachable_from_head": ancestor,
                "commits_stale": None,
                "excluded_reason": (
                    "candidate is not an ancestor of HEAD"
                    if ancestor is False
                    else "candidate reachability could not be verified"
                ),
            })
            continue
        candidate_count = _commits_since(workdir, candidate_commit)
        if candidate_count is not None:
            reachable.append((candidate_count, candidate_source, candidate_commit))
            candidate_evidence.append({
                "source": candidate_source,
                "commit": candidate_commit,
                "reachable_from_head": True,
                "commits_stale": candidate_count,
                "excluded_reason": None,
            })
    if not reachable:
        return {
            "slug": slug,
            "memory_as_of_commit": memory_as_of_commit,
            "baseline_source": baseline_source,
            "repo_head": repo_head,
            "commits_stale": 0,
            "stale": False,
            "reason": f"could not count commits since {memory_as_of_commit[:8]} (shallow clone or unknown sha)",
            "baseline_candidates": candidate_evidence,
        }

    # The ledger and milestone append are separate durability steps. If ledger
    # telemetry degrades after the milestone lands, both candidates can exist
    # with the ledger older. Choose the freshest reachable evidence (fewest
    # commits behind), retaining candidate order as the deterministic tie-break.
    commits_stale, baseline_source, memory_as_of_commit = min(
        reachable,
        key=lambda candidate: candidate[0],
    )

    stale = commits_stale >= commits_threshold
    latest_run_evidence = _latest_run_evidence(workdir) if stale else None
    message = (
        f"{slug} memory is {commits_stale} commits behind HEAD — append a milestone/decision"
        if stale
        else f"{slug} memory current ({commits_stale} commits since last {baseline_source})"
    )

    return {
        "slug": slug,
        "memory_as_of_commit": memory_as_of_commit,
        "baseline_source": baseline_source,
        "repo_head": repo_head,
        "commits_stale": commits_stale,
        "stale": stale,
        "message": message,
        "latest_run_evidence": latest_run_evidence,
        "baseline_candidates": candidate_evidence,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect when project memory has drifted behind the repo's git HEAD."
    )
    parser.add_argument(
        "--workdir",
        required=True,
        help="Path to the git repository root.",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Project slug.  Defaults to the repo directory name.",
    )
    parser.add_argument(
        "--memory-root",
        default=None,
        help="Root of the build-loop-memory tree (default: resolved memory store root).",
    )
    parser.add_argument(
        "--commits-threshold",
        type=int,
        default=DEFAULT_COMMITS_THRESHOLD,
        help=f"Flag memory stale when commits_stale >= N (default {DEFAULT_COMMITS_THRESHOLD}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Emit JSON to stdout.",
    )
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    slug = args.project or workdir.name
    if args.memory_root:
        memory_root = Path(args.memory_root).resolve()
    else:
        from _paths import memory_store_root  # noqa: PLC0415
        memory_root = memory_store_root()

    result = check(
        workdir=workdir,
        slug=slug,
        memory_root=memory_root,
        commits_threshold=args.commits_threshold,
    )

    # Human summary → stderr.
    if result.get("stale"):
        print(
            f"[MEMORY STALE] {result['message']}",
            file=sys.stderr,
        )
    else:
        reason = result.get("reason") or result.get("message") or "memory current"
        print(f"[MEMORY OK] {reason}", file=sys.stderr)

    if args.output_json:
        print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
