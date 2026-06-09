#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Reference activation audit — make "references are top of mind when needed" checkable.

A reference doc only earns its keep if it is *reachable* from something that
actually loads it at the right moment: an owning SKILL.md, a phase doc, an
agent, a script, or the references INDEX. This gate fails when a reference is
orphaned, drifted across copies, mis-filed, or oversized-without-a-summary —
the failure modes that silently make a doc invisible to the running agent.

Scope notes:
- `plugin-artifacts/**` is a BUILD-GENERATED mirror, not a source of truth.
  It is excluded from every detector (auditing it would double-count and flag
  the mirror against itself).
- `archive/**` is historical and excluded.

Exit code: 0 = clean, 1 = findings (gating), 2 = usage/IO error.

Usage:
    python3 scripts/reference_activation_audit.py [--root <repo>] [--json]
                                                  [--max-lines 600]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

# Directory segments that are mirrors/history, never a source of truth.
EXCLUDED_SEGMENTS = ("plugin-artifacts", "archive", "node_modules", "__pycache__")

# Markers that classify a reference as a deprecated shim.
SHIM_NAME_HINTS = (".alt.",)
SHIM_CONTENT_RE = re.compile(r"\b(deprecated|shim|superseded|legacy stub)\b", re.IGNORECASE)
# An acceptable expiry declaration on a shim.
EXPIRY_RE = re.compile(r"\b(expires?|remove[- ]after|sunset|delete[- ]after)\b[:=]", re.IGNORECASE)

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"


@dataclass
class Finding:
    rule: str
    severity: str
    path: str
    detail: str


def _excluded(rel: Path) -> bool:
    return any(seg in EXCLUDED_SEGMENTS for seg in rel.parts)


def _in_references_dir(rel: Path) -> bool:
    return "references" in rel.parts


def reference_md_files(root: Path) -> list[Path]:
    """All reference markdown docs that are sources of truth (absolute paths)."""
    out: list[Path] = []
    for p in root.rglob("*.md"):
        rel = p.relative_to(root)
        if _excluded(rel) or not _in_references_dir(rel):
            continue
        out.append(p)
    return sorted(out)


