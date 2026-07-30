#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Detect whether a repo carries the canonical four-layer attribution stack.

Surfaces an advisory line for Phase 1 Assess when **all** of the following hold:

1. The repo has a public GitHub origin remote.
2. One or more of ``NOTICE`` / ``REUSE.toml`` / ``CONTRIBUTING.md`` is missing,
   OR fewer than 80% of shipped source files carry an SPDX header.

Output (JSON):

    {
      "should_advise": true,
      "reasons": ["missing NOTICE", "spdx_coverage=0.42"],
      "advisory": "Repo is missing standard attribution layers — run `python scripts/attribution_stamp.py --repo <path>`",
      "spdx_coverage": 0.42,
      "shipped_paths": ["src", "scripts"]
    }

This is **advisory only**. The orchestrator surfaces the advisory line in the
Phase 1 Assess report and queues an auto chunk in Phase 2 Plan when scope ≥ S;
it never blocks the run nor pauses to ask the user. Per
``feedback_advisory_checks_are_automated`` in user memory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REQUIRED_FILES = ("NOTICE", "REUSE.toml", "CONTRIBUTING.md")
SHIPPED_PATH_CANDIDATES = (
    "src",
    "scripts",
    "hooks",
    "skills",
    "agents",
    "commands",
    "references",
    # Python-heavy repo fallback: any top-level package directory containing __init__.py
)
SPDX_PATTERN = re.compile(r"SPDX-FileCopyrightText:", re.MULTILINE)
SOURCE_EXTENSIONS = {
    ".py",
    ".sh",
    ".bash",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".md",
    ".mdx",
}
EXCLUDES = {
    "node_modules",
    "dist",
    "build",
    ".git",
    ".venv",
    "venv",
    "archive",
    "tests/fixtures",
    "docs/test-fixtures",
    "__pycache__",
    ".pytest_cache",
}
COVERAGE_THRESHOLD = 0.80


def has_github_origin(repo: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    url = result.stdout.strip().lower()
    return "github.com" in url


def discover_python_package_dirs(repo: Path) -> list[str]:
    """Return top-level package directories (a dir at repo root with __init__.py)."""
    dirs: list[str] = []
    for entry in repo.iterdir():
        if not entry.is_dir():
            continue
        if entry.name in EXCLUDES or entry.name.startswith("."):
            continue
        if (entry / "__init__.py").exists():
            dirs.append(entry.name)
    return dirs


def compute_spdx_coverage(repo: Path, paths: list[str]) -> tuple[int, int]:
    """Return ``(stamped_count, total_count)`` across shipped source files."""
    total = 0
    stamped = 0
    for top in paths:
        root = repo / top
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDES]
            for fn in filenames:
                p = Path(dirpath) / fn
                if p.suffix.lower() not in SOURCE_EXTENSIONS:
                    continue
                rel = p.relative_to(repo)
                if any(part in EXCLUDES for part in rel.parts):
                    continue
                total += 1
                try:
                    head = p.read_text(encoding="utf-8", errors="ignore")[:2000]
                except OSError:
                    continue
                if SPDX_PATTERN.search(head):
                    stamped += 1
    return stamped, total


def detect(repo: Path) -> dict:
    if not has_github_origin(repo):
        return {
            "should_advise": False,
            "reasons": ["no_github_origin"],
            "advisory": None,
            "spdx_coverage": None,
            "shipped_paths": [],
        }

    reasons: list[str] = []
    for fname in REQUIRED_FILES:
        if not (repo / fname).exists():
            reasons.append(f"missing {fname}")

    shipped = [p for p in SHIPPED_PATH_CANDIDATES if (repo / p).exists()]
    shipped += [d for d in discover_python_package_dirs(repo) if d not in shipped]

    stamped, total = compute_spdx_coverage(repo, shipped)
    coverage = (stamped / total) if total else 1.0
    if total > 0 and coverage < COVERAGE_THRESHOLD:
        reasons.append(f"spdx_coverage={coverage:.2f}")

    should_advise = bool(reasons)
    advisory = (
        "Repo is missing standard attribution layers — "
        "run `python scripts/attribution_stamp.py --repo <path>`"
        if should_advise
        else None
    )
    return {
        "should_advise": should_advise,
        "reasons": reasons,
        "advisory": advisory,
        "spdx_coverage": round(coverage, 2) if total else None,
        "shipped_paths": shipped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect canonical attribution layers in a repo.")
    parser.add_argument("--workdir", default=".", help="Repo root to inspect.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON (default).")
    args = parser.parse_args(argv)

    repo = Path(args.workdir).resolve()
    result = detect(repo)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
