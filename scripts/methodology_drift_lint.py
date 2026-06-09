#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Methodology drift lint — single-source guard for the four methodology docs.

Background (WP-A, 2026-06-09): the phase sequence, Review sub-step ordering, and
phase-count headline were hand-maintained in four files — CLAUDE.md, AGENTS.md,
skills/build-loop/SKILL.md, agents/build-orchestrator.md — and drifted. One file
dropped Auto-Resolve from the Review sub-steps; another dropped Optimize. This
lint makes `references/methodology-core.md` the single source: it parses the
canonical phrasings declared there and verifies each appears verbatim in every
satellite that covers that fact.

It is a PHRASE-PRESENCE check, not a byte-diff: the four files keep their own
format (README / open-standard spec / skill router / agent prompt). The guard
targets the load-bearing phrasings whose disagreement was the observed failure.

Each invariant in methodology-core.md declares a canonical phrase via a blockquote
immediately under a line containing "Canonical phrase". The phrase is the text of
that blockquote (the `> ` line). The invariant also declares which satellites must
carry it; by default every satellite is required. A satellite may be exempted from
a given invariant only via SATELLITE_EXEMPTIONS below, with a written reason.

Usage:
    python3 scripts/methodology_drift_lint.py            # human-readable
    python3 scripts/methodology_drift_lint.py --json     # machine-readable
    python3 scripts/methodology_drift_lint.py --strict   # exit 1 on any drift (CI)

Exit codes: 0 clean (or non-strict); 1 drift found under --strict; 2 setup error
(canonical file missing / no invariants parsed) so a broken guard fails loudly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
CANONICAL = REPO_ROOT / "references" / "methodology-core.md"

SATELLITES = (
    "CLAUDE.md",
    "AGENTS.md",
    "skills/build-loop/SKILL.md",
    "agents/build-orchestrator.md",
)

# A satellite may legitimately not cover a given invariant (e.g. a file that
# never states the iterate cap). Exempt it here WITH A REASON; an unexempted
# satellite missing a canonical phrase is drift.
SATELLITE_EXEMPTIONS: dict[str, set[str]] = {
    # invariant_id -> set of satellite paths exempt from carrying its phrase
    # Populated only with evidenced, documented exemptions.
}

# Matches a blockquote line: "> some phrase"
BLOCKQUOTE_RE = re.compile(r"^>\s?(.+?)\s*$", re.MULTILINE)
# Matches an invariant heading: "### INV-SOMETHING" (optional trailing suffix
# such as "  (ENFORCED)" is allowed and ignored).
INV_HEADING_RE = re.compile(r"^###\s+(INV-[A-Z0-9-]+)\b.*$", re.MULTILINE)


def parse_canonical(text: str) -> list[dict]:
    """Return [{id, phrases:[...]}] — every canonical phrase under each invariant.

    A canonical phrase is the blockquote that follows a line containing
    "Canonical phrase" within an invariant section.
    """
    invariants: list[dict] = []
    # Split the doc into invariant sections by the ### INV- headings.
    headings = list(INV_HEADING_RE.finditer(text))
    for i, h in enumerate(headings):
        inv_id = h.group(1)
        start = h.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section = text[start:end]
        phrases: list[str] = []
        # Find each "Canonical phrase" marker and take the next blockquote.
        for m in re.finditer(r"Canonical phrase[^\n]*\n", section):
            after = section[m.end():]
            bq = BLOCKQUOTE_RE.search(after)
            if bq:
                phrases.append(bq.group(1).strip())
        if phrases:
            invariants.append({"id": inv_id, "phrases": phrases})
    return invariants


def check(repo_root: Path) -> dict:
    # Derive the canonical file FROM repo_root so --repo-root and tests relocate
    # the whole check (canonical + satellites), not just the satellites. The
    # module-global CANONICAL is only the default for the real repo.
    canonical = repo_root / "references" / "methodology-core.md"
    if not canonical.is_file():
        return {"error": f"canonical file missing: {canonical}", "findings": []}
    canon_text = canonical.read_text(encoding="utf-8")
    invariants = parse_canonical(canon_text)
    if not invariants:
        return {"error": "no canonical invariants parsed from methodology-core.md", "findings": []}

    sat_text: dict[str, str] = {}
    for sat in SATELLITES:
        p = repo_root / sat
        sat_text[sat] = p.read_text(encoding="utf-8") if p.is_file() else None

    findings: list[dict] = []
    for inv in invariants:
        for sat in SATELLITES:
            if sat in SATELLITE_EXEMPTIONS.get(inv["id"], set()):
                continue
            text = sat_text[sat]
            if text is None:
                findings.append({"invariant": inv["id"], "satellite": sat, "issue": "satellite file missing"})
                continue
            for phrase in inv["phrases"]:
                if phrase not in text:
                    findings.append({
                        "invariant": inv["id"],
                        "satellite": sat,
                        "missing_phrase": phrase,
                        "issue": "canonical phrase not found verbatim",
                    })
    return {"error": None, "invariants_checked": [i["id"] for i in invariants], "findings": findings}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any drift (CI gate)")
    ap.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = ap.parse_args(argv)

    result = check(args.repo_root)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["error"]:
            print(f"SETUP ERROR: {result['error']}", file=sys.stderr)
        elif not result["findings"]:
            print(f"No methodology drift. Checked {len(result['invariants_checked'])} invariants "
                  f"across {len(SATELLITES)} satellites.")
        else:
            print(f"Methodology drift — {len(result['findings'])} finding(s):\n")
            for f in result["findings"]:
                if "missing_phrase" in f:
                    print(f"  [{f['invariant']}] {f['satellite']}: missing canonical phrase\n"
                          f"      expected: {f['missing_phrase']!r}")
                else:
                    print(f"  [{f['invariant']}] {f['satellite']}: {f['issue']}")
            print("\nFix: update each named satellite to carry the canonical phrasing from "
                  "references/methodology-core.md, or add a documented exemption.")

    if result["error"]:
        return 2
    if args.strict and result["findings"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
