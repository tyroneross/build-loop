#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""infer_risk_surface.py — deterministic riskSurfaceChange auto-infer.

Phase 1 Assess helper. Returns `risk_surface_change: true` whenever the goal,
plan, or planned filesTouched overlap any rule in the build-loop constitution
(`~/.build-loop/memory/constitution.md`).

Closes the gap surfaced by plan §11.4 Sim G (assess plan in
~/.claude/plans/assess-build-loop-how-logical-quilt.md): auth-touching diffs
shipped without security-reviewer firing because the manual `riskSurfaceChange`
flag was missed.

Inputs (auto-discovered from --workdir):
- `state.json.goal` and `state.json.constitution.loadedRuleIds[]`
- `.build-loop/plan/plan.md` (if present, scanned for keywords)
- `state.json.filesTouchedPlanned[]` (if present) — planned scope file list

Output: single JSON object on stdout. Stdlib only.
Exit codes: 0 always (informational; never blocks).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KEYWORDS_BY_RULE = {
    "C-SEC/no_secrets_in_commits": [
        r"\bsecret\b", r"\bapi[_-]?key\b", r"\btoken\b", r"\bpassword\b",
        r"\bprivate[_-]?key\b", r"\bcredential\b", r"\b\.env\b",
    ],
    "C-SEC/no_log_credentials": [r"\blog.*(secret|token|password|credential)\b"],
    "C-AUTH/no_disable_auth_without_flag": [
        r"\bauth\b", r"\bauthz\b", r"\bauthentication\b", r"\bauthorization\b",
        r"\blogin\b", r"\bsession\b", r"\bjwt\b", r"\boauth\b", r"\bsaml\b",
        r"\bsigned[_-]?request\b", r"\brole[_-]?check\b",
    ],
    "C-AUTH/auth_change_requires_test": [r"\bauth\b", r"\blogin\b", r"\bsession\b"],
    "C-DATA/no_drop_without_rollback": [
        r"\bdrop\s+(tables?|columns?|indexes?|indices)\b", r"\bmigrations?\b", r"\balter\s+tables?\b",
        r"\btruncate\b", r"\brollback\b",
    ],
    "C-DATA/no_destructive_ops_in_loop": [
        r"\brm\s+-rf\b", r"\bforce[-_]?push\b", r"--force\b",
        r"\bdrop\s+databases?\b", r"\btruncate\s+tables?\b",
    ],
    "C-CLAIMS/no_fake_data_as_real": [r"\bmock\b", r"\bfaker\b", r"\bplaceholder\b", r"\blorem\b"],
    "C-AGENT/no_silent_self_modification": [
        r"~/.build-loop/", r"~/.claude/", r"\.claude-plugin/",
        r"\.build-loop/skills/active", r"\.build-loop/agents/active",
        r"\bself[-_]?modif", r"\bauto[-_]?promote\b",
    ],
    "C-AGENT/no_bypass_pre_commit_hooks": [r"--no-verify\b", r"--no-gpg-sign\b"],
    "C-AGENT/no_destructive_git_without_confirm": [
        r"\bgit\s+reset\s+--hard\b", r"\bgit\s+push\s+--force\b",
        r"\bgit\s+clean\s+-f\b", r"\bgit\s+branch\s+-D\b",
    ],
    "C-MEMORY/no_write_memory_directly": [
        r"~/.build-loop/memory/", r"memory_writer\.py", r"\.build-loop/memory/",
    ],
}

# Generic risk-surface keywords that flip the flag regardless of constitution
# membership. These mirror the existing trigger-rules.md enumeration so the
# detector subsumes it.
GENERIC_RISK_KEYWORDS = [
    (r"\bmcp\s+server\b|\bnew\s+mcp\b", "new MCP server"),
    (r"\bllm\s+call\b|\bshipped\s+prompt\b", "new LLM call or shipped prompt"),
    (r"\bvector\s+store\b|\bpersistent\s+agent\s+memory\b", "persistent agent memory or vector store"),
    (r"\bpii\b|\bphi\b|\bfinancial\b|\bhealth\s+data\b|\bregulated\b", "regulated user-data class"),
    (r"\bexternal\s+api\b", "external API call"),
]


def gather_text(workdir: Path) -> tuple[str, list[str]]:
    """Return (combined_text, files_planned) drawn from state.json + plan files."""
    parts: list[str] = []
    files_planned: list[str] = []

    state_path = workdir / ".build-loop" / "state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
        if isinstance(state, dict):
            goal = state.get("goal")
            if isinstance(goal, str):
                parts.append(goal)
            ftp = state.get("filesTouchedPlanned")
            if isinstance(ftp, list):
                files_planned = [f for f in ftp if isinstance(f, str)]

    plan_path = workdir / ".build-loop" / "plan" / "plan.md"
    if plan_path.exists():
        parts.append(plan_path.read_text(encoding="utf-8", errors="replace"))
    legacy_plan = workdir / ".build-loop" / "goal.md"
    if legacy_plan.exists():
        parts.append(legacy_plan.read_text(encoding="utf-8", errors="replace"))

    return ("\n".join(parts), files_planned)


def evaluate(text: str, files_planned: list[str], loaded_rule_ids: set[str]) -> dict:
    """Match keywords in text + filepaths. Only count rules whose IDs are loaded."""
    matched: dict[str, list[str]] = {}
    haystack = text + "\n" + "\n".join(files_planned)
    haystack_lower = haystack.lower()

    for rule_id, patterns in KEYWORDS_BY_RULE.items():
        if loaded_rule_ids and rule_id not in loaded_rule_ids:
            continue
        for pat in patterns:
            for m in re.finditer(pat, haystack_lower, re.IGNORECASE):
                matched.setdefault(rule_id, []).append(m.group(0))

    generic_hits: list[dict] = []
    for pat, label in GENERIC_RISK_KEYWORDS:
        if re.search(pat, haystack_lower, re.IGNORECASE):
            generic_hits.append({"label": label, "evidence": pat})

    return {
        "risk_surface_change": bool(matched) or bool(generic_hits),
        "matched_rules": sorted(matched.keys()),
        "constitution_evidence": {rid: list(set(hits))[:3] for rid, hits in matched.items()},
        "generic_evidence": generic_hits,
        "loaded_constitution_rules_count": len(loaded_rule_ids),
    }


def loaded_rule_ids(workdir: Path) -> set[str]:
    state_path = workdir / ".build-loop" / "state.json"
    if not state_path.exists():
        return set()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    if not isinstance(state, dict):
        return set()
    constitution = state.get("constitution")
    if not isinstance(constitution, dict):
        return set()
    ids = constitution.get("loadedRuleIds")
    if not isinstance(ids, list):
        return set()
    return {x for x in ids if isinstance(x, str)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Deterministic riskSurfaceChange auto-infer.")
    p.add_argument("--workdir", required=True, help="Project root containing .build-loop/")
    p.add_argument("--json", action="store_true", help="Emit JSON (default human text)")
    args = p.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    text, files_planned = gather_text(workdir)
    rule_ids = loaded_rule_ids(workdir)

    result = evaluate(text, files_planned, rule_ids)

    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"risk_surface_change: {result['risk_surface_change']}")
        if result["matched_rules"]:
            print(f"matched constitution rules: {', '.join(result['matched_rules'])}")
        if result["generic_evidence"]:
            print(f"generic risk hits: {', '.join(h['label'] for h in result['generic_evidence'])}")
        if not result["risk_surface_change"]:
            print("(no risk-surface signals; security-reviewer will not auto-fire)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
