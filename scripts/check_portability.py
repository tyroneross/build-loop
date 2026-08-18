#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Pre-commit guard — fails a commit that ships a maintainer-machine path.

build-loop is installed by other people. A literal path that only exists on
the maintainer's laptop does not fail loudly on their machine: an agent reads
the address, finds nothing, and silently proceeds with less context — or, in
the append_milestone.py case (fixed 2026-08-18), CREATES the maintainer's
directory tree on the user's disk.

WHY THIS EXISTS RATHER THAN ANOTHER SWEEP
-----------------------------------------
This exact defect class was already fixed once. docs/SECURITY_FOLLOWUP_2026-05-05.md
records SEC-009 (commit de17a72) replacing a hardcoded
``/dev/git-folder/build-loop-memory`` and genericising private fixtures. Three
and a half months later the tree held 60 ``~/dev`` occurrences, a dead
transcript miner, and four Python modules with laptop-path defaults. A sweep
without a gate decays. This is the gate.

Sibling guard: ``scripts/check_private_slugs.py`` (private project names).
Same CLI shape, same exit codes, deliberately separate concerns.

Usage:
    python3 scripts/check_portability.py            # scan staged files
    python3 scripts/check_portability.py --all      # scan whole tree
    python3 scripts/check_portability.py FILE...    # scan named files
    python3 scripts/check_portability.py --all --stats   # precision triage

Exit codes:
    0 — clean (commit may proceed)
    1 — a maintainer-machine path was found (commit blocked)
    2 — usage / git error
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

SELF_BASENAME = "check_portability.py"

# Surfaces that SHIP to an installed user. docs/ and tests/ are excluded: they
# are read by humans and CI, not followed as instructions by an installed agent.
SHIPPED_DIRS = ("skills", "agents", "commands", "hooks", "references", "scripts",
                "templates", ".agents", "codex-skills")

# Paths where a maintainer-machine string is CORRECT and must never be flagged.
# Keep this list short and justify every entry.
EXEMPT_PATHS: dict[str, str] = {
    "scripts/_paths.py":
        "defines the legacy memory root on purpose — this is the resolver every "
        "other module is required to call instead of hardcoding an address",
    "scripts/check_portability.py":
        "this file; necessarily contains the patterns it searches for",
    "scripts/check_private_slugs.py":
        "sibling guard; contains denylist-adjacent logic by design",
    "scripts/migrate_project_memory.py":
        "migration script; the legacy path is its subject, not an address it "
        "assumes still exists",
    "scripts/install_memory.py":
        "documents the real resolution order (legacy-if-present, else neutral)",
    "scripts/retrospective/write.py":
        "docstring documents the resolver's own fallback order",
    "scripts/_test_helpers.py":
        "asserts tests never touch the legacy root — naming it is the point",
    "templates/memory/README.md":
        "documents the legacy -> neutral migration path for users who have one",
    "scripts/transcript_pattern_miner/accuracy/TRANSPARENCY.md":
        "uses a fictional '/Users/alexchen/...' example to explain path "
        "normalisation; not an address anything reads",
    "scripts/transcript_pattern_miner/textproc.py":
        "uses '/dev/git-folder/' as a project-NAME heuristic when parsing "
        "transcript cwd strings, not as a path to read",
}

# Tokens that make a hit benign: the doc is deliberately showing a placeholder,
# or is describing the resolution order rather than instructing a read.
PLACEHOLDER_MARKERS = (
    "/Users/you/", "<your-", "<path-to-", "<home-slug>", "<memory_store_root>",
    "<local-checkout>", "<slug-for-", "your-private-", "example.com",
    "-Users-*", "projects/*/", "your local checkout", "<your", "$GROUNDWORK_ROOT",
)

RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "private-home-path",
        re.compile(r"(?:~|\$HOME|/Users/[A-Za-z0-9_.-]+)/(?:dev|Desktop)/git-folder/"),
        "a path that exists only on the maintainer's machine. Cite the resolver "
        "(scripts/_paths.py:memory_store_root(), or scripts/memory_facade.py "
        "recall()) instead of an address.",
    ),
    (
        "private-research-path",
        re.compile(r"(?:~|\$HOME|/Users/[A-Za-z0-9_.-]+)/dev/research"),
        "a private research directory. Keep the human-readable title, drop the "
        "path, and mark it '(private research note — substance summarized here)'.",
    ),
    (
        "maintainer-home-slug",
        re.compile(r"-Users-[A-Za-z0-9_]+(?<!-Users-you)"),
        "a home-directory slug baked in as a literal. Derive it from $HOME "
        "(str(Path.home()).replace('/', '-')) or glob ~/.claude/projects/*/.",
    ),
    (
        "personal-dsn",
        re.compile(r"postgres(?:ql)?://(?!(?:user|USER|you|localhost|\$)[:@/])[A-Za-z0-9_.-]+@"),
        "a connection string carrying a personal username. Read it from "
        "$BUILD_LOOP_DATABASE_URL / $DATABASE_URL instead.",
    ),
]


