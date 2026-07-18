#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Verify a dispatched isolation worktree actually resolves to the INTENDED target repo.

Background
----------
When a build-loop:build-orchestrator run is dispatched with the Agent tool's
`isolation: "worktree"` and the dispatch brief names a target repo that
differs from the harness SESSION repo, the harness has been observed to
provision the worktree against the SESSION repo instead of the intended one
(memory: feedback_agent_worktree_targets_session_repo.md; recurred twice).
This script turns that doc-only workaround into a preflight CHECK: given the
intended repo and the actual worktree/cwd the agent is running in, assert
both resolve to the SAME git repository, and on mismatch emit the exact
`git worktree add` command to self-correct in the intended repo.

Key subtlety
------------
A legitimate `git worktree` of the intended repo has a DIFFERENT toplevel
than the main checkout but the SAME `git rev-parse --git-common-dir`. That
must match, not false-positive as a mismatch. Falls back to comparing
normalized `owner/repo` from `git remote get-url origin` when common-dir
alone is inconclusive (e.g. two independent clones of the same repo, no
shared .git directory).

Public API
----------
resolve_git_identity(path) -> dict | None
    {"toplevel", "common_dir", "origin", "origin_norm"} for a path inside a
    git repo, or None if the path is not inside a git repo / git failed.

normalize_origin(url) -> str | None
    Normalize a git remote URL (SSH or HTTPS) to lowercase "owner/repo".

decide_match(intended, actual) -> (bool, str)
    "same repo" decision per the identity dicts above; returns (match, reason).

build_provision_cmd(intended_toplevel, slug) -> str
    The `git worktree add` command to provision a correct worktree of the
    intended repo, using worktree_guard's canonical path/branch helpers.

CLI
---
  verify_worktree_target.py --intended-workdir PATH [--actual-workdir PATH] [--slug SLUG] [--json]

Exit codes: 0 = match, 1 = mismatch, 2 = a given path is not inside a git repo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Ensure the sibling `worktree_guard` module resolves whether this file is
# invoked as a script (python3 verify_worktree_target.py ...) or imported
# directly. Under pytest, the colocated test's own directory is already on
# sys.path via rootdir-insertion (see scripts/test_worktree_guard.py), so
# this mirrors that for the non-pytest / CLI entry point.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import worktree_guard  # noqa: E402


def _run_git(args: list[str], path: str) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", "-C", path, *args],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _git_toplevel(path: str) -> Optional[str]:
    rc, out, _ = _run_git(["rev-parse", "--show-toplevel"], path)
    return out or None if rc == 0 else None


def _git_common_dir(path: str) -> Optional[str]:
    rc, out, _ = _run_git(["rev-parse", "--git-common-dir"], path)
    if rc != 0 or not out:
        return None
    common = Path(out)
    if not common.is_absolute():
        common = Path(path) / common
    return str(common.resolve())


def _git_origin_url(path: str) -> Optional[str]:
    rc, out, _ = _run_git(["remote", "get-url", "origin"], path)
    return out or None if rc == 0 else None


def normalize_origin(url: str | None) -> Optional[str]:
    """Normalize a git origin URL to lowercase 'owner/repo'.

    Handles both `git@github.com:owner/repo.git` (scp-like) and
    `https://github.com/owner/repo.git` (URL) forms, and any host, not
    only github.com.
    """
    if not url:
        return None
    cleaned = url.strip()
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    cleaned = cleaned.rstrip("/")
    segments = [seg for seg in re.split(r"[/:]", cleaned) if seg]
    if len(segments) < 2:
        return None
    owner, repo = segments[-2], segments[-1]
    return f"{owner}/{repo}".lower()


def resolve_git_identity(path: str) -> Optional[dict[str, Optional[str]]]:
    """Resolve the git identity of ``path``: toplevel, common_dir, origin.

    Returns None if ``path`` is not inside a git repository (or git itself
    is unavailable) — the caller treats that as an error, not a mismatch.
    """
    toplevel = _git_toplevel(path)
    if toplevel is None:
        return None
    origin = _git_origin_url(path)
    return {
        "toplevel": toplevel,
        "common_dir": _git_common_dir(path),
        "origin": origin,
        "origin_norm": normalize_origin(origin),
    }


