#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Re-validate self-review findings against current code and disposition the closed ones.

A queued finding describes the repo as it was when the finding was written. Nothing
re-checks it, so a finding fixed weeks ago keeps being offered as open work. Measured
2026-08-21 on `.build-loop/proposals/`: of 70 `self_missing_test` findings, **24 (34%)
already had a test**. The same failure shape closed the enforce-from-retro lane —
a verdict that lives nowhere the code can read is a verdict the tooling ignores.

Only MECHANICALLY CHECKABLE kinds are re-validated. `self_missing_test` says "no test
file for X", which is a filesystem question with a yes/no answer. Kinds like
`self_complexity_high_complexity` or `user_correction_cluster` are judgment calls and
are deliberately left alone: auto-closing those would trade a stale queue for a
silently-emptied one, which is worse.

Dry-run by default. `--apply` appends a disposition block to each resolved finding;
the scanner side (`enforce_retro_signals._is_dispositioned`) already honours a checked
box, so a dispositioned finding stops being re-offered.

    python3 scripts/revalidate_self_review_findings.py --workdir . --json
    python3 scripts/revalidate_self_review_findings.py --workdir . --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROPOSALS_SUBDIR = Path(".build-loop") / "proposals"
# Paths that are copies, caches, or other runs — never the live source of truth.
EXCLUDED_PARTS = ("plugin-artifacts", "worktrees", "__pycache__", ".build-loop")
MISSING_TEST_KIND = "self_missing_test"
_MISSING_TEST_TARGET_RE = re.compile(r"no test file for\s+`?([^\s`*\n]+)", re.IGNORECASE)
_DISPOSED_RE = re.compile(r"^\s*-\s*\[[xX]\]", re.MULTILINE)
DISPOSITION_HEADING = "## Re-validated"


def _live(path: Path) -> bool:
    return not any(part in str(path) for part in EXCLUDED_PARTS)


def resolve_source(root: Path, name: str) -> Path | None:
    """Findings name a bare filename; resolve it to a real file in the repo.

    Resolving from the repo root alone reports every bare name as deleted — that
    mistake turned a 34% stale rate into a false 100% on the first measurement.
    """
    direct = root / name
    if direct.exists():
        return direct
    hits = sorted(h for h in root.rglob(Path(name).name) if _live(h))
    return hits[0] if hits else None


def find_test(root: Path, stem: str) -> Path | None:
    hits = sorted(h for h in root.rglob(f"test_{stem}.py") if _live(h))
    return hits[0] if hits else None


def classify(root: Path, body: str) -> tuple[str, str, str]:
    """Return (verdict, target, evidence) for one finding body.

    verdict: resolved | open | source-gone | not-checkable | already-dispositioned
    """
    if _DISPOSED_RE.search(body):
        return "already-dispositioned", "", ""
    if MISSING_TEST_KIND not in body:
        return "not-checkable", "", ""
    m = _MISSING_TEST_TARGET_RE.search(body)
    if not m:
        return "not-checkable", "", ""
    target = m.group(1).strip().rstrip(".,)")
    src = resolve_source(root, target)
    if src is None:
        return "source-gone", target, ""
    test = find_test(root, Path(target).stem)
    if test is not None:
        return "resolved", target, str(test)
    return "open", target, str(src)


def disposition_block(verdict: str, target: str, evidence: str, today: str) -> str:
    reason = (f"a test now exists at `{evidence}`" if verdict == "resolved"
              else f"`{target}` no longer exists in the tree")
    return (f"\n\n{DISPOSITION_HEADING} {today}\n\n"
            f"- [x] RESOLVED — {reason}.\n\n"
            f"Closed by `scripts/revalidate_self_review_findings.py`. The finding "
            f"described the repo when it was written; it no longer holds.\n")


def revalidate(workdir: Path, *, apply: bool = False, today: str = "") -> dict[str, Any]:
    root = workdir
    proposals = workdir / PROPOSALS_SUBDIR
    out: dict[str, Any] = {"scanned": 0, "resolved": 0, "open": 0, "source_gone": 0,
                           "not_checkable": 0, "already_dispositioned": 0,
                           "applied": 0, "items": []}
    if not proposals.is_dir():
        return out
    for f in sorted(proposals.glob("self-review-*.md")):
        try:
            body = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        out["scanned"] += 1
        verdict, target, evidence = classify(root, body)
        key = {"resolved": "resolved", "open": "open", "source-gone": "source_gone",
               "not-checkable": "not_checkable",
               "already-dispositioned": "already_dispositioned"}[verdict]
        out[key] += 1
        if verdict in ("resolved", "source-gone"):
            out["items"].append({"file": f.name, "verdict": verdict,
                                 "target": target, "evidence": evidence})
            if apply:
                f.write_text(body.rstrip() + disposition_block(verdict, target, evidence, today))
                out["applied"] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", default=".", help="repo root (default: cwd)")
    ap.add_argument("--apply", action="store_true",
                    help="write dispositions (default: dry-run, report only)")
    ap.add_argument("--today", default="", help="date stamp for the disposition block")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    res = revalidate(Path(a.workdir).resolve(), apply=a.apply, today=a.today or "(undated)")
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"scanned={res['scanned']} resolved={res['resolved']} open={res['open']} "
              f"source_gone={res['source_gone']} not_checkable={res['not_checkable']} "
              f"already_dispositioned={res['already_dispositioned']} applied={res['applied']}")
        for i in res["items"][:20]:
            print(f"  {i['verdict']:12s} {i['target'][:40]:40s} {i['evidence'][:44]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
