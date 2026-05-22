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
    2 — usage / git / config error (e.g. missing .private-slugs)

------------------------------------------------------------------------
DENYLIST — runtime config, NOT shipped in the tracked tree.
The list of guarded slugs lives in a gitignored ``.private-slugs`` file
(one slug per line) at the repo root. A tracked ``.private-slugs.example``
documents the format with generic placeholders. This keeps the real
private slugs out of every tracked file, including this one.
------------------------------------------------------------------------
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Runtime denylist config (gitignored). One slug per line; ``#`` comments
# and blank lines ignored. Each line is a LITERAL slug (regex
# metacharacters are escaped), matched case-insensitively as a word-ish
# token.
DENYLIST_FILENAME = ".private-slugs"
EXAMPLE_FILENAME = ".private-slugs.example"

# This file necessarily contains denylist-adjacent logic; never scan it.
# Matched by resolved path (worktree/submodule-safe) and by basename.
SELF_BASENAME = "check_private_slugs.py"

# Files where a slug is an intentional, load-bearing historical record.
# These are exempt because genericizing them would falsify the record.
# Keep this list short and justify every entry. Currently empty: every
# tracked file is fully scrubbed and the guard enforces zero exceptions.
EXEMPT_PATHS: set[str] = set()


def _repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if not out:
            print("check_private_slugs: cannot resolve repo root", file=sys.stderr)
            sys.exit(2)
        return Path(out)
    except (subprocess.CalledProcessError, OSError):
        print("check_private_slugs: not a git repo", file=sys.stderr)
        sys.exit(2)


def _load_denylist(root: Path) -> list[str]:
    """Read the gitignored .private-slugs file.

    Fail closed: a missing or empty config file is a usage error (exit 2),
    never a silent pass. Shipping the guard with no denylist would let
    every slug through unnoticed.
    """
    cfg = root / DENYLIST_FILENAME
    if not cfg.exists():
        print(
            f"check_private_slugs: {DENYLIST_FILENAME} not found at repo root.",
            file=sys.stderr,
        )
        print(
            f"  Copy {EXAMPLE_FILENAME} to {DENYLIST_FILENAME} and add the "
            f"private slugs to guard (one per line). The guard cannot run "
            f"without it.",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        raw = cfg.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"check_private_slugs: cannot read {DENYLIST_FILENAME}: {exc}",
              file=sys.stderr)
        sys.exit(2)
    slugs = [
        ln.strip() for ln in raw.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not slugs:
        print(
            f"check_private_slugs: {DENYLIST_FILENAME} is empty — no slugs "
            f"to guard. Add at least one slug or remove the guard.",
            file=sys.stderr,
        )
        sys.exit(2)
    return slugs


def _compile_pattern(slugs: list[str]) -> re.Pattern[str]:
    # Boundary class is alphanumeric ONLY — underscore is treated as a
    # boundary that still allows the match, so an embedded slug like
    # ``_atomize`` or ``atomize_`` is caught. The original guard's
    # lookbehind included ``_`` while the lookahead did not; that
    # asymmetry let an underscore-prefixed slug slip past (SEC-005).
    #
    # Each denylist entry is a LITERAL slug, not a regex fragment — the
    # SEC-011 runtime ``.private-slugs`` config holds plain strings a
    # maintainer types without knowing regex. ``re.escape`` neutralises
    # every metacharacter, so a literal dot in ``rosslabs.ai`` matches
    # only a real dot, not the public ``rosslabs-ai-toolkit`` name.
    # Skipping ``re.escape`` here was the SEC-011 regression: it turned
    # a literal ``.`` into a wildcard and flagged ~30 legitimate public
    # marketplace references in CI.
    escaped = (re.escape(s) for s in slugs)
    return re.compile(
        r"(?<![A-Za-z0-9])(" + "|".join(escaped) + r")(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


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


def _is_self(root: Path, path: str) -> bool:
    """Worktree/submodule-safe SELF check.

    A relative-path string compare breaks when the script is invoked
    from a worktree, a submodule, or with a cwd that differs from the
    repo root. Compare the resolved absolute path, with a basename
    fallback so the exemption holds even if path resolution is degraded.
    """
    if Path(path).name == SELF_BASENAME:
        try:
            resolved = (root / path).resolve()
            return resolved == Path(__file__).resolve()
        except (OSError, RuntimeError):
            # Resolution failed — fall back to the basename match, which
            # is already True at this point. The guard scanning itself
            # would always block, so basename exemption is the safe call.
            return True
    return False


def main(argv: list[str]) -> int:
    root = _repo_root()
    pattern = _compile_pattern(_load_denylist(root))
    mode_all = "--all" in argv
    explicit = [a for a in argv if not a.startswith("-")]

    if explicit:
        files = explicit
        reader = _disk_content
        ci_mode = True  # explicit/CI invocation — fail closed on unreadable
    elif mode_all:
        files = _all_tracked(root)
        reader = _disk_content
        ci_mode = True
    else:
        files = _staged_files(root)
        reader = _staged_content
        ci_mode = False

    hits: list[tuple[str, int, str, str]] = []
    unreadable: list[str] = []
    for path in files:
        # The denylist config and its tracked format-template both
        # necessarily contain denylist-vocabulary tokens; never scan
        # either. `.private-slugs` is gitignored, but an explicit
        # FILE... invocation could still name it; `.private-slugs.example`
        # IS tracked and would otherwise self-trip the guard on its own
        # sentinel tokens. Match by basename so the exemption holds from
        # any cwd / worktree. Neither file can hold a real private slug:
        # `.private-slugs` is gitignored and `.private-slugs.example`
        # ships sentinel placeholders reviewed in every PR.
        if Path(path).name in (DENYLIST_FILENAME, EXAMPLE_FILENAME):
            continue
        if _is_self(root, path) or path in EXEMPT_PATHS:
            continue
        content = reader(root, path)
        if content is None:
            unreadable.append(path)
            # Staged mode: a blob git can't show is not committable
            # content for this path — skip is reasonable. CI/explicit
            # mode: never silently pass an unreadable tracked file.
            if ci_mode:
                print(f"check_private_slugs: cannot read tracked file: {path}",
                      file=sys.stderr)
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            m = pattern.search(line)
            if m:
                hits.append((path, lineno, m.group(1), line.strip()[:200]))

    if hits:
        print("BLOCKED: private app slug found in staged content.", file=sys.stderr)
        print("build-loop is open source — replace each hit with a generic,",
              file=sys.stderr)
        print("non-private placeholder before committing.\n", file=sys.stderr)
        for path, lineno, slug, line in hits:
            print(f"  {path}:{lineno}: [{slug}] {line}", file=sys.stderr)
        print(f"\nIf a hit is an intentional historical record, add the path to",
              file=sys.stderr)
        print(f"EXEMPT_PATHS in scripts/{SELF_BASENAME}.", file=sys.stderr)
        return 1

    if ci_mode and unreadable:
        print(
            f"\ncheck_private_slugs: {len(unreadable)} tracked file(s) could "
            f"not be read and were NOT scanned — failing closed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
