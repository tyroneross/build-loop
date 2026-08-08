#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""run_provenance — corroborate a run record's commit + goal before it is written.

`append_run` used to trust whatever commit and goal the caller handed it. On
2026-07-09 that produced a `state.json` `runs[]` entry whose commit (`6616b71`)
was reachable from neither the run's push range nor its branch, and whose goal
text described a different piece of work. Nothing rejected it, so Phase 6 Learn
and every downstream auditor read a confidently-wrong provenance record.

Two checks, deliberately asymmetric in strength:

1. COMMIT — a supplied SHA must be reachable from the run's push range (or,
   absent a push range, from HEAD). Unreachable is a **block**: the caller's SHA
   is refused. An absent / empty / `pending` commit is fine — a mid-run append
   before the push has nothing to corroborate yet, and `pending` is honest.
2. GOAL — the supplied goal text is compared against the run's `intent.md`
   headline by difflib similarity. A low match is a **warn**, never a block:
   a goal can legitimately be phrased differently from its intent headline, and
   a gate that fires on paraphrase gets disabled.

Ported from agent-rally-point `scripts/append_run_provenance.py` (enforce-candidate
E3), which is where the logic was first implemented and tested. Build-loop keeps
its own copy rather than importing across repos so the check survives on a host
that has no agent-rally-point checkout. Two deliberate deviations from that
source, both to fit build-loop's own data:

- Reachability from HEAD uses `git merge-base --is-ancestor` instead of
  enumerating `git rev-list HEAD`. Same answer; build-loop's own history is
  1200+ commits and this runs on every run-record write.
- The goal comparison is skipped when `intent.md` carries an `intent_run_id`
  marker naming a DIFFERENT run. Build-loop leaves the previous run's intent on
  disk, so comparing against it would warn on nearly every run — noise that
  would train readers to ignore the finding.

Library use is the point (`validate_run_provenance`); the CLI exists for manual
triage of a record already on disk.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# `## Intent — <goal>` / `# Intent: <goal>` — the boilerplate label is not part
# of the goal, and leaving it in drags every similarity ratio down uniformly.
_INTENT_LABEL = re.compile(r"^intent\s*[—–:-]\s*", re.IGNORECASE)
_INTENT_RUN_ID = re.compile(r"intent_run_id:\s*(\S+)")
_PENDING_COMMITS = {"", "pending"}


def _run_git(repo_root: str, args: list) -> Optional[str]:
    """Run a git command, returning stdout text or None on any failure.

    Never raises: subprocess/OS errors, non-zero exit codes, and a missing git
    binary are all "could not determine", so callers can fail closed.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_root] + args,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _git_succeeds(repo_root: str, args: list) -> bool:
    """True only when the git command exits 0. Used for predicate subcommands."""
    return _run_git(repo_root, args) is not None


def _extract_headline(path: str) -> Optional[str]:
    """First markdown headline (else first non-empty line), label prefix stripped."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    first_nonempty = None
    headline = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<!--"):  # provenance markers are not the headline
            continue
        if first_nonempty is None:
            first_nonempty = stripped
        if stripped.startswith("#"):
            headline = stripped.lstrip("#").strip()
            break
    text = headline if headline is not None else first_nonempty
    return _INTENT_LABEL.sub("", text).strip() if text else text


