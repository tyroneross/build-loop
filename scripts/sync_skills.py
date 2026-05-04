#!/usr/bin/env python3
"""
Drift-detection for build-loop's native skills copied from canonical upstream repos
(NavGator, claude-code-debugger).

Walks skills/architecture/ and skills/debugging/, reads each SKILL.md's `source:` and
`source_hash:` frontmatter fields, recomputes the SHA-256 of the canonical source file,
and reports drift.

Read-only. Never auto-updates a skill — exit code signals whether refresh is needed.

Exit codes:
    0 — all skills clean
    1 — drift detected (or canonical source missing)
    2 — internal error (unreadable file, malformed frontmatter)

Usage:
    python3 scripts/sync_skills.py [--json]
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sys
from typing import Optional


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
HOME = pathlib.Path.home()
SKILL_TREES = ("skills/architecture", "skills/debugging")


def read_frontmatter(path: pathlib.Path) -> dict[str, str]:
    """Return YAML-ish frontmatter as a flat dict. Tolerates simple `key: value` lines only."""
    try:
        text = path.read_text()
    except OSError as exc:
        raise SystemExit(f"[sync-skills] cannot read {path}: {exc}") from exc
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}
    fm: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fm[key.strip()] = value.strip().strip('"').strip("'")
    return fm


def find_canonical(rel_path: str) -> Optional[pathlib.Path]:
    """Search common parent dirs for the canonical source path."""
    candidates = [
        HOME / "dev" / "git-folder",
        REPO_ROOT.parent,
        pathlib.Path.cwd(),
    ]
    for parent in candidates:
        path = parent / rel_path
        if path.exists():
            return path
    return None


def main() -> int:
    as_json = "--json" in sys.argv
    drift: list[dict[str, str]] = []
    checked = 0

    for tree in SKILL_TREES:
        tree_root = REPO_ROOT / tree
        if not tree_root.exists():
            continue
        for skill_md in tree_root.rglob("SKILL.md"):
            fm = read_frontmatter(skill_md)
            source = fm.get("source")
            expected = fm.get("source_hash")
            rel = str(skill_md.relative_to(REPO_ROOT))
            if not source or not expected:
                # Not a synced skill, skip silently
                continue
            checked += 1
            canonical = find_canonical(source)
            if canonical is None:
                drift.append({
                    "skill": rel,
                    "kind": "MISSING",
                    "detail": f"canonical source not found: {source}",
                    "expected_hash": expected,
                    "actual_hash": None,
                })
                continue
            actual = hashlib.sha256(canonical.read_bytes()).hexdigest()
            if actual != expected:
                drift.append({
                    "skill": rel,
                    "kind": "DRIFT",
                    "detail": f"expected {expected[:12]} got {actual[:12]}",
                    "expected_hash": expected,
                    "actual_hash": actual,
                    "source": source,
                })

    if as_json:
        print(json.dumps({"checked": checked, "drift_count": len(drift), "drift": drift}, indent=2))
    else:
        print(f"Checked: {checked} skills")
        if not drift:
            print("Status: clean — no drift")
        else:
            print(f"Status: {len(drift)} drift(s)")
            for entry in drift:
                print(f"  [{entry['kind']}] {entry['skill']}")
                print(f"          {entry['detail']}")
            print()
            print("Refresh: read the canonical source, re-author the local SKILL.md preserving")
            print("  build-loop framing (skill name, sibling references), recompute source_hash,")
            print("  then re-run scripts/sync_skills.py to confirm.")

    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
