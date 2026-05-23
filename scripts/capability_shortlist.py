#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]
SHORTLIST_CAP = 8

# Tier preference for plugin-surface collapse: higher-tier surface wins.
# Skill is canonical, agent is structured-flow, command is user-invokable,
# script is the lowest-level executable, mcp_tool/hook in between.
SURFACE_TIER_RANK = {
    "skill": 5,
    "agent": 4,
    "command": 3,
    "mcp_tool": 2,
    "hook": 2,
    "script": 1,
}

# Penalty applied when a trigger-demoted entry is detected. Empirical: 5pts
# is enough to drop an off-topic entry (intent score 5-7) below relevant
# entries (intent score 10+) without removing it entirely from the registry.
TRIGGER_DEMOTION_PENALTY = 5

# Names containing these tokens get demoted when the matching trigger is off.
_UI_VALIDATION_TOKENS = ("ibr", "frontend-design", "calm-precision", "ui-guidance")
_PROMPT_TOKENS = ("prompt-builder", "prompt_builder")
_MIGRATION_TOKENS = ("replit-migrate", "replit_migrate")

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


def _plugin_namespace(entry: Dict[str, Any]) -> str:
    """Derive a plugin/family key from an entry's source_path or name.

    Multiple surfaces from the same plugin family (e.g. `commands/debug.md`,
    `commands/debugger.md`, `skills/debugging/*`, `agents/*-debugger.md`) all
    collapse to the same namespace. Used by `apply_plugin_surface_collapse`.

    Heuristic by precedence:
      1. `skills/<family>/...` → `family` (after stripping leading namespace
         like `build-loop:` if present).
      2. `commands/<base>.md` → strip surface prefixes like `debugger-` →
         family root.
      3. `agents/<base>.md` → strip suffixes like `-agent`, `-scout`,
         `-builder` → family root.
      4. `.mcp.json`-derived tool names → `mcp:<server>` part.
      5. Fallback: leading word of entry name.
    """
    src = entry.get("source_path") or ""
    name = (entry.get("name") or "").lower()
    if src.startswith("skills/"):
        # skills/debugging/memory/SKILL.md → 'debugging'
        parts = src.split("/", 2)
        if len(parts) >= 2:
            return parts[1].replace("-", "_")
    if src.startswith("commands/"):
        base = Path(src).stem.lower()
        # 'debugger-detail' / 'debugger-scan' / 'debugger-status' → 'debug'
        for prefix in ("debugger", "debug"):
            if base.startswith(prefix):
                return "debug"
        return base.split("-")[0]
    if src.startswith("agents/"):
        base = Path(src).stem.lower()
        for suffix in ("-agent", "-scout", "-builder", "-critic", "-checker", "-scanner"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        return base.replace("-", "_")
    if "debugger" in name or "debug" in name.split(":")[-1]:
        # mcp:build-loop-debugger and similar
        return "debug"
    return name.split(":")[0].split("-")[0]


def apply_plugin_surface_collapse(
    scored: List[tuple],
) -> List[tuple]:
    """Collapse plugin-surface redundancy.

    When ≥3 entries share the same `(category, plugin_namespace)`, keep at
    most 2 representatives — preferring higher-tier surface
    (skill > agent > command > mcp_tool/hook > script). The kept survivors
    keep their original score; collapsed entries are dropped. Order within
    the input is preserved for kept entries.
    """
    # Group by (category, namespace).
    groups: Dict[tuple, List[int]] = {}
    for idx, (_score, _reasons, e) in enumerate(scored):
        key = (e.get("category", ""), _plugin_namespace(e))
        groups.setdefault(key, []).append(idx)

    drop_indices: set = set()
    for key, idxs in groups.items():
        if len(idxs) < 3:
            continue
        # Sort indices by (kind tier desc, score desc, name asc) and keep top-2.
        def _rank(i: int):
            score, _r, e = scored[i]
            return (
                -SURFACE_TIER_RANK.get(e.get("kind", ""), 0),
                -score,
                e.get("name", ""),
            )

        ordered = sorted(idxs, key=_rank)
        for victim_idx in ordered[2:]:
            drop_indices.add(victim_idx)
    return [t for i, t in enumerate(scored) if i not in drop_indices]


def _infer_triggers_from_repo(workdir: Path) -> Dict[str, Any]:
    """Best-effort heuristics when state.json doesn't have trigger fields.

    Looks at on-disk file extensions and config files.
    Returns dict with `uiTarget`, `migrationSource`, `promptAuthoring`.
    """
    inferred: Dict[str, Any] = {
        "uiTarget": None,
        "migrationSource": None,
        "promptAuthoring": False,
        "promptEditingExisting": False,
    }
    try:
        # uiTarget: any UI extensions present
        for pattern in ("*.tsx", "*.swift", "*.kt", "*.dart"):
            if any(workdir.rglob(pattern)):
                inferred["uiTarget"] = "auto"
                break
    except OSError:
        pass
    try:
        if (workdir / ".replit").exists() or (workdir / "replit.nix").exists():
            inferred["migrationSource"] = "replit"
    except OSError:
        pass
    return inferred


def _read_state_triggers(workdir: Path) -> Dict[str, Any]:
    """Read .build-loop/state.json triggers + sub-routers; fall back to heuristic."""
    state_path = workdir / ".build-loop" / "state.json"
    state: Dict[str, Any] = {}
    try:
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}

    triggers = state.get("triggers", {}) or {}
    out = {
        "uiTarget": state.get("uiTarget"),
        "platform": state.get("platform"),
        "migrationSource": state.get("migrationSource"),
        "promptAuthoring": triggers.get("promptAuthoring", False),
        "promptEditingExisting": triggers.get("promptEditingExisting", False),
    }
    # If state.json absent or fields missing entirely, fall back to repo heuristics.
    if not state_path.is_file() or (
        out["uiTarget"] is None and out["migrationSource"] is None
        and not out["promptAuthoring"] and not out["promptEditingExisting"]
    ):
        inferred = _infer_triggers_from_repo(workdir)
        for k, v in inferred.items():
            if out.get(k) in (None, False):
                out[k] = v
    return out


