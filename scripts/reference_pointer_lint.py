#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""reference_pointer_lint.py — every ``references/X.md`` pointer on the primary
surface must resolve to a real file in the repo.

Named, observed failure this control earns its place against: markdown on the
surface a reader actually lands on (``AGENTS.md``, ``README.md``, the Codex
wrapper, the canonical Build Loop skill) points at ``references/X.md`` and the
file does not exist, so the reader follows a dead link.

This check used to live inside the Codex bundle builder, where it resolved
pointers against a flattened ``<bundle>/references/`` mirror. The mirror is
gone — the repo root IS the Codex surface — so resolution now happens against
the repo's real reference directories in ``REFERENCE_SOURCE_DIRS``, searched in
order.

Exit codes: 0 every primary-surface pointer resolves · 1 at least one dangles.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# The logical ``references/`` namespace is spread across the repo's top-level
# ``references/`` plus individual skills' own ``references/``. Search order
# resolves drift deterministically: the skill's own copy wins, then the repo
# root, then the ui-design skill.
REFERENCE_SOURCE_DIRS = (
    Path("skills") / "build-loop" / "references",
    Path("skills") / "repo-maintenance" / "references",
    Path("references"),
    Path("skills") / "ui-design" / "references",
)

# Primary surface = the files a reader actually lands on.
SURFACE_ROOTS = (
    Path("codex-skills") / "build-loop",
    Path("skills") / "build-loop",
    Path("AGENTS.md"),
    Path("README.md"),
)

# A bare ``references/X.md`` pointer. Anchored to a path boundary so we don't
# match foreign-skill prose like ``build-loop:deepagents`` references — those
# are always written with the skill name in front (``The skill's
# `references/anti-patterns.md```) and resolve inside that other skill.
_REFERENCE_POINTER_RE = re.compile(r"(?:^|[\s`(\[])references/([A-Za-z0-9_.-]+\.md)")

# Pointers that name another skill's references dir in prose, not a build-loop
# file. They have no build-loop source and must not fail the check.
FOREIGN_SKILL_REFERENCES = frozenset(
    {
        "anti-patterns.md",  # build-loop:building-with-deepagents
        "stack-templates.md",  # build-loop:logging-tracer
        "ios-notification-alarm-playbook.md",  # build-loop:debugging-memory
    }
)

# Pre-existing, deliberate forward-reference placeholders in the source tree
# (marked TBD in prose). They have no file anywhere. Keep this list tight: a
# NEW unresolvable pointer on the primary surface must still fail.
KNOWN_TBD_REFERENCES = frozenset(
    {
        "brief-filters.md",  # references/implementer-envelope-schema.md: "(TBD)"
    }
)


def cited_reference_basenames(*roots: Path) -> set[str]:
    """Every bare ``references/<file>.md`` pointer across the given markdown roots.

    A root may be a directory (walked for ``*.md``) or a single markdown file.
    """
    cited: set[str] = set()
    for root in roots:
        if root.is_dir():
            files = root.rglob("*.md")
        elif root.is_file():
            files = [root]
        else:
            continue
        for md in files:
            cited.update(
                _REFERENCE_POINTER_RE.findall(md.read_text(encoding="utf-8", errors="ignore"))
            )
    return cited


def resolve_reference_source(repo_root: Path, basename: str) -> Path | None:
    """Locate a cited reference file in the repo's reference search paths."""
    for rel_dir in REFERENCE_SOURCE_DIRS:
        candidate = repo_root / rel_dir / basename
        if candidate.is_file():
            return candidate
    return None


def dangling_reference_pointers(repo_root: Path) -> list[str]:
    """Primary-surface ``references/X.md`` pointers with no file in the repo."""
    cited = cited_reference_basenames(*(repo_root / rel for rel in SURFACE_ROOTS))
    return sorted(
        basename
        for basename in cited
        if basename not in FOREIGN_SKILL_REFERENCES
        and basename not in KNOWN_TBD_REFERENCES
        and resolve_reference_source(repo_root, basename) is None
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="repository root to lint (default: this script's repo)",
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    dangling = dangling_reference_pointers(repo_root)
    if dangling:
        print(
            "dangling reference pointers on the primary surface (no file under "
            + ", ".join(str(d) for d in REFERENCE_SOURCE_DIRS)
            + "): "
            + ", ".join(dangling),
            file=sys.stderr,
        )
        return 1
    print("reference pointers: all primary-surface pointers resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
