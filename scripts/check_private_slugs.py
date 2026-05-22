#!/usr/bin/env python3
"""Pre-commit guard — fails a commit that stages a private app slug.

build-loop is open source. Shipping a real private project slug in
examples, fixtures, or docs leaks the maintainer's private project data.
This scanner attacks the root cause: it runs on every commit, scans the
staged content of each tracked file against a denylist, and exits
non-zero (blocking the commit) on a hit.

Usage:
    python3 scripts/check_private_slugs.py            # scan staged files
    python3 scripts/check_private_slugs.py --all      # scan whole tree
    python3 scripts/check_private_slugs.py FILE...     # scan named files

Exit codes:
    0 — no private slug found (commit may proceed)
    1 — private slug found (commit blocked); offending lines printed
    2 — usage / git error

------------------------------------------------------------------------
DENYLIST — edit this list to add or remove guarded slugs.
Each entry is a case-insensitive regex fragment matched as a word-ish
token. Keep them specific enough not to false-positive on generic words.
------------------------------------------------------------------------
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# --- EDIT HERE: the private app slugs / domains that must never ship ---
PRIVATE_SLUGS: list[str] = [
    r"speaksavvy",
    r"atomize-ai",
    r"atomize",
    r"travel-planner",
    r"productpilot",
    r"local-smartz",
    r"rosslabs\.ai",
]
# -----------------------------------------------------------------------

# This file necessarily contains the denylist literals; never scan it.
SELF = "scripts/check_private_slugs.py"

# Files where a slug is an intentional, load-bearing historical record.
# These are exempt because genericizing them would falsify the record.
# Keep this list short and justify every entry.
EXEMPT_PATHS: set[str] = {
    "docs/SECURITY_FOLLOWUP_2026-05-05.md",  # quotes a past fixture-scrub fix verbatim
}

_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(" + "|".join(PRIVATE_SLUGS) + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return Path(out)
    except (subprocess.CalledProcessError, OSError):
        print("check_private_slugs: not a git repo", file=sys.stderr)
        sys.exit(2)


def _staged_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def _all_tracked(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def _staged_content(root: Path, path: str) -> str | None:
    """Return the staged (index) blob of `path`, or None if unreadable."""
    r = subprocess.run(
        ["git", "show", f":{path}"], cwd=root, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout


def _disk_content(root: Path, path: str) -> str | None:
    try:
        return (root / path).read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeError):
        return None


def main(argv: list[str]) -> int:
    root = _repo_root()
    mode_all = "--all" in argv
    explicit = [a for a in argv if not a.startswith("-")]

    if explicit:
        files = explicit
        reader = _disk_content
    elif mode_all:
        files = _all_tracked(root)
        reader = _disk_content
    else:
        files = _staged_files(root)
        reader = _staged_content

    hits: list[tuple[str, int, str, str]] = []
    for path in files:
        if path == SELF or path in EXEMPT_PATHS:
            continue
        content = reader(root, path)
        if content is None:
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            m = _PATTERN.search(line)
            if m:
                hits.append((path, lineno, m.group(1), line.strip()[:200]))

    if hits:
        print("BLOCKED: private app slug found in staged content.", file=sys.stderr)
        print("build-loop is open source — replace with a generic placeholder",
              file=sys.stderr)
        print("(example-app, example-ios-app, example-web-app, example.com).\n",
              file=sys.stderr)
        for path, lineno, slug, line in hits:
            print(f"  {path}:{lineno}: [{slug}] {line}", file=sys.stderr)
        print("\nIf a hit is an intentional historical record, add the path to",
              file=sys.stderr)
        print(f"EXEMPT_PATHS in {SELF}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
