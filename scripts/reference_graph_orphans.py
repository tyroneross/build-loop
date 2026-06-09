#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Reference-graph orphan check — which scripts/ are referenced from skills/commands/agents/hooks/CI; emit a PROPOSE-removal list (never auto-delete).
#   application: meta
#   status: active
"""Reference-graph orphan check (WP-E item 2) — propose, never delete.

Walks every top-level scripts/*.py and asks: is this script referenced anywhere
in the loadable surfaces (skills, commands, agents, hooks, .github CI, docs, AND
other scripts)? A script referenced ZERO times is an ORPHAN CANDIDATE.

This is deliberately a PROPOSE tool, not a delete tool (WP-E spec + the standing
"measure tool usage before removing" feedback). Two independent lenses must agree
before any removal:
  1. THIS reference-graph (static): no loadable surface names the script.
  2. Transcript/usage data (dynamic): the script was never invoked.
A script can be referenced only dynamically (a shell string `python3 scripts/x.py`
that this grep does catch, OR an orchestrator that calls it by name in prose) —
so a static orphan is a CANDIDATE for human review, never an automatic removal.

Reference detection (per script `name`):
  - the literal `name.py` (catches `scripts/name.py`, `python name.py`, import
    paths written as paths) anywhere in a referencing surface;
  - the bare module token `name` as a Python import (`import name`,
    `from name import`) in another script.
The script's own file and its colocated `test_name.py` never count as references.

Stdlib only (ast, json, re, pathlib, argparse, sys). Read-only — emits a report;
makes NO filesystem changes beyond an optional --output json file.

CLI::

    python3 scripts/reference_graph_orphans.py --workdir <repo> [--json] [--output FILE]

Exit codes: 0 always (advisory; orphans are a proposal, not a failure); 2 setup error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Surfaces that can reference a script. Other scripts are included so a script
# used only as a library by another script is NOT flagged orphan.
REFERENCING_DIRS = ("skills", "commands", "agents", "hooks", ".github", "docs", "scripts")
REFERENCING_FILES = ("AGENTS.md", "CLAUDE.md", "README.md")
REFERENCING_SUFFIXES = (".md", ".json", ".yml", ".yaml", ".sh", ".py", ".toml")


def _iter_referencing_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for d in REFERENCING_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in REFERENCING_SUFFIXES:
                out.append(p)
    for f in REFERENCING_FILES:
        p = root / f
        if p.is_file():
            out.append(p)
    return out


def _script_names(scripts_dir: Path) -> list[str]:
    names: list[str] = []
    for p in sorted(scripts_dir.glob("*.py")):
        n = p.stem
        if n.startswith("__") or n.startswith("test_"):
            continue
        names.append(n)
    return names


def find_orphans(root: Path) -> dict[str, Any]:
    scripts_dir = root / "scripts"
    if not scripts_dir.is_dir():
        return {"error": f"no scripts/ dir under {root}", "orphans": [], "checked": 0}
    names = _script_names(scripts_dir)
    files = _iter_referencing_files(root)
    # Read each referencing file once; build a single corpus per file so we scan
    # O(files), not O(files*names).
    bodies: list[tuple[Path, str]] = []
    for p in files:
        try:
            bodies.append((p, p.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue

    orphans: list[str] = []
    for name in names:
        own = {f"{name}.py", f"test_{name}.py"}
        dotpy = f"{name}.py"
        import_re = re.compile(rf"\b(?:import|from)\s+{re.escape(name)}\b")
        referenced = False
        for p, body in bodies:
            if p.name in own:
                continue
            if dotpy in body or import_re.search(body):
                referenced = True
                break
        if not referenced:
            orphans.append(name)
    return {
        "error": None,
        "checked": len(names),
        "orphan_count": len(orphans),
        "orphans": sorted(orphans),
        "note": ("PROPOSE-removal candidates — static reference-graph only. "
                 "Confirm against transcript/usage data + human review before any "
                 "removal (WP-E: never auto-delete; measure usage first)."),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workdir", type=Path, default=Path.cwd())
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--output", type=Path, default=None, help="write JSON report to this path")
    args = ap.parse_args(argv)

    root = args.workdir.resolve()
    if not root.is_dir():
        print(f"setup error: workdir not a directory: {root}", file=sys.stderr)
        return 2

    result = find_orphans(root)
    if result.get("error"):
        print(f"setup error: {result['error']}", file=sys.stderr)
        return 2

    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"reference-graph orphan check: {result['orphan_count']}/{result['checked']} "
              f"scripts have zero static references (PROPOSE-removal — verify usage first)")
        for o in result["orphans"]:
            print(f"  - {o}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