def reachability_corpus(root: Path) -> str:
    """Concatenated text of everything that can legitimately surface a reference:
    owning SKILL.md files, agents, commands, phase/loader scripts, AGENTS.md, and
    the references INDEX. Searched as a single haystack for basename mentions."""
    parts: list[str] = []
    globs = [
        "skills/**/SKILL.md",
        "agents/*.md",
        "commands/**/*.md",
        "scripts/**/*.py",
        "references/**/*.md",  # INDEX.md and phase docs that load other refs
        "AGENTS.md",
        "CLAUDE.md",
    ]
    for g in globs:
        for p in root.glob(g):
            rel = p.relative_to(root)
            if _excluded(rel):
                continue
            try:
                parts.append(p.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    return "\n".join(parts)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _line_count(p: Path) -> int:
    try:
        return sum(1 for _ in p.open("rb"))
    except OSError:
        return 0


def detect_dup_drift(root: Path, refs: list[Path]) -> list[Finding]:
    """Duplicate basenames whose content DIFFERS — two sources of truth that disagree.
    Identical duplicates are benign (intentional co-location) and not flagged here."""
    by_base: dict[str, list[Path]] = {}
    for p in refs:
        by_base.setdefault(p.name, []).append(p)
    findings: list[Finding] = []
    for base, paths in sorted(by_base.items()):
        if len(paths) < 2:
            continue
        shas = {_sha(p) for p in paths}
        if len(shas) > 1:
            rels = ", ".join(str(p.relative_to(root)) for p in sorted(paths))
            findings.append(
                Finding(
                    "dup_drift",
                    SEVERITY_ERROR,
                    base,
                    f"{len(paths)} copies with DIFFERENT content (drifted sources of truth): {rels}",
                )
            )
    return findings


def detect_orphans(root: Path, refs: list[Path], corpus: str) -> list[Finding]:
    """Reference docs not mentioned by any loader/owner/INDEX."""
    findings: list[Finding] = []
    for p in refs:
        if p.name == "INDEX.md":
            continue  # the index itself is the map, not a mapped doc
        # A doc is reachable if its basename appears anywhere in the corpus
        # outside its own file. The corpus excludes the doc's own text only
        # incidentally; a self-mention inside the doc still counts as content,
        # so we strip the doc's own body from the search to avoid false-clears.
        own = ""
        try:
            own = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass
        haystack = corpus.replace(own, "") if own else corpus
        if p.name not in haystack:
            findings.append(
                Finding(
                    "orphan",
                    SEVERITY_ERROR,
                    str(p.relative_to(root)),
                    "not referenced by any SKILL.md, agent, script, phase doc, or references/INDEX.md",
                )
            )
    return findings


def detect_ds_store(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for p in root.rglob(".DS_Store"):
        rel = p.relative_to(root)
        if "node_modules" in rel.parts:
            continue
        if _in_references_dir(rel):
            findings.append(Finding("ds_store", SEVERITY_ERROR, str(rel), "OS cruft committed under a references/ dir"))
    return findings


def detect_skill_local_unmentioned(root: Path, refs: list[Path]) -> list[Finding]:
    """A skill-local reference must be named by its OWNING SKILL.md, else it is
    invisible to the only agent that should load it."""
    findings: list[Finding] = []
    for p in refs:
        rel = p.relative_to(root)
        parts = rel.parts
        # shape: skills/<skill>/references/<...>.md
        if len(parts) < 4 or parts[0] != "skills" or parts[2] != "references":
            continue
        if p.name == "INDEX.md":
            continue
        skill_md = root / "skills" / parts[1] / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            txt = skill_md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if p.name not in txt:
            findings.append(
                Finding(
                    "skill_local_unmentioned",
                    SEVERITY_WARN,
                    str(rel),
                    f"skill-local reference not named by its owner skills/{parts[1]}/SKILL.md",
                )
            )
    return findings


def detect_shim_no_expiry(root: Path, refs: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for p in refs:
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        name_shim = any(h in p.name for h in SHIM_NAME_HINTS)
        content_shim = bool(SHIM_CONTENT_RE.search(txt[:2000]))  # marker should be near the top
        if (name_shim or content_shim) and not EXPIRY_RE.search(txt):
            findings.append(
                Finding(
                    "shim_no_expiry",
                    SEVERITY_WARN,
                    str(p.relative_to(root)),
                    "looks like a deprecated/shim doc but has no expiry marker (expires:/remove-after:)",
                )
            )
    return findings


def detect_oversized_no_index(root: Path, refs: list[Path]) -> list[Finding]:
    index_text = ""
    idx = root / "references" / "INDEX.md"
    if idx.exists():
        index_text = idx.read_text(encoding="utf-8", errors="ignore")
    findings: list[Finding] = []
    for p in refs:
        if p.name == "INDEX.md":
            continue
        n = _line_count(p)
        if n > MAX_LINES_DEFAULT_HOLDER[0] and p.name not in index_text:
            findings.append(
                Finding(
                    "oversized_no_index",
                    SEVERITY_WARN,
                    str(p.relative_to(root)),
                    f"{n} lines (> {MAX_LINES_DEFAULT_HOLDER[0]}) without a references/INDEX.md entry — large doc the model won't read whole",
                )
            )
    return findings


# Mutable holder so detect_oversized_no_index can read the configured threshold
# without threading it through every signature.
MAX_LINES_DEFAULT_HOLDER = [600]


def run_audit(root: Path, max_lines: int = 600) -> list[Finding]:
    MAX_LINES_DEFAULT_HOLDER[0] = max_lines
    refs = reference_md_files(root)
    corpus = reachability_corpus(root)
    findings: list[Finding] = []
    findings += detect_dup_drift(root, refs)
    findings += detect_orphans(root, refs, corpus)
    findings += detect_ds_store(root)
    findings += detect_skill_local_unmentioned(root, refs)
    findings += detect_shim_no_expiry(root, refs)
    findings += detect_oversized_no_index(root, refs)
    return findings


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reference activation audit for build-loop.")
    p.add_argument("--root", default=None, help="Repo root (default: parent of scripts/)")
    p.add_argument("--json", action="store_true", help="Emit findings as JSON")
    p.add_argument("--max-lines", type=int, default=600, help="Oversized-doc threshold (default 600)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    if not root.is_dir():
        print(f"error: root not a directory: {root}", file=sys.stderr)
        return 2
    findings = run_audit(root, max_lines=args.max_lines)
    errors = [f for f in findings if f.severity == SEVERITY_ERROR]

    if args.json:
        print(json.dumps(
            {
                "root": str(root),
                "finding_count": len(findings),
                "error_count": len(errors),
                "findings": [asdict(f) for f in findings],
            },
            indent=2,
        ))
    else:
        if not findings:
            print("reference activation audit: clean ✅")
        else:
            for f in findings:
                mark = "✗" if f.severity == SEVERITY_ERROR else "⚠"
                print(f"{mark} [{f.rule}] {f.path}\n    {f.detail}")
            print(f"\n{len(errors)} error(s), {len(findings) - len(errors)} warning(s)")

    # Gate on errors only; warnings are advisory and do not fail the gate.
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
