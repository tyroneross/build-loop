#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Pre-push STAGE 3 — the version number is coherent before it leaves the machine.

WHICH RULE APPLIES DEPENDS ON WHO OWNS THE VERSION
--------------------------------------------------
Two release models, and this gate detects which one the repo is running:

RELEASE-PLEASE MODEL (this repo, since 2026-09-02). release-please owns the
version. It bumps package.json, the plugin manifests, and its own manifest
together inside the release PR, and the weekly cut merges that PR. A push is a
COMMIT, not a release, so demanding a bump per push would be wrong twice over:
it would force a hand-edit of a field an automation owns, and it would desync
`.release-please-manifest.json` from package.json, which is the one thing that
makes release-please compute the next version from the wrong base. What this
gate enforces here instead is that the three version fields AGREE — the failure
that actually breaks the automation.

MANUAL MODEL (a repo with no release-please config). A push is the unit of
release: the manifest number moves exactly once per push, and fifty commits and
one commit both cost one patch bump. Unchanged, so this file stays portable.

WHY A GATE AND NOT AN AUTO-BUMP
-------------------------------
A hook that rewrote the manifest mid-push would change commits git has already
computed refs for, and the bump would land in a commit nobody reviewed. So this
BLOCKS with the exact command instead, matching stages 1 and 2, which also
block and never mutate.

WHY THE VERSION IS COMPARED AGAINST ORIGIN, NOT AGAINST A TAG
-------------------------------------------------------------
Tags in this repo stop at v0.36.4 while the manifest reads 0.39.0 — three
releases were never tagged. Trusting tags would let those three push again with
no bump. The remote's own copy of plugin.json cannot drift from what origin
actually has.

CONTRACT (per `man githooks`): evaluate() receives the repo path, the raw stdin
lines, and the environment. Returns {"action": "allow"|"block", ...}. Never
raises — an internal error must not wedge pushing, same as stages 1 and 2.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

MANIFEST = ".claude-plugin/plugin.json"
RP_CONFIG = "release-please-config.json"
RP_MANIFEST = ".release-please-manifest.json"
PACKAGE_JSON = "package.json"
ZERO = "0" * 40
BYPASS_ENV = ("BUILD_LOOP_HOOKS", "BUILD_LOOP_SKIP_VERSION_GATE")