def _file_run_id(path: str) -> Optional[str]:
    """The `intent_run_id:` marker build-loop stamps into `.build-loop/intent.md`."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            head = fh.read(2048)
    except OSError:
        return None
    match = _INTENT_RUN_ID.search(head)
    return match.group(1).strip() if match else None


def resolve_intent_path(workdir) -> Optional[str]:
    """`<workdir>/.build-loop/intent.md` when it exists, else None.

    `goal.md` is deliberately NOT a fallback: its headline is the fixed string
    "Goal criteria (F-criteria)", which matches no real goal and would warn on
    every run.
    """
    candidate = Path(workdir) / ".build-loop" / "intent.md"
    return str(candidate) if candidate.is_file() else None


def _commit_reachable(
    repo_root: str, commit: str, push_range: Optional[str]
) -> bool:
    if push_range:
        # The caller asserted a range; membership in it is the whole claim. An
        # empty-but-valid range means nothing shipped, so nothing is reachable.
        output = _run_git(repo_root, ["rev-list", push_range])
        if output is not None:
            for rev in output.split():
                if rev == commit or rev.startswith(commit) or commit.startswith(rev):
                    return True
            return False
        # An unusable range (bad refs, detached rebase) falls through to HEAD
        # rather than blocking on the caller's bookkeeping.
    return _git_succeeds(repo_root, ["merge-base", "--is-ancestor", commit, "HEAD"])


def validate_run_provenance(
    *,
    run_id: str,
    commit: Optional[str],
    goal: str,
    repo_root: str,
    push_range: Optional[str] = None,
    intent_path: Optional[str] = None,
    plan_path: Optional[str] = None,
    similarity_threshold: float = 0.5,
) -> dict:
    """Check that `commit` and `goal` are corroborated before an append_run write.

    Returns `{"ok": bool, "findings": [...], "derived_commit": str|None}`.
    `ok` is False only when a `block`-severity finding fired.
    """
    findings = []

    derived_commit = _run_git(repo_root, ["rev-parse", "HEAD"])
    if derived_commit is not None:
        derived_commit = derived_commit.strip()

    supplied = (commit or "").strip()
    if supplied.lower() not in _PENDING_COMMITS and not _commit_reachable(
        repo_root, supplied, push_range
    ):
        findings.append(
            {
                "code": "commit_unreachable",
                "severity": "block",
                "detail": (
                    f"commit {supplied!r} for run {run_id!r} is not reachable from "
                    f"{'push_range ' + push_range if push_range else 'HEAD'}"
                ),
            }
        )

    source_path = intent_path or plan_path
    if source_path:
        source_run_id = _file_run_id(source_path)
        if source_run_id and run_id and source_run_id != run_id:
            source_path = None  # a previous run's intent is not this run's claim

    headline = _extract_headline(source_path) if source_path else None
    if headline and goal:
        ratio = difflib.SequenceMatcher(None, goal.lower(), headline.lower()).ratio()
        if ratio < similarity_threshold:
            findings.append(
                {
                    "code": "goal_mismatch",
                    "severity": "warn",
                    "detail": (
                        f"goal {goal!r} has similarity {ratio:.2f} (< "
                        f"{similarity_threshold}) vs headline {headline!r} in "
                        f"{source_path!r}"
                    ),
                }
            )

    ok = not any(f["severity"] == "block" for f in findings)
    return {"ok": ok, "findings": findings, "derived_commit": derived_commit}


def format_findings(findings: list) -> list:
    """Human-readable `[BLOCK]/[WARN] code: detail` lines, in severity order."""
    order = {"block": 0, "warn": 1}
    ranked = sorted(findings, key=lambda f: order.get(f.get("severity"), 2))
    return [
        f"  [{str(f.get('severity', '?')).upper()}] {f.get('code')}: {f.get('detail')}"
        for f in ranked
    ]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate commit/goal provenance for an append_run write.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--goal", default="")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--push-range", default=None)
    parser.add_argument("--intent", default=None, dest="intent_path")
    parser.add_argument("--plan", default=None, dest="plan_path")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    try:
        result = validate_run_provenance(
            run_id=args.run_id,
            commit=args.commit,
            goal=args.goal,
            repo_root=args.repo_root,
            push_range=args.push_range,
            intent_path=args.intent_path or resolve_intent_path(args.repo_root),
            plan_path=args.plan_path,
            similarity_threshold=args.threshold,
        )
    except Exception as exc:  # noqa: BLE001 — report the error, never traceback
        print(f"run_provenance: usage/IO error: {exc}", file=sys.stderr)
        return 2

    print(f"run_provenance: run_id={args.run_id} ok={result['ok']}", file=sys.stderr)
    for line in format_findings(result["findings"]):
        print(line, file=sys.stderr)
    if args.json:
        print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
