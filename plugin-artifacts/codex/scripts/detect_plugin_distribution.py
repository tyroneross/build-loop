#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""detect_plugin_distribution.py — classify a plugin repo's distribution shape, then
recommend a version policy. DETECT, don't mandate.

WHY THIS EXISTS (named, observed failure — 2026-07-14)
-----------------------------------------------------
A blanket "omit `version` everywhere" rule was applied across an 18-plugin fleet. It was
RIGHT for 17 and WRONG for build-loop: build-loop installs from a *directory* source, so
Claude Code never resolves it from git — the version field is not its update key, and
removing it buys nothing while gutting its release machinery. The lesson is that the
distribution SHAPE decides the policy; a hard rule cannot.

TWO SEPARATE THINGS (do not conflate)
-------------------------------------
1. POLICY  — auto-SHA vs semver. DETECTED per repo (this script). Depends on the install
   source, which the repo does not get to choose unilaterally.
2. INVARIANT — whatever policy holds, the version state must be CONSISTENT across every
   surface (claude plugin.json / codex plugin.json / marketplace entry). A version set on
   only one surface silently masks the others (plugin.json wins). This is a HARD RULE and
   is enforced in CI by the shared `verify-plugin-manifests.yml` workflow.

Also orthogonal: `package.json` version. An npm-published repo ALWAYS keeps semver there —
npm requires it — regardless of the plugin's distribution policy. Both can coexist
(auto-SHA plugin + semver npm artifact), and 4 repos in the fleet do exactly that.

Usage:  detect_plugin_distribution.py <repo> [--hub <marketplace.json>] [--json]
Exit:   0 always (advisory). Emits a recommendation, never edits anything.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

GIT_SOURCES = {"github", "git", "git-subdir", "url"}


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _plugin_root(repo: str) -> str | None:
    """Plugin manifests at repo root, or under plugin/ (the app-companion layout)."""
    for cand in (".", "plugin"):
        if os.path.isfile(os.path.join(repo, cand, ".claude-plugin", "plugin.json")):
            return cand
    return None


def _install_sources(name: str, hub: str | None) -> dict:
    """Collect EVERY source this plugin is distributed from — never silently pick one.

    A plugin can be dual-sourced: a `directory` source locally (how the author runs it) AND
    a git source publicly (how everyone else installs it). Those imply OPPOSITE version
    policies, and picking one without saying so is how build-loop's pinned version was
    wrongly waved through (2026-07-14): the directory path made `version` look irrelevant,
    while the git path meant that same pinned version froze /plugin update for everyone else.
    """
    found: dict[str, str] = {}
    hub_d = _load(hub) if hub else None
    if isinstance(hub_d, dict):
        for entry in hub_d.get("plugins", []):
            if entry.get("name") == name:
                src = entry.get("source")
                found["hub"] = src.get("source") if isinstance(src, dict) else "path"
    st = _load(os.path.expanduser("~/.claude/settings.json"))
    if isinstance(st, dict):
        mkts = st.get("extraKnownMarketplaces")
        if isinstance(mkts, dict) and name in mkts:
            src = mkts[name].get("source", {})
            found["local"] = src.get("source") if isinstance(src, dict) else "path"
    return found


