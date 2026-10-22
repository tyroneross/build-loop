#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""branch_disposition.py — resolve local branches whose work is already on main.

The weekly repo-hygiene card used to file a "decide branch <b>" card for every
unmerged branch; 9 of 10 sampled were already on main (cherry-picked or
squash-merged) and needed no owner. This script classifies each local branch
and, with --apply, closes the ones git can prove are redundant:

  merged           branch head is an ancestor of base       -> delete (git branch -d)
  content-on-main  merging b into base changes nothing       -> archive tag + delete
                   (`git merge-tree --write-tree` == base tree; catches squash merges)
  patch-equivalent `git cherry base b` has no "+" commits, b has no merge commits
                   (cherry cannot see their resolutions), and base later edited
                   those lines (merge conflicts)               -> archive tag + delete.
                   If the merge is clean but would re-add content, base removed it
                   on purpose (revert): that is needs-decision, not redundancy.
  needs-decision   b carries content base does not           -> report commits + diffstat
  skipped          protected name, checked out in a worktree, or a queue-card branch

Only `needs-decision` branches can become owner cards, and only after the agent
checks they hold a product or taste call (see references/safety-protocol.md §7a).

Safety: never touches a branch checked out in any worktree (re-checked right
before each delete), base/main/master/develop/release/*/archive/*, or remote
refs. Archive tags (`archive/pre-closeout-<date>/<branch>`, annotated) are
created and verified BEFORE the delete. The delete is one ref transaction that
locks the branch at the classified head and checks worktrees while the lock is
held, so a branch that moved or got checked out since classification survives.

CLI: branch_disposition.py --repo <path> [--base main] [--apply] [--include-queue-branches]
Output: JSON {repo, base, applied, branches:[{name, head, disposition, reason, action, ...}]}
Exit: 0 ok, 1 git error (bad repo/base), 2 bad arguments.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path

PROTECTED = {"main", "master", "develop", "trunk"}
PROTECTED_PREFIXES = ("release/", "archive/")
QUEUE_PREFIXES = ("oc/",)  # task-queue card branches: the queue's landing check owns them
RESOLVABLE = ("patch-equivalent", "content-on-main")


def _env() -> dict:
    # A hook or a parent git process can export GIT_DIR/GIT_INDEX_FILE; every call
    # here targets --repo explicitly, so drop them rather than act on another repo.
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=_env())
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r


def checked_out_branches(repo: Path) -> set[str]:
    out = git(repo, "worktree", "list", "--porcelain").stdout
    return {line.split(" ", 1)[1].removeprefix("refs/heads/")
            for line in out.splitlines() if line.startswith("branch ")}


def classify(repo: Path, base: str, name: str, head: str, checked_out: set[str],
             include_queue: bool = False) -> dict:
    item = {"name": name, "head": head}
    if name == base or name in PROTECTED or name.startswith(PROTECTED_PREFIXES):
        return {**item, "disposition": "skipped", "reason": "protected name"}
    if name in checked_out:
        return {**item, "disposition": "skipped", "reason": "checked out in a worktree"}
    if name.startswith(QUEUE_PREFIXES) and not include_queue:
        return {**item, "disposition": "skipped", "reason": "task-queue card branch (landing check owns it)"}
    if git(repo, "merge-base", "--is-ancestor", head, base, check=False).returncode == 0:
        return {**item, "disposition": "merged", "reason": f"head is an ancestor of {base}"}
    base_tree = git(repo, "rev-parse", f"{base}^{{tree}}").stdout.strip()
    mt = git(repo, "merge-tree", "--write-tree", base, head, check=False)
    if mt.returncode not in (0, 1):  # 1 = conflicts; anything else = git could not answer
        return {**item, "disposition": "skipped",
                "reason": f"git merge-tree failed (exit {mt.returncode}): {mt.stderr.strip()[:160]}"}
    merged_tree = mt.stdout.split("\n", 1)[0].strip() if mt.returncode == 0 else None
    if merged_tree == base_tree:
        return {**item, "disposition": "content-on-main",
                "reason": f"merging into {base} changes no file (squash-merged or re-implemented identically)"}
    cherry = git(repo, "cherry", base, head, check=False)
    # `git cherry` ignores merge commits, so a branch-only merge (with its own
    # conflict resolution) has no patch-id proof: never call such a branch picked.
    merges = git(repo, "rev-list", "--merges", f"{base}..{head}", check=False).stdout.strip()
    all_picked = (cherry.returncode == 0 and not merges
                  and not any(l.startswith("+") for l in cherry.stdout.splitlines()))
    # A clean merge that still changes base means base no longer holds this content
    # (picked, then reverted or deleted on purpose): that is a decision, not redundancy.
    if all_picked and merged_tree is None:
        return {**item, "disposition": "patch-equivalent",
                "reason": f"every commit has a patch-id twin on {base} (git cherry); {base} has since edited those lines"}
    if all_picked:
        reason = f"commits were applied to {base} but {base} has since removed that content"
        return {**item, "disposition": "needs-decision", "reason": reason,
                "commits": git(repo, "log", "--oneline", "-n", "20", f"{base}..{head}").stdout.splitlines(),
                "diffstat": ""}
    log = git(repo, "log", "--oneline", "-n", "20", f"{base}..{head}").stdout.splitlines()
    stat = git(repo, "diff", "--stat", f"{base}...{head}", check=False).stdout.strip().splitlines()
    return {**item, "disposition": "needs-decision",
            "reason": "branch carries content not on " + base,
            "commits": log, "diffstat": stat[-1] if stat else ""}


def resolve(repo: Path, item: dict, today: str) -> dict:
    """Archive (if needed) and delete one resolvable branch. Returns the item with `action`."""
    name, head = item["name"], item["head"]
    if name in checked_out_branches(repo):  # re-check: a session may have checked it out since
        return {**item, "action": "kept: checked out since classification"}
    if item["disposition"] in RESOLVABLE:
        tag = f"archive/pre-closeout-{today}/{name}"
        existing = git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}", check=False)
        if existing.returncode != 0:
            git(repo, "tag", "-a", tag, head, "-m",
                f"repo-hygiene: {name} archived before delete ({item['disposition']}: {item['reason']})")
        elif existing.stdout.strip() != head:
            return {**item, "action": f"kept: tag {tag} already points at another commit"}
        if git(repo, "rev-parse", f"refs/tags/{tag}^{{commit}}").stdout.strip() != head:
            return {**item, "action": f"kept: archive tag {tag} did not verify"}
        item = {**item, "archive_tag": tag}
    return {**item, "action": delete_branch_at(repo, name, head)}


def delete_branch_at(repo: Path, name: str, head: str) -> str:
    """Delete refs/heads/<name> only if it still points at `head` and no worktree has it.

    One `git update-ref --stdin` transaction: `prepare` locks the ref and verifies the
    expected id (so no commit or ref update can land meanwhile), the worktree check
    runs WHILE the lock is held, then `commit` or `abort`.
    """
    proc = subprocess.Popen(["git", "-C", str(repo), "update-ref", "--stdin"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env())
    assert proc.stdin and proc.stdout

    def say(line: str) -> str:
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
        return proc.stdout.readline().strip()

    try:
        if say("start") != "start: ok":
            return "kept: ref transaction did not start"
        proc.stdin.write(f"delete refs/heads/{name} {head}\n")
        if say("prepare") != "prepare: ok":
            return "kept: branch moved since classification (ref lock or id check failed)"
        if name in checked_out_branches(repo):
            say("abort")
            return "kept: checked out since classification"
        if say("commit") != "commit: ok":
            return "kept: delete did not commit"
        # A ref lock does not block checkout. If a session checked the branch out
        # between the locked check and the commit, put the ref back at once.
        if name in checked_out_branches(repo):
            git(repo, "update-ref", f"refs/heads/{name}", head, "0" * 40, check=False)
            return "kept: checked out during delete; ref restored"
        return "deleted"
    except (BrokenPipeError, OSError):
        return "kept: " + (proc.stderr.read().strip()[:160] if proc.stderr else "ref transaction failed")
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.wait(timeout=10)


def run(repo: Path, base: str = "main", apply: bool = False, include_queue: bool = False,
        today: str | None = None) -> dict:
    today = today or _dt.date.today().isoformat()
    git(repo, "rev-parse", "--verify", f"{base}^{{commit}}")
    checked = checked_out_branches(repo)
    refs = git(repo, "for-each-ref", "--format=%(refname:short)%00%(objectname)", "refs/heads").stdout
    items = []
    for line in refs.splitlines():
        name, head = line.split("\0")
        item = classify(repo, base, name, head, checked, include_queue)
        if apply and item["disposition"] in ("merged", *RESOLVABLE):
            item = resolve(repo, item, today)
        items.append(item)
    return {"repo": str(repo), "base": base, "applied": apply, "branches": items}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True)
    p.add_argument("--base", default="main")
    p.add_argument("--apply", action="store_true", help="archive + delete resolvable branches")
    p.add_argument("--include-queue-branches", action="store_true", dest="include_queue")
    args = p.parse_args(argv)
    try:
        out = run(Path(args.repo).expanduser().resolve(), args.base, args.apply, args.include_queue)
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