def _repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        print("check_portability: not a git repo", file=sys.stderr)
        sys.exit(2)
    if not out:
        print("check_portability: cannot resolve repo root", file=sys.stderr)
        sys.exit(2)
    return Path(out)


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
    r = subprocess.run(["git", "show", f":{path}"], cwd=root, capture_output=True)
    if r.returncode != 0:
        return None
    text = r.stdout.decode("utf-8", errors="replace")
    return None if "\x00" in text else text


def _disk_content(root: Path, path: str) -> str | None:
    try:
        return (root / path).read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeError):
        return None


def _is_test_file(path: str) -> bool:
    """Tests are not shipped instructions.

    No installed agent reads scripts/test_*.py, and several tests must contain
    the literal they guard against — scripts/test_portable_paths.py asserts the
    maintainer path is ABSENT from source, which requires naming it. Grading
    tests would make the gate fight its own regression suite.
    """
    name = Path(path).name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or "/fixtures" in path
        or name == "fixtures.py"
    )


def _in_shipped_surface(path: str) -> bool:
    return path.split("/", 1)[0] in SHIPPED_DIRS and not _is_test_file(path)


def _is_benign(line: str) -> bool:
    """A hit is benign when the line is showing a placeholder, is an SPDX
    attribution header, or is a mirrored plugin artifact."""
    if "SPDX-FileCopyrightText" in line or "SPDX-License-Identifier" in line:
        return True
    return any(marker in line for marker in PLACEHOLDER_MARKERS)


def scan(root: Path, paths: list[str], staged: bool) -> list[tuple[str, int, str, str]]:
    hits: list[tuple[str, int, str, str]] = []
    for path in paths:
        if not _in_shipped_surface(path):
            continue
        if path in EXEMPT_PATHS or Path(path).name == SELF_BASENAME:
            continue
        if path.startswith("plugin-artifacts/"):
            continue  # generated mirror; the source file is graded instead
        content = _staged_content(root, path) if staged else _disk_content(root, path)
        if content is None:
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            if _is_benign(line):
                continue
            for rule_name, pattern, advice in RULES:
                if pattern.search(line):
                    hits.append((path, lineno, rule_name, advice))
                    break
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true", help="scan every tracked file")
    ap.add_argument("--stats", action="store_true", help="summarise by rule (precision triage)")
    ap.add_argument("files", nargs="*", help="explicit files to scan")
    args = ap.parse_args(argv)

    root = _repo_root()
    if args.files:
        paths, staged = args.files, False
    elif args.all:
        paths, staged = _all_tracked(root), False
    else:
        paths, staged = _staged_files(root), True

    hits = scan(root, paths, staged)

    if args.stats:
        by_rule: dict[str, int] = {}
        for _, _, rule, _a in hits:
            by_rule[rule] = by_rule.get(rule, 0) + 1
        print(f"scanned {len(paths)} path(s); {len(hits)} hit(s) in "
              f"{len({h[0] for h in hits})} file(s)")
        for rule, n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
            print(f"  {n:4d}  {rule}")
        return 0 if not hits else 1

    if not hits:
        return 0

    print("BLOCKED: maintainer-machine path found in shipped content.", file=sys.stderr)
    print("build-loop is installed by other people — a laptop-only address makes\n"
          "their agent chase a file that cannot exist.\n", file=sys.stderr)
    seen_advice: set[str] = set()
    for path, lineno, rule, advice in hits:
        print(f"  {path}:{lineno}: [{rule}]", file=sys.stderr)
        if advice not in seen_advice:
            print(f"      -> {advice}", file=sys.stderr)
            seen_advice.add(advice)
    print(f"\n{len(hits)} hit(s). If one is genuinely correct, add the path to\n"
          "EXEMPT_PATHS in scripts/check_portability.py with a justification.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
