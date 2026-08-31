#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Advise (never auto-bump) plugin versions for build-loop Review Sub-step D Gate 6.

Default state is `hold` — emit a one-line nudge in Review-F. Switches to `suggest`
only when `.build-loop/release-pending.md` exists in the workdir, signaling the user
has declared the in-flight feature batch complete.

Last-bump source: prefer `git describe --tags --match 'v*' --abbrev=0`. Fall back to
`git log -1 --pretty=%H -- .claude-plugin/plugin.json` when tags are absent or stale
(common — many repos bump in commit messages without tagging).

Bump kind is DECLARED, never inferred from commit content. A push is the unit of
release, so N local commits produce one patch bump whatever they contain. The
old rule walked Conventional Commit prefixes and escalated the whole batch to
minor on a single `feat:` — which made the version track local authoring rather
than releases (observed: 143 commits since 0.39.0 advising 0.40.0).

To declare something other than patch, put `bump: minor` (or `major`) on a line
in `.build-loop/release-pending.md`. Absent or unparseable -> patch.

Breaking changes are still SURFACED — `breaking_commits` lists them so a release
is never silently shipped as a patch — but they no longer move the number on
their own. Deciding that is the release author's call, not a prefix's.

Output: pure JSON to stdout. Never writes. Exit 0 always (advisory, non-blocking).
Exit 2 only on usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-[A-Za-z0-9.-]+)?$")
TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?)$")


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return 127, ""
    return proc.returncode, (proc.stdout or "").strip()


def read_manifest_version(workdir: Path) -> str | None:
    manifest = workdir / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version")
    if isinstance(version, str) and SEMVER_RE.match(version):
        return version
    return None


def _semver_tuple(version: str) -> tuple[int, int, int]:
    m = SEMVER_RE.match(version)
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def last_bump_from_tag(workdir: Path, current: str) -> dict[str, Any] | None:
    """Return tag info only when the latest tag matches the manifest version.

    Many repos bump in commit messages without tagging (build-loop itself: tags
    stop at v0.2.0 while plugin.json sits at v0.9.0). Returning a stale tag
    would drown the advisor in dozens of false "commits since bump" entries.
    """
    rc, tag = run(
        ["git", "describe", "--tags", "--match", "v*", "--abbrev=0"], workdir
    )
    if rc != 0 or not tag:
        return None
    m = TAG_RE.match(tag)
    if not m:
        return None
    tag_version = m.group(1)
    if _semver_tuple(tag_version) < _semver_tuple(current):
        return None
    rc, sha = run(["git", "rev-list", "-n", "1", tag], workdir)
    if rc != 0 or not sha:
        return None
    rc, date = run(["git", "log", "-1", "--pretty=%cI", sha], workdir)
    return {"sha": sha, "date": date if rc == 0 else "", "source": "tag", "ref": tag}


def last_bump_from_manifest(workdir: Path) -> dict[str, Any] | None:
    rc, sha = run(
        [
            "git",
            "log",
            "-1",
            "--pretty=%H",
            "--",
            ".claude-plugin/plugin.json",
        ],
        workdir,
    )
    if rc != 0 or not sha:
        return None
    rc, date = run(["git", "log", "-1", "--pretty=%cI", sha], workdir)
    return {
        "sha": sha,
        "date": date if rc == 0 else "",
        "source": "manifest",
        "ref": ".claude-plugin/plugin.json",
    }


def commits_since(workdir: Path, sha: str) -> list[str]:
    rc, out = run(
        ["git", "log", f"{sha}..HEAD", "--pretty=%s"], workdir
    )
    if rc != 0 or not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


VALID_KINDS = ("patch", "minor", "major")
BUMP_DECL_RE = re.compile(r"^\s*bump\s*:\s*(patch|minor|major)\s*$",
                          re.IGNORECASE | re.MULTILINE)


def declared_bump_kind(marker_text: str) -> str:
    """Bump kind from the release marker. Default patch — one push, one patch."""
    m = BUMP_DECL_RE.search(marker_text or "")
    return m.group(1).lower() if m else "patch"


def breaking_commits(messages: list[str]) -> list[str]:
    """Commits that LOOK breaking. Advisory only; they do not move the number.

    Kept because dropping the signal entirely would let a breaking change ship
    as a patch with nothing said. Reporting it puts the call in front of the
    release author instead of a regex.
    """
    out = []
    for msg in messages:
        head = msg.split(":", 1)[0].strip()
        if "BREAKING CHANGE" in msg or head.endswith("!"):
            out.append(msg.splitlines()[0][:120])
    return out


def bump_version(current: str, kind: str) -> str:
    m = SEMVER_RE.match(current)
    if not m:
        return current
    major, minor, patch = (int(m.group(i)) for i in (1, 2, 3))
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Advise plugin version bumps without writing.")
    ap.add_argument("--workdir", required=True, help="Plugin source repo root")
    args = ap.parse_args()

    workdir = Path(args.workdir).resolve()
    if not (workdir / ".git").exists():
        print(json.dumps({"error": "not a git repo", "workdir": str(workdir)}))
        return 0

    current = read_manifest_version(workdir)
    if current is None:
        print(json.dumps({"error": "no .claude-plugin/plugin.json with valid semver", "workdir": str(workdir)}))
        return 0

    last_bump = last_bump_from_tag(workdir, current) or last_bump_from_manifest(workdir)
    commits = [] if last_bump is None else commits_since(workdir, last_bump["sha"])

    # Marker first: it DECLARES the kind, so it must be read before the number
    # is computed. Commit content is no longer an input to the version.
    marker = workdir / ".build-loop" / "release-pending.md"
    if marker.is_file():
        state = "suggest"
        try:
            release_notes = marker.read_text().strip()
        except OSError:
            release_notes = ""
    else:
        state = "hold"
        release_notes = ""

    kind = declared_bump_kind(release_notes)
    suggested = bump_version(current, kind)
    breaking = breaking_commits(commits)

    out = {
        "state": state,
        "current": current,
        "commits_since_bump": len(commits),
        "last_bump": last_bump,
        "suggested_version": suggested,
        "bump_kind": kind,
        "breaking_commits": breaking,
        "release_notes": release_notes,
        "marker_path": str(marker),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
