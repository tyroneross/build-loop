#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""enforce_retro_signals.py — second signal source for recurring-pattern-detector.

Scans ``.build-loop/proposals/enforce-from-retro/`` (written by the post-push
retrospective at ``scripts/retrospective/write.py:write_enforce_candidates``),
normalizes each candidate body, and counts recurrence across **distinct run
ids**. Emits the same pattern-envelope shape the detector agent emits so the
agent can splice this output directly into its own ``patterns[]`` array.

File contract (writer-side, already in production):
  path: .build-loop/proposals/enforce-from-retro/<run-id>-<NN>.md
  body: "# Enforce candidate — <run-id> #<N>\n\n"
        "_Source: post-push retrospective (<YYYY-MM-DD>)_\n\n"
        "## Candidate\n\n<text>\n\n"
        "## Disposition\n\n- [ ] Adopt as default ..."

We extract the ``## Candidate`` body (up to the next ``##`` heading or EOF)
and use a normalized form as the signature:

  signature = first 120 chars of (text.lower() with whitespace collapsed)

Threshold: signature appears in >= 2 **distinct** run-id prefixes. (One
run dropping the same candidate twice in `-01.md` + `-02.md` counts once.)

CLI: ``python3 scripts/enforce_retro_signals.py --workdir <dir> --json``
prints the envelope to stdout. Library: ``scan(workdir: Path) -> dict``.

Never raises on bad input — degraded inputs are skipped silently per the
detector's "silent skip is correct" rule.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROPOSAL_SUBDIR = Path(".build-loop") / "proposals" / "enforce-from-retro"
SIGNATURE_LEN = 120
# A run-id prefix is everything before the LAST `-NN.md` suffix. The retro
# writer uses `<run-id>-<NN>.md`; run-ids may themselves contain hyphens
# (e.g. `learn-mandatory-20260606-0106`), so we split on the trailing
# `-<digits>.md` pattern only.
_RUN_ID_PATTERN = re.compile(r"^(?P<run_id>.+)-(?P<seq>\d{1,4})\.md$")
_CANDIDATE_HEADING = re.compile(r"^##\s+Candidate\s*$", re.M)
_NEXT_HEADING = re.compile(r"^##\s+\S", re.M)
_WHITESPACE = re.compile(r"\s+")


def _extract_candidate_text(body: str) -> str | None:
    """Pull the body of the ``## Candidate`` section. Returns None on miss."""
    m = _CANDIDATE_HEADING.search(body)
    if not m:
        return None
    start = m.end()
    after = body[start:]
    nxt = _NEXT_HEADING.search(after)
    text = after[: nxt.start()] if nxt else after
    return text.strip()


def _normalize(text: str) -> str:
    """Lowercase + whitespace-collapse + truncate to SIGNATURE_LEN."""
    collapsed = _WHITESPACE.sub(" ", text.strip().lower())
    return collapsed[:SIGNATURE_LEN]


def _parse_run_id(filename: str) -> str | None:
    """Return the run-id prefix or None if the filename does not match."""
    m = _RUN_ID_PATTERN.match(filename)
    return m.group("run_id") if m else None


def _mtime_iso(p: Path) -> str:
    try:
        ts = p.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except OSError:
        return "unknown"


def _skill_skeleton_name(signature: str) -> str:
    """Derive a stable skill-skeleton name from a signature.

    Lowercases, replaces non-alphanumerics with hyphens, collapses runs,
    truncates to 48 chars, prefixes with ``enforce-`` so the architect's
    dedupe layer can group these distinctly from `phase_failure` / etc.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", signature.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)[:48].strip("-") or "candidate"
    return f"enforce-{slug}"


def scan(workdir: Path) -> dict[str, Any]:
    """Scan ``workdir/.build-loop/proposals/enforce-from-retro/`` and return
    an envelope matching the ``recurring-pattern-detector`` schema.

    Returns:
        {
          "scannedFiles": int,
          "patterns": [
            {
              "type": "enforce_recurrence",
              "signature": "<normalized text>",
              "count": int,                    # distinct run-ids
              "confidence": "high" | "medium",
              "evidence": [ {"date": "...", "goal": "", "detail": "..."}, ... ],
              "proposal": {
                "skillSkeleton": {
                  "name": "enforce-<slug>",
                  "trigger": "when the same retro enforce-candidate recurs across runs",
                  "purpose": "Adopt the recurring retro enforce-candidate as a default."
                }
              }
            }
          ]
        }
    """
    proposals_dir = workdir / PROPOSAL_SUBDIR
    envelope: dict[str, Any] = {"scannedFiles": 0, "patterns": []}
    if not proposals_dir.is_dir():
        return envelope

    # Group: signature -> {run_ids: set, evidence: [...]}
    buckets: dict[str, dict[str, Any]] = {}
    scanned = 0
    for p in sorted(proposals_dir.iterdir()):
        if not p.is_file() or p.suffix != ".md":
            continue
        run_id = _parse_run_id(p.name)
        if run_id is None:
            continue
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            continue
        scanned += 1
        text = _extract_candidate_text(body)
        if not text:
            continue
        sig = _normalize(text)
        if not sig:
            continue
        bucket = buckets.setdefault(sig, {"run_ids": set(), "evidence": []})
        bucket["run_ids"].add(run_id)
        if len(bucket["evidence"]) < 5:
            bucket["evidence"].append({
                "date": _mtime_iso(p),
                "goal": "",
                "detail": text[:200],
                "run_id": run_id,
                "file": str(p.relative_to(workdir)),
            })

    envelope["scannedFiles"] = scanned

    for sig, bucket in buckets.items():
        run_count = len(bucket["run_ids"])
        if run_count < 2:
            continue  # below threshold
        # confidence: high at >=4 distinct runs, medium otherwise (matches
        # the detector's generic "threshold x 2 == high" rule of thumb).
        confidence = "high" if run_count >= 4 else "medium"
        envelope["patterns"].append({
            "type": "enforce_recurrence",
            "signature": sig,
            "count": run_count,
            "confidence": confidence,
            "evidence": bucket["evidence"],
            "proposal": {
                "skillSkeleton": {
                    "name": _skill_skeleton_name(sig),
                    "trigger": (
                        "when the same retro enforce-candidate recurs across "
                        ">=2 runs (cross-run enforce-recurrence signal)"
                    ),
                    "purpose": (
                        "Adopt the recurring retro enforce-candidate as a "
                        "default project rule so it stops being repeatedly "
                        "prompted as a fresh candidate."
                    ),
                },
            },
        })

    # Deterministic order: highest count first, then signature alphabetic.
    envelope["patterns"].sort(key=lambda p: (-p["count"], p["signature"]))
    return envelope


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Scan .build-loop/proposals/enforce-from-retro/ for "
            "cross-run enforce-candidate recurrence (>=2 distinct run-ids)."
        ),
    )
    ap.add_argument("--workdir", default=".", help="Project workdir (default '.').")
    ap.add_argument("--json", action="store_true",
                    help="Emit envelope as JSON to stdout (default).")
    args = ap.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    envelope = scan(workdir)
    print(json.dumps(envelope, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