def detect(repo: str, hub: str | None = None) -> dict:
    root = _plugin_root(repo)
    if root is None:
        return {"repo": repo, "shape": "not-a-plugin",
                "rationale": "no .claude-plugin/plugin.json at repo root or plugin/"}

    base = os.path.join(repo, root)
    claude = _load(os.path.join(base, ".claude-plugin", "plugin.json")) or {}
    codex = _load(os.path.join(base, ".codex-plugin", "plugin.json"))
    mkt = _load(os.path.join(base, ".claude-plugin", "marketplace.json"))
    pkg = _load(os.path.join(base, "package.json")) or _load(os.path.join(repo, "package.json"))

    name = claude.get("name")
    sources = _install_sources(name, hub)
    src_vals = set(sources.values())

    # --- versions across every update-detection surface ---
    surfaces = {"claude plugin.json": claude.get("version")}
    if codex is not None:
        surfaces["codex plugin.json"] = codex.get("version")
    if isinstance(mkt, dict):
        for e in mkt.get("plugins", []):
            if e.get("name") == name:
                surfaces["self-marketplace entry"] = e.get("version")

    consistent = len({str(v) for v in surfaces.values()}) <= 1
    npm_published = bool(pkg and pkg.get("name") and not pkg.get("private"))

    git_srcs = src_vals & GIT_SOURCES
    has_dir = "directory" in src_vals or "path" in src_vals

    # --- SHAPE decides POLICY. Dual-source is a REAL state, not an edge case. ---
    if root == "plugin" and pkg and pkg.get("private"):
        shape, policy = "app-companion", "follow-app"
        why = ("plugin lives under plugin/ inside a private app repo — it ships with the app, "
               "so it follows the app's release cadence, not the fleet's")
    elif git_srcs and has_dir:
        shape, policy = "dual-sourced", "semver-but-must-bump"
        why = (f"distributed BOTH ways: {sources}. The directory source makes `version` look "
               "irrelevant (the host reads the local dir), but the git source means a pinned "
               "`version` FREEZES /plugin update for everyone who installs it publicly. "
               "So semver is viable ONLY if you actually bump on every release — otherwise "
               "switch to auto-SHA. DECIDE EXPLICITLY; do not infer from the local path alone")
    elif has_dir:
        shape, policy = "directory-sourced-tool", "semver"
        why = ("installed ONLY from a directory source — the host reads the local dir and never "
               "resolves from git, so `version` is not the update key. Omitting it buys nothing")
    elif git_srcs:
        shape, policy = "git-sourced-plugin", "auto-sha"
        why = (f"installed from a git source ({', '.join(sorted(git_srcs))}) — omitting `version` "
               "makes the host resolve to the commit SHA, so every push ships. A pinned version "
               "freezes /plugin update until someone remembers to bump (how the fleet drifted)")
    else:
        shape, policy = "unknown-source", "auto-sha"
        why = ("install source could not be determined; defaulting to the fleet convention "
               "(auto-SHA). VERIFY the marketplace entry before acting on this")

    actions = []
    declared = claude.get("version")
    if policy == "semver-but-must-bump":
        actions.append(f"CONFIRM the release process actually bumps `version` every release "
                       f"(currently {declared}). If bumps get skipped, public /plugin update silently "
                       f"freezes — switch to auto-SHA instead.")
    if policy == "auto-sha" and declared is not None:
        actions.append(f"omit `version` ({declared}) from every plugin surface (keep it in package.json)")
    if policy == "semver" and declared is None:
        actions.append("declare a semver `version` in plugin.json (this repo needs one)")
    if not consistent:
        actions.append(f"HARD RULE violated — version disagrees across surfaces: {surfaces}. "
                       "One surface masks the others; make them agree.")
    if npm_published:
        actions.append("package.json keeps semver regardless (npm requires it) — orthogonal to the plugin policy")

    return {
        "repo": os.path.basename(os.path.abspath(repo)),
        "plugin_name": name,
        "plugin_root": root,
        "install_sources": sources,
        "shape": shape,
        "recommended_version_policy": policy,
        "npm_published": npm_published,
        "surfaces": surfaces,
        "consistent": consistent,
        "rationale": why,
        "actions": actions,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect a plugin repo's distribution shape.")
    ap.add_argument("repo")
    ap.add_argument("--hub", help="path to the fleet marketplace.json")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    r = detect(a.repo, a.hub)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0

    print(f"  repo            {r['repo']}")
    if r["shape"] == "not-a-plugin":
        print(f"  shape           not-a-plugin — {r['rationale']}")
        return 0
    print(f"  shape           {r['shape']}  (source: {r["install_sources"] or "undetermined"})")
    print(f"  version policy  {r['recommended_version_policy']}")
    print(f"  consistent      {'yes' if r['consistent'] else 'NO — surfaces disagree'}")
    print(f"  why             {r['rationale']}")
    for act in r["actions"]:
        print(f"  → {act}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