def apply_trigger_demotion(
    scored: List[tuple],
    triggers: Dict[str, Any],
) -> List[tuple]:
    """Demote off-topic entries based on phase-1 sub-routers and triggers.

    Penalty subtracted (TRIGGER_DEMOTION_PENALTY); reason appended. Order is
    preserved; the caller re-sorts by score afterwards.

    Rules:
    - `uiTarget` is None → demote any entry with `category=ui-validation` or
      whose name contains an IBR/UI-validation token.
    - Both `promptAuthoring` and `promptEditingExisting` False → demote
      `prompt-builder*` entries.
    - `migrationSource` is None → demote `replit-migrate*` entries.
    """
    ui_off = triggers.get("uiTarget") is None
    prompt_off = (
        not triggers.get("promptAuthoring") and not triggers.get("promptEditingExisting")
    )
    migration_off = triggers.get("migrationSource") is None

    out: List[tuple] = []
    for score, reasons, e in scored:
        cat = (e.get("category") or "").lower()
        name_l = (e.get("name") or "").lower()
        penalty = 0
        notes: List[str] = []
        if ui_off and (cat == "ui-validation"
                       or any(tok in name_l for tok in _UI_VALIDATION_TOKENS)):
            penalty += TRIGGER_DEMOTION_PENALTY
            notes.append("demote:no-ui-target")
        if prompt_off and any(tok in name_l for tok in _PROMPT_TOKENS):
            penalty += TRIGGER_DEMOTION_PENALTY
            notes.append("demote:no-prompt-work")
        if migration_off and any(tok in name_l for tok in _MIGRATION_TOKENS):
            penalty += TRIGGER_DEMOTION_PENALTY
            notes.append("demote:no-migration")
        if penalty:
            out.append((score - penalty, list(reasons) + notes, e))
        else:
            out.append((score, reasons, e))
    return out


