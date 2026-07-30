#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""enforce_canonical_memory.py — C-MEMORY/canonical_writer enforcement gate.

The contract (constitution C-MEMORY, `references/memory.md`, `AGENTS.md` M5):
all writes to a canonical ``build-loop-memory/`` lane MUST go through
``scripts/memory_writer.py``, which stamps provenance frontmatter
(source_workdir, source_run_id, source_host, created_at, ...). Direct file
appends bypass provenance and break cross-repo trust gradients.

That contract was DOCUMENTED but never ENFORCED — an agent could ``Write`` or
``echo >>`` straight into a memory lane and nothing caught it. This script is
the smallest mechanism that actually enforces it: it scans staged git changes
and flags any memory-lane ``*.md`` file missing the required provenance
frontmatter (the signature of a writer-stamped file). Provenance present =>
written through the canonical writer => pass. Provenance absent => direct
write => violation.

DRY: the required-field set and the frontmatter parser are imported from
``memory_writer`` — one source of truth for the provenance schema. No
duplicated keys, no duplicated YAML-subset parser.

Modes:
  --staged            Scan ``git diff --cached --name-only`` in --workdir
                      (default cwd). This is the pre-commit / standalone-gate
                      mode. Fail-soft outside a git repo (reports clean).
  --paths P [P ...]   Scan an explicit list of files (testing / ad-hoc).

Exit codes:
  0  no violations (or --no-strict)
  1  violations found AND --strict (default)

Output: human summary, or ``--json`` for a structured envelope.
Stdlib only beyond the in-repo ``memory_writer`` import. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import memory_writer as mw  # noqa: E402  (in-repo, sys.path adjusted above)

# The canonical memory store directory name and the lane subfolders that live
# under it. A staged path is "in a memory lane" when its components contain the
# store dir followed by a known lane. Matching on the relative path keeps the
# check independent of where the store is mounted (worktree, clone, override).
MEMORY_STORE_DIRNAME = "build-loop-memory"
LANE_NAMES = frozenset({
    "lessons", "decisions", "design", "debugging", "product", "projects",
})

# Writer-managed sidecar files that legitimately live in a lane without
# provenance frontmatter. These are emitted BY the canonical tooling, so a
# staged change to them is not a bypass.
WRITER_MANAGED_BASENAMES = frozenset({
    "INDEX.jsonl", "TELEMETRY.jsonl", "milestones.jsonl",
    "INDEX.jsonl.lock", "milestones.jsonl.lock",
    "MEMORY.md",  # human-maintained index, not a provenance-bearing entry
})


def is_memory_lane_entry(rel_path: str) -> bool:
    """True if ``rel_path`` is a provenance-bearing memory entry (a lane *.md).

    Requires the path to pass through ``build-loop-memory/<lane>/`` AND end in
    ``.md`` AND not be a writer-managed sidecar. JSONL logs / lock files /
    MEMORY.md are excluded — they are not canonical-writer entries.
    """
    parts = Path(rel_path).parts
    if MEMORY_STORE_DIRNAME not in parts:
        return False
    idx = parts.index(MEMORY_STORE_DIRNAME)
    tail = parts[idx + 1 :]
    if not tail or tail[0] not in LANE_NAMES:
        return False
    if not rel_path.endswith(".md"):
        return False
    if Path(rel_path).name in WRITER_MANAGED_BASENAMES:
        return False
    return True


def has_provenance(path: Path) -> bool:
    """True if the file carries all required provenance frontmatter fields.

    Reuses ``memory_writer._split_frontmatter`` so the parse semantics match
    exactly what the writer emits and the migrator backfills.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    fm, _ = mw._split_frontmatter(text)
    return mw.REQUIRED_PROVENANCE_FIELDS.issubset(fm.keys())


def scan_paths(paths: list[str], *, root: Path) -> list[dict]:
    """Return a violation row for each memory-lane entry lacking provenance.

    ``root`` resolves relative staged paths to disk so we can read frontmatter.
    A lane entry that was deleted (not on disk) is skipped — a deletion is not
    a direct write.
    """
    violations: list[dict] = []
    for rel in paths:
        if not is_memory_lane_entry(rel):
            continue
        disk = (root / rel) if not Path(rel).is_absolute() else Path(rel)
        if not disk.exists():
            continue  # deletion / rename-away — not a bypass write
        if not has_provenance(disk):
            violations.append({
                "path": rel,
                "reason": "memory-lane write missing provenance frontmatter "
                          "(bypassed memory_writer.py)",
            })
    return violations


def git_staged_paths(workdir: Path) -> list[str]:
    """Return repo-relative staged paths via ``git diff --cached --name-only``.

    Fail-soft: returns [] when git is unavailable or the dir is not a repo,
    matching the non-blocking posture of build-loop's other memory tooling.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(workdir), "diff", "--cached", "--name-only"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--staged", action="store_true",
                     help="Scan git staged paths in --workdir")
    src.add_argument("--paths", nargs="+", help="Explicit file list to scan")
    p.add_argument("--workdir", default=".",
                   help="Repo root for --staged and for resolving relative paths")
    p.add_argument("--strict", dest="strict", action="store_true", default=True,
                   help="Exit 1 on violation (default)")
    p.add_argument("--no-strict", dest="strict", action="store_false",
                   help="Always exit 0 (advisory/report mode)")
    p.add_argument("--json", action="store_true", help="Emit JSON envelope")
    args = p.parse_args(argv)

    root = Path(args.workdir).resolve()
    paths = git_staged_paths(root) if args.staged else args.paths
    violations = scan_paths(paths, root=root)

    envelope = {
        "rule": "C-MEMORY/canonical_writer",
        "scanned": len(paths),
        "count": len(violations),
        "violations": violations,
    }

    if args.json:
        json.dump(envelope, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif violations:
        print(f"C-MEMORY/canonical_writer: {len(violations)} violation(s) — "
              f"memory written directly, bypassing memory_writer.py:")
        for v in violations:
            print(f"  - {v['path']}")
        print("Fix: write the entry via "
              "`python3 scripts/memory_writer.py write ...` so provenance is stamped.")
    else:
        print("C-MEMORY/canonical_writer: clean "
              f"({len(paths)} path(s) scanned, no direct memory writes)")

    return 1 if (violations and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
