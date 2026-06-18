#!/usr/bin/env python3
"""learning_to_draft.py — turn retrospective learning-objects into drafter proposals.

Move A of the recursive-learning pipeline: the recursive-retrospective skill
emits structured learning-objects, each tagged with an ``encoding_target``
(skill / agent / memory / eval / gate / preflight / approval / project_note /
do_not_encode). This converter takes that list and produces the pattern-proposal
shape ``self-improvement-architect`` already consumes, for ONLY the objects
targeted ``skill`` or ``agent`` and marked ``encode: yes``. The Learn phase then
hands each proposal to the drafter, which authors the experimental SKILL.md /
agent .md. Capture -> auto-draft, no human re-keying.

Honest scope (gap #3): targets that are NOT skill/agent (eval, gate, preflight,
approval, memory) have no producer yet. This converter does NOT drop them
silently — it reports them as ``unrouted`` with their target so the gap is
visible, not hidden. Stdlib only; no deps.

Input  (``--in`` JSON, or stdin): a list of learning-objects, each:
    {"title": str, "evidence": [str, ...], "encoding_target": str,
     "scope": "cross-project"|"project-specific"|"local",
     "confidence": "high"|"med"|"low", "encode": "yes"|"no"|"needs_approval",
     "trigger": str (optional), "purpose": str (optional)}

Output (``--out`` JSON): {"proposals": [...], "unrouted": [...], "summary": {...}}
where each proposal matches the drafter contract:
    {"type": "retrospective_pattern", "signature": str, "confidence": str,
     "evidence": [...], "proposal": {"skillSkeleton": {"name","trigger","purpose"}},
     "target_type": "skill"|"agent", "scope": str}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

DRAFTABLE = {"skill", "agent"}
# Targets that are real homes but have no producer wired yet (gap #3) vs. targets
# that intentionally route elsewhere (memory) or nowhere (do_not_encode).
_NO_PRODUCER = {"eval", "gate", "preflight", "approval"}
_ELSEWHERE = {"memory", "project_note", "project-note", "do_not_encode", "do-not-encode"}


def slugify(text: str) -> str:
    """kebab-case slug for an experimental artifact name."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "unnamed")[:60]


def to_proposal(obj: dict) -> dict:
    """Map one skill/agent-targeted learning-object to the drafter contract."""
    title = str(obj.get("title") or "").strip()
    target = str(obj.get("encoding_target") or "").lower()
    name = f"experimental-{slugify(title)}"
    trigger = str(obj.get("trigger") or "").strip() or f"when working in the context of: {title}"
    purpose = str(obj.get("purpose") or "").strip() or title
    evidence = obj.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = [str(evidence)]
    return {
        "type": "retrospective_pattern",
        "signature": title,
        "confidence": str(obj.get("confidence") or "med").lower(),
        "evidence": [str(e) for e in evidence],
        "proposal": {"skillSkeleton": {"name": name, "trigger": trigger, "purpose": purpose}},
        "target_type": target,
        "scope": str(obj.get("scope") or "project-specific"),
    }


def convert(objects: list[dict]) -> dict:
    proposals: list[dict] = []
    unrouted: list[dict] = []
    skipped: list[dict] = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        target = str(obj.get("encoding_target") or "").lower()
        encode = str(obj.get("encode") or "").lower()
        title = str(obj.get("title") or "(untitled)")
        if encode not in ("yes",):
            skipped.append({"title": title, "reason": f"encode={encode or 'unset'}"})
            continue
        if target in DRAFTABLE:
            proposals.append(to_proposal(obj))
        elif target in _NO_PRODUCER:
            # Gap #3: a real target with no producer yet. Surface it loudly.
            unrouted.append({"title": title, "encoding_target": target,
                             "note": "no producer wired (gap #3: needs an enforced-check path)"})
        elif target in _ELSEWHERE:
            skipped.append({"title": title, "reason": f"target={target} (routed elsewhere)"})
        else:
            skipped.append({"title": title, "reason": f"target={target or 'unset'} (unknown)"})
    summary = {
        "total": len(objects),
        "drafted": len(proposals),
        "unrouted_no_producer": len(unrouted),
        "skipped": len(skipped),
        "unrouted_targets": sorted({u["encoding_target"] for u in unrouted}),
    }
    return {"proposals": proposals, "unrouted": unrouted, "skipped": skipped, "summary": summary}


def _load(path: str | None) -> list[dict]:
    raw = sys.stdin.read() if path in (None, "-") else open(path, encoding="utf-8").read()
    data = json.loads(raw)
    if isinstance(data, dict) and "learning_objects" in data:
        data = data["learning_objects"]
    if not isinstance(data, list):
        raise SystemExit("input must be a JSON list of learning-objects (or {learning_objects: [...]})")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert retrospective learning-objects to drafter proposals.")
    ap.add_argument("--in", dest="inp", default="-", help="Input JSON file (default stdin).")
    ap.add_argument("--out", dest="out", default=None, help="Write result JSON here (default stdout).")
    ap.add_argument("--json", action="store_true", help="Emit only the summary to stdout.")
    args = ap.parse_args(argv)

    result = convert(_load(args.inp))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, sort_keys=True)
    payload = result["summary"] if args.json else result
    print(json.dumps(payload, indent=2, sort_keys=True))
    # Non-zero-ish signal is unhelpful here; always succeed. Unrouted is reported, not fatal.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