def shortlist(
    registry: Dict[str, Any],
    phase: int,
    intent: str,
    kinds: Optional[Sequence[str]] = None,
    workdir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Score and shortlist registry entries against (phase, intent).

    `workdir` defaults to the repo root; passed through to trigger reads
    so synthetic test fixtures can override.
    """
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

    # P13 refinements (run #4): collapse plugin-surface redundancy so a
    # single plugin can't dominate the shortlist; demote off-topic
    # categories based on Phase 1 sub-routers (uiTarget, migrationSource)
    # and triggers (promptAuthoring/promptEditingExisting).
    scored = apply_plugin_surface_collapse(scored)
    if workdir is not None:
        triggers = _read_state_triggers(Path(workdir))
        scored = apply_trigger_demotion(scored, triggers)

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
    """Cache a compact summary under `state.json.activeCapabilities`.

    Shape (P16): phase-keyed dict
        {
          "1": [{phase, intent, shortlist, results, generated_at}, ...],
          "2": [...],
          "3": [...]
        }

    Each phase entry is a list capped at the most recent 10 invocations for
    that phase so subagent dispatchers can pick the freshest matching intent.
    The full `results` array (with kind/category/score/source_path/description)
    is preserved so consumers can embed shortlist context directly into a
    subagent brief without re-running the matcher.

    Backward-compat read path: when a caller reads the field, it may
    encounter the legacy flat list shape from before P16 (`[{phase, ...}, ...]`).
    Use `read_active_capabilities()` to abstract over both.

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

    phase_key = str(result["phase"])
    entry = {
        "phase": result["phase"],
        "intent": result["intent"][:240],
        "shortlist": [r["name"] for r in result["results"]],
        "results": result["results"],
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    cap = state.get("activeCapabilities")
    if isinstance(cap, list):
        # Migrating from legacy flat-list shape: regroup into phase-keyed dict.
        new_cap: Dict[str, List[Dict[str, Any]]] = {}
        for old_entry in cap:
            if not isinstance(old_entry, dict):
                continue
            old_phase = str(old_entry.get("phase", "?"))
            new_cap.setdefault(old_phase, []).append(old_entry)
        cap = new_cap
    elif not isinstance(cap, dict):
        cap = {}

    bucket = list(cap.get(phase_key) or [])
    bucket.append(entry)
    # Keep the last 10 invocations per phase — fresher entries win on lookup.
    cap[phase_key] = bucket[-10:]
    state["activeCapabilities"] = cap
    try:
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def read_active_capabilities(
    state: Dict[str, Any],
    phase: int,
    fallback_phase: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return the most-recent shortlist results[] for `phase`.

    Tolerates both the new phase-keyed dict shape and the legacy flat-list
    shape so older state.json files keep working without an explicit
    migration step. Returns `[]` when no cached shortlist matches.

    `fallback_phase`: when no entry exists for `phase`, fall back to this
    phase before giving up. Used by Phase 3 to read Phase 2's shortlist
    when Phase 3 isn't separately scored.
    """
    cap = state.get("activeCapabilities")
    if not cap:
        return []
    if isinstance(cap, dict):
        bucket = cap.get(str(phase)) or []
        if not bucket and fallback_phase is not None:
            bucket = cap.get(str(fallback_phase)) or []
        if not bucket:
            return []
        latest = bucket[-1]
        return list(latest.get("results") or [])
    if isinstance(cap, list):
        # Legacy flat-list shape — pick the most recent matching phase entry.
        for entry in reversed(cap):
            if not isinstance(entry, dict):
                continue
            if entry.get("phase") == phase:
                return list(entry.get("results") or [])
        if fallback_phase is not None:
            for entry in reversed(cap):
                if not isinstance(entry, dict):
                    continue
                if entry.get("phase") == fallback_phase:
                    return list(entry.get("results") or [])
        return []
    return []


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
    parser.add_argument("--cache-into-state", action="store_true",
                        help="Explicitly cache the result to state.json.activeCapabilities[<phase>]. "
                             "Caching already happens by default; this flag exists so callers "
                             "(notably the orchestrator's mandatory Phase 1 invocation) can be "
                             "explicit about intent. Mutually exclusive with --no-cache.")
    args = parser.parse_args(argv)

    if args.no_cache and args.cache_into_state:
        print("error: --no-cache and --cache-into-state are mutually exclusive",
              file=sys.stderr)
        return 2

    workdir = Path(args.workdir).resolve()
    registry = ensure_registry(workdir)
    if not registry.get("entries"):
        print("registry empty — run scripts/build_capability_registry.py first",
              file=sys.stderr)
        return 1

    result = shortlist(registry, args.phase, args.intent, args.kind, workdir=workdir)
    # Default behavior is cache-on. --cache-into-state is an explicit reaffirmation
    # that callers (e.g. the orchestrator's mandatory Phase 1 step) use to express
    # intent and exercise the same atomic write path (cache_into_state) used by
    # subagents reading via read_active_capabilities().
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