def _git(args: list[str], repo: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _semver(v: str | None) -> tuple[int, int, int] | None:
    if not isinstance(v, str):
        return None
    parts = v.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


def version_at(repo: Path, ref: str) -> str | None:
    """Manifest version as of one ref. None when absent or unparseable."""
    blob = _git(["show", f"{ref}:{MANIFEST}"], repo)
    if not blob:
        return None
    try:
        return json.loads(blob).get("version")
    except json.JSONDecodeError:
        return None


def local_version(repo: Path) -> str | None:
    p = repo / MANIFEST
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("version")
    except (OSError, json.JSONDecodeError):
        return None


def release_please_owns_versioning(repo: Path) -> bool:
    """Both config files present means an automation, not a human, moves the number."""
    return (repo / RP_CONFIG).is_file() and (repo / RP_MANIFEST).is_file()


def _json_field(repo: Path, relpath: str, *keys: str):
    try:
        node = json.loads((repo / relpath).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, str) else None


def evaluate_release_please(repo: Path) -> dict:
    """The three fields release-please keeps in lockstep must be in lockstep.

    Desync is silent and expensive: release-please reads its own manifest as
    "the last released version", so a manifest behind package.json makes it
    propose a version npm already has (publish rejected), and a manifest ahead
    makes it skip a number with no record of what was in it.
    """
    fields = {
        RP_MANIFEST: _json_field(repo, RP_MANIFEST, "."),
        PACKAGE_JSON: _json_field(repo, PACKAGE_JSON, "version"),
        MANIFEST: _json_field(repo, MANIFEST, "version"),
    }
    readable = {k: v for k, v in fields.items() if v is not None}
    if len(readable) < 2:
        return {"action": "allow", "reason": "release-please version fields unreadable"}
    if len(set(readable.values())) == 1:
        return {
            "action": "allow",
            "reason": f"release-please owns the version; all fields agree at {next(iter(readable.values()))}",
        }
    return {"action": "block", "exit_code": 1, "mode": "release-please", "fields": fields}


def evaluate(repo: Path, stdin_lines: list[str], env: dict) -> dict:
    for key in BYPASS_ENV:
        if str(env.get(key, "")).lower() in ("off", "0", "false", "skip", "1", "yes", "true"):
            # BUILD_LOOP_HOOKS=off is the repo-wide escape the other stages
            # honour; the dedicated var exists so this gate can be dropped
            # without disarming the test and deploy gates too.
            if key == "BUILD_LOOP_HOOKS" and str(env.get(key)).lower() != "off":
                continue
            return {"action": "allow", "reason": f"bypassed via {key}"}

    if release_please_owns_versioning(repo):
        return evaluate_release_please(repo)

    current = local_version(repo)
    if current is None or _semver(current) is None:
        return {"action": "allow", "reason": "no parseable manifest version"}

    for line in stdin_lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, _local_sha, remote_ref, remote_sha = parts
        if not remote_ref.startswith("refs/heads/"):
            continue                      # tags and notes are not releases
        if remote_sha == ZERO:
            continue                      # brand-new branch: nothing to compare
        if local_ref == "(delete)":
            continue

        published = version_at(repo, remote_sha)
        pub, cur = _semver(published), _semver(current)
        if pub is None or cur is None:
            continue                      # unreadable remote manifest: do not block
        if cur <= pub:
            return {
                "action": "block",
                "exit_code": 1,
                "current": current,
                "published": published,
                "remote_ref": remote_ref,
            }
    return {"action": "allow", "reason": "version advanced past origin"}


def format_block_message(verdict: dict) -> str:
    if verdict.get("mode") == "release-please":
        rows = "\n".join(
            f"    {path:<32} {value or '(unreadable)'}"
            for path, value in verdict.get("fields", {}).items()
        )
        return (
            "\n"
            "  PUSH BLOCKED — the version fields release-please keeps in lockstep disagree.\n"
            "\n"
            f"{rows}\n"
            "\n"
            "  release-please reads .release-please-manifest.json as the LAST RELEASED\n"
            "  version. Behind package.json it proposes a version npm already has and the\n"
            "  publish is rejected; ahead of it, a version number is skipped silently.\n"
            "\n"
            "  Let the release PR set all three, or sync them deliberately:\n"
            "\n"
            "    python3 scripts/bump_version.py --sync\n"
            "    # then set .release-please-manifest.json's \".\" to the same number\n"
            "\n"
            "  Genuinely intentional? BUILD_LOOP_SKIP_VERSION_GATE=1 git push\n"
            "\n"
        )
    cur, pub = verdict.get("current"), verdict.get("published")
    same = cur == pub
    return (
        "\n"
        "  PUSH BLOCKED — this push carries no version bump.\n"
        "\n"
        f"    origin  {verdict.get('remote_ref')}  is at  {pub}\n"
        f"    local   {MANIFEST}       is at  {cur}"
        f"{'  (unchanged)' if same else '  (BEHIND origin)'}\n"
        "\n"
        "  A push is one release. Bump once, whatever the commit count:\n"
        "\n"
        "    python3 scripts/bump_version.py --patch     # ordinary push\n"
        "    python3 scripts/bump_version.py --minor     # functionality change\n"
        "\n"
        "  Commit the manifest change, then push again. To publish a tagged\n"
        "  package later, tag that committed version separately:\n"
        "    python3 scripts/bump_version.py --tag\n"
        "    git push --follow-tags\n"
        "\n"
        "  Genuinely not a release? BUILD_LOOP_SKIP_VERSION_GATE=1 git push\n"
        "\n"
    )