def decide_match(
    intended: dict[str, Optional[str]], actual: dict[str, Optional[str]]
) -> tuple[bool, str]:
    """"Same repo" decision, in order: common-dir, then normalized origin."""
    if intended["common_dir"] and actual["common_dir"] and intended["common_dir"] == actual["common_dir"]:
        return True, "same git-common-dir (actual is a worktree of the intended repo)"
    if intended["origin_norm"] and actual["origin_norm"] and intended["origin_norm"] == actual["origin_norm"]:
        return True, "same normalized origin owner/repo"
    return False, "different repo identity: no shared git-common-dir and no matching origin"


def _derive_slug(intended_toplevel: str) -> str:
    """Derive a slug from the intended repo's basename plus a short timestamp."""
    basename = Path(intended_toplevel).name
    ts = time.strftime("%Y%m%dT%H%M%S")
    return f"{basename}-{ts}"


def build_provision_cmd(intended_toplevel: str, slug: str) -> str:
    """The exact `git worktree add` command to provision the INTENDED repo.

    DRY: reuses worktree_guard's canonical path/branch helpers so a
    self-corrected worktree lands in the same canonical location any other
    build-loop-provisioned worktree would.
    """
    wt_path = worktree_guard.canonical_worktree_path(Path(intended_toplevel), slug)
    branch = worktree_guard.canonical_branch_name(slug)
    return f"git -C {intended_toplevel} worktree add {wt_path} -b {branch}"


def verify(intended_workdir: str, actual_workdir: str, slug: str | None) -> dict[str, Any]:
    """Core check. Returns the result dict; does not touch exit codes or I/O."""
    intended_path = str(Path(intended_workdir).resolve())
    actual_path = str(Path(actual_workdir).resolve())

    intended = resolve_git_identity(intended_path)
    actual = resolve_git_identity(actual_path)

    if intended is None or actual is None:
        bad_label = "intended-workdir" if intended is None else "actual-workdir"
        bad_path = intended_path if intended is None else actual_path
        return {
            "error": f"{bad_label} is not inside a git repository (or git is unavailable): {bad_path}",
            "match": False,
        }

    match, reason = decide_match(intended, actual)
    intended_repo = intended["origin_norm"] or Path(intended["toplevel"]).name
    actual_repo = actual["origin_norm"] or Path(actual["toplevel"]).name

    correct_provision_cmd = None
    if not match:
        resolved_slug = slug or _derive_slug(intended["toplevel"])
        correct_provision_cmd = build_provision_cmd(intended["toplevel"], resolved_slug)

    return {
        "match": match,
        "intended_repo": intended_repo,
        "actual_repo": actual_repo,
        "intended_toplevel": intended["toplevel"],
        "actual_toplevel": actual["toplevel"],
        "correct_provision_cmd": correct_provision_cmd,
        "reason": reason,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assert an isolation worktree/cwd resolves to the intended target repo."
    )
    parser.add_argument(
        "--intended-workdir",
        required=True,
        help="The repo the dispatched work is SUPPOSED to edit.",
    )
    parser.add_argument(
        "--actual-workdir",
        default=None,
        help="The worktree/cwd the agent is actually in (default: cwd).",
    )
    parser.add_argument(
        "--slug",
        default=None,
        help="Label for the self-correction worktree path/branch (default: derived).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Pretty-print JSON (default: compact JSON).",
    )
    args = parser.parse_args(argv)

    actual_workdir = args.actual_workdir or os.getcwd()
    result = verify(args.intended_workdir, actual_workdir, args.slug)

    indent = 2 if args.json else None
    print(json.dumps(result, indent=indent))

    if "error" in result:
        return 2
    return 0 if result["match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
