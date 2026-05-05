#!/usr/bin/env python3
"""Capability shortlist matcher.

Reads `.build-loop/capability-registry.json` and returns ≤8 entries scored
against a (phase, intent) pair. Stdlib only.

CLI:
    python3 capability_shortlist.py --phase 1 --intent "scan architecture and find blast radius" [--kind agent skill] [--workdir .] [--json]

Used by `skills/capabilities/SKILL.md` and consumed by the build-orchestrator
at Phase 1 Assess.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]
SHORTLIST_CAP = 8

PHASE_CATEGORIES: Dict[int, Dict[str, List[str]]] = {
    1: {"primary": ["architecture", "planning", "memory", "observability"], "secondary": ["meta"]},
    2: {"primary": ["planning", "architecture", "validation"], "secondary": ["meta"]},
    3: {"primary": ["execution", "debugging", "ux-ui", "deployment"], "secondary": ["testing"]},
    4: {"primary": ["validation", "debugging", "ux-ui", "optimization"], "secondary": ["testing"]},
    5: {"primary": ["debugging", "execution", "validation"], "secondary": ["architecture"]},
    6: {"primary": ["meta", "memory", "optimization"], "secondary": ["validation"]},
}

TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{2,}")

# Common English stop-words and intent-fluff words that match too broadly
# (`and`, `for`, etc. land hits in nearly every description). The intent
# matcher already enforces len >= 3, so 2-letter words are also out.
STOP_WORDS = frozenset({
    "and", "the", "for", "with", "from", "this", "that", "these",
    "those", "into", "onto", "than", "then", "but", "are", "was",
    "were", "have", "has", "had", "you", "your", "our", "their",
    "via", "out", "all", "any", "some", "more", "less",
    "find", "show", "show", "make", "use", "uses", "used",
    "issues", "issue", "things", "thing", "stuff",
})


def tokenize(text: str) -> List[str]:
    return [
        t.lower() for t in TOKEN_RE.findall(text or "")
        if t.lower() not in STOP_WORDS
    ]


def ensure_registry(workdir: Path) -> Dict[str, Any]:
    """Read the registry; rebuild it if missing or unreadable."""
    reg_path = workdir / ".build-loop" / "capability-registry.json"
    if reg_path.is_file():
        try:
            return json.loads(reg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    builder = workdir / "scripts" / "build_capability_registry.py"
    if not builder.is_file():
        return {"entries": [], "total": 0}
    subprocess.run(
        [sys.executable, str(builder), "--workdir", str(workdir)],
        check=False,
        capture_output=True,
    )
    if reg_path.is_file():
        try:
            return json.loads(reg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"entries": [], "total": 0}
    return {"entries": [], "total": 0}


def score_entry(
    entry: Dict[str, Any],
    intent_tokens: List[str],
    primary: List[str],
    secondary: List[str],
) -> tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []
    haystack = " ".join([
        entry.get("name", ""),
        entry.get("description", ""),
        " ".join(entry.get("triggers", []) or []),
    ]).lower()
    for tok in intent_tokens:
        # Word-boundary match against haystack — minimum 3 chars to avoid noise.
        if len(tok) < 3:
            continue
        if re.search(r"(^|[\s_\-/.])" + re.escape(tok) + r"($|[\s_\-/.])", haystack):
            score += 5
            reasons.append(f"intent:{tok}")
    cat = entry.get("category", "")
    if cat in primary:
        score += 3
        reasons.append(f"primary:{cat}")
    elif cat in secondary:
        score += 1
        reasons.append(f"secondary:{cat}")
    if entry.get("tier") in ("sonnet", "opus"):
        score += 1
        reasons.append(f"tier:{entry['tier']}")
    return score, reasons


def shortlist(
    registry: Dict[str, Any],
    phase: int,
    intent: str,
    kinds: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = list(registry.get("entries", []) or [])
    if kinds:
        kinds_set = set(kinds)
        entries = [e for e in entries if e.get("kind") in kinds_set]

    intent_tokens = tokenize(intent)
    pc = PHASE_CATEGORIES.get(phase, {"primary": [], "secondary": []})
    primary = pc.get("primary", []) or []
    secondary = pc.get("secondary", []) or []

    scored: List[tuple[int, List[str], Dict[str, Any]]] = []
    for e in entries:
        s, reasons = score_entry(e, intent_tokens, primary, secondary)
        if s > 0:
            scored.append((s, reasons, e))

    # If nothing matched intent, fall back to phase-category-only ranking
    # so the orchestrator never gets an empty list.
    if not scored:
        for e in entries:
            cat = e.get("category", "")
            if cat in primary or cat in secondary:
                base = 3 if cat in primary else 1
                scored.append((base, [f"phase-only:{cat}"], e))

    scored.sort(key=lambda t: (-t[0], t[2].get("name", "")))
    out_entries = []
    for s, reasons, e in scored[:SHORTLIST_CAP]:
        out_entries.append({
            "name": e.get("name"),
            "kind": e.get("kind"),
            "category": e.get("category"),
            "score": s,
            "reasons": reasons,
            "source_path": e.get("source_path"),
            "description": e.get("description"),
        })
    return {
        "phase": phase,
        "intent": intent,
        "shortlist_size": len(out_entries),
        "registry_total": registry.get("total", len(entries)),
        "results": out_entries,
    }


def cache_into_state(workdir: Path, result: Dict[str, Any]) -> None:
    """Append a compact summary to .build-loop/state.json.activeCapabilities[].

    Best-effort: failures are ignored (this is a hint surface, not source
    of truth).
    """
    state_path = workdir / ".build-loop" / "state.json"
    if not state_path.parent.exists():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return
    cap_list = state.get("activeCapabilities") or []
    cap_list.append({
        "phase": result["phase"],
        "intent": result["intent"][:240],
        "shortlist": [r["name"] for r in result["results"]],
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    })
    # Keep last 50.
    state["activeCapabilities"] = cap_list[-50:]
    try:
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", type=int, required=True, choices=range(1, 7))
    parser.add_argument("--intent", required=True)
    parser.add_argument("--kind", nargs="*", default=None,
                        choices=["agent", "skill", "command", "hook", "mcp_tool", "script"])
    parser.add_argument("--workdir", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-cache", action="store_true",
                        help="Do not append the result to state.json.activeCapabilities[]")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    registry = ensure_registry(workdir)
    if not registry.get("entries"):
        print("registry empty — run scripts/build_capability_registry.py first",
              file=sys.stderr)
        return 1

    result = shortlist(registry, args.phase, args.intent, args.kind)
    if not args.no_cache:
        cache_into_state(workdir, result)

    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    # Human-readable.
    print(f"Phase {args.phase} · intent: {args.intent[:80]}")
    print(f"  registry_total={result['registry_total']} → shortlist={result['shortlist_size']}")
    for i, r in enumerate(result["results"], 1):
        reasons = ",".join(r["reasons"][:3])
        print(f"  {i}. [{r['kind']}/{r['category']}] {r['name']}  (score={r['score']}, {reasons})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
