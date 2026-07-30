"""Test that bridge-skill prose drift is cleaned up.

Priority 8 of the architecture-awareness follow-up.

The bridges `build-loop:navgator-bridge` and `build-loop:debugger-bridge` were
removed in priority 6 (filesystem cleanup) in favor of native skills under
`skills/architecture/` and the `debugging-memory` skill. This test asserts no
load-bearing skill prose still names the removed bridges.

Excluded paths:
  - `refactor-history/` — historical record of the refactor itself; the
    bridges are mentioned there by design (this IS the history of removing
    them).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "skills"

PATTERN = re.compile(r"build-loop:(navgator-bridge|debugger-bridge)")

# Historical record — explicitly preserved, must not be cleaned.
EXCLUDED_PATH_FRAGMENTS = ("refactor-history/",)


def _grep_skills() -> list[str]:
    """Return list of `path:line:content` hits across skills/ excluding history."""
    if not SKILLS.exists():
        return []
    hits: list[str] = []
    for path in SKILLS.rglob("*.md"):
        rel = path.relative_to(REPO).as_posix()
        if any(frag in rel for frag in EXCLUDED_PATH_FRAGMENTS):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if PATTERN.search(line):
                hits.append(f"{rel}:{lineno}:{line.strip()}")
    return hits


def test_no_bridge_references_in_load_bearing_skills() -> None:
    """No load-bearing skill prose should reference the removed bridge skills."""
    hits = _grep_skills()
    assert hits == [], (
        "Found references to removed bridges in load-bearing skill prose:\n"
        + "\n".join(hits)
    )


def test_refactor_history_preserved() -> None:
    """Historical record should still mention the bridges (sanity check —
    ensures we excluded the right path and didn't accidentally scrub history).

    History uses bare names (`navgator-bridge`, `debugger-bridge`) since those
    were the directory names; the load-bearing-prose check uses the qualified
    `build-loop:` form because that's how skills are invoked.
    """
    history_dir = SKILLS / "build-loop" / "references" / "refactor-history"
    if not history_dir.exists():
        return
    bare_pattern = re.compile(r"\b(navgator-bridge|debugger-bridge)\b")
    found = False
    for path in history_dir.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if bare_pattern.search(text):
            found = True
            break
    assert found, (
        "Expected refactor-history to retain historical references to the "
        "removed bridges. Either history was scrubbed or paths changed."
    )
