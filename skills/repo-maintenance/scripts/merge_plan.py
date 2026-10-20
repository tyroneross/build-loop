#!/usr/bin/env python3
"""Read-only merge planner: order and batch several branches before merging.

Reports per-branch commits ahead, changed files and a clean/conflict verdict
against base, then per-pair file overlap and conflict. Emits ordered batches of
at most N branches that share no files, do not conflict, and respect ancestry.
Runs no merges and changes no refs, worktrees or the index. (`git merge-tree
--write-tree` may add unreferenced objects to the object database; nothing
points at them and `git gc` prunes them.)

Output: JSON first, then a short human summary (summary omitted with --json).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from itertools import combinations

GIT_TIMEOUT = 60


def git(repo, *args, check=False):
    try:
        p = subprocess.run(["git", "-C", repo, *args], text=True, capture_output=True,
                           timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "timeout")
    if check and p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p


def resolve_base(repo, base):
    if base:
        return base
    up = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if up.returncode == 0 and up.stdout.strip():
        return up.stdout.strip()
    for cand in ("main", "master"):
        if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{cand}").returncode == 0:
            return cand
    raise SystemExit("cannot determine base; pass --base")


def merge_verdict(repo, *args):
    """Return (verdict, detail). Exit 0 clean, 1 conflict, else unknown."""
    p = git(repo, "merge-tree", "--write-tree", *args)
    if p.returncode == 0:
        return "clean", []
    if p.returncode == 1:
        files = sorted({ln.split("\t", 1)[1] for ln in p.stdout.splitlines()
                        if "\t" in ln and ln[:6].isdigit()})
        return "conflict", files
    return "unknown", [p.stderr.strip()[:200]]


def discover(repo, base, match):
    out = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/", check=True)
    names = []
    for n in out.stdout.split():
        if n == base or (match and not fnmatch.fnmatch(n, match)):
            continue
        n_ahead = git(repo, "rev-list", "--count", f"{base}..{n}").stdout.strip()
        if n_ahead.isdigit() and int(n_ahead) > 0:
            names.append(n)
    return names


def plan(repo, base, branches, batch_size):
    info, needs = {}, []
    for b in branches:
        if git(repo, "rev-parse", "--verify", "--quiet", f"{b}^{{commit}}").returncode != 0:
            needs.append({"branch": b, "reason": "ref not found"})
            continue
        ahead = int(git(repo, "rev-list", "--count", f"{base}..{b}").stdout.strip() or 0)
        files = sorted(filter(None, git(repo, "diff", "--name-only", "--no-renames",
                                        f"{base}...{b}").stdout.splitlines()))
        verdict, detail = merge_verdict(repo, base, b)
        info[b] = {"commits_ahead": ahead, "files": files, "base_verdict": verdict,
                   "base_conflict_files": detail if verdict == "conflict" else []}
        if verdict != "clean":
            reason = ("conflicts with base" if verdict == "conflict"
                      else "conflict probe unavailable: " + "; ".join(detail))
            needs.append({"branch": b, "reason": reason, "files": info[b]["base_conflict_files"]})
    ready = [b for b in branches if b in info and info[b]["base_verdict"] == "clean"]
    bad = {n["branch"] for n in needs}

    def is_anc(a, b):  # a's tip is ancestor of b's tip
        return git(repo, "merge-base", "--is-ancestor", a, b).returncode == 0

    tip = {b: git(repo, "rev-parse", b).stdout.strip() for b in ready}
    deps = {b: set() for b in ready}
    for a, b in combinations(ready, 2):
        if tip[a] == tip[b]:
            deps[b].add(a)  # identical tips: keep stable order, second after first
        elif is_anc(a, b):
            deps[b].add(a)
        elif is_anc(b, a):
            deps[a].add(b)
    # A branch whose prerequisite needs resolution cannot land before it.
    pairs = []
    incompatible = set()
    for a, b in combinations(ready, 2):
        related = b in deps[a] or a in deps[b]
        overlap = sorted(set(info[a]["files"]) & set(info[b]["files"]))
        verdict, detail = ("related", []) if related else merge_verdict(
            repo, "--merge-base", base, a, b)
        pairs.append({"a": a, "b": b, "shared_files": overlap, "ancestry": related,
                      "conflict": verdict, "conflict_files": detail if verdict == "conflict" else []})
        if not related and (overlap or verdict != "clean"):
            incompatible.add(frozenset((a, b)))

    batches, placed, remaining = [], set(), list(ready)
    while remaining:
        cur = []
        for b in remaining:
            if not deps[b] <= placed or len(cur) >= batch_size:
                continue
            if any(frozenset((b, c)) in incompatible for c in cur):
                continue
            cur.append(b)
        if not cur:  # defensive; ancestry is acyclic so this should not happen
            cur = [remaining[0]]
        batches.append(cur)
        placed.update(cur)
        remaining = [b for b in remaining if b not in placed]
    return {
        "repo": repo, "base": base, "batch_size": batch_size,
        "branches": info, "pairs": pairs,
        "batches": [{"order": i + 1, "branches": c} for i, c in enumerate(batches)],
        "needs_resolution": needs,
    }


def summary(r):
    lines = [f"Merge plan against {r['base']} (batch size {r['batch_size']})"]
    for b in r["batches"]:
        lines.append(f"  batch {b['order']}: {', '.join(b['branches'])}")
    for n in r["needs_resolution"]:
        lines.append(f"  needs resolution: {n['branch']} ({n['reason']})")
    if not r["batches"] and not r["needs_resolution"]:
        lines.append("  nothing to merge")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base")
    ap.add_argument("branches", nargs="*", help="branches to plan")
    ap.add_argument("--auto", action="store_true", help="local branches ahead of base")
    ap.add_argument("--match", help="glob filter for --auto")
    ap.add_argument("--batch", type=int, default=3)
    ap.add_argument("--json", action="store_true", help="JSON only, no summary")
    a = ap.parse_args(argv)
    if a.batch < 1:
        ap.error("--batch must be >= 1")
    if not a.auto and not a.branches:
        ap.error("give branches or --auto")
    top = git(a.repo, "rev-parse", "--show-toplevel", check=True).stdout.strip()
    base = resolve_base(top, a.base)
    branches = list(dict.fromkeys(a.branches))
    if a.auto:
        branches += [b for b in discover(top, base, a.match) if b not in branches]
    report = plan(top, base, branches, a.batch)
    print(json.dumps(report, indent=2))
    if not a.json:
        print("\n" + summary(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
