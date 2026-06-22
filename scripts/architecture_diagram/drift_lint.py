#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Drift linter for the living architecture manifest.

Cross-checks architecture/flow.yaml against the real agents/ and hooks/ so the
diagram can never reference something that no longer exists, and warns when a
real agent is missing from the diagram. Mirrors the scripts/sync_skills.py
drift-detector contract: read-only, structured findings, non-zero exit on ERROR.

ERROR (exit 1):
  - flow.yaml references an agent (chip) that is not an agents/*.md name,
    not a declared alias/group, and not a PROPOSED-new agent.
  - flow.yaml references a hook basename that is not in hooks/hooks.json,
    not a synthetic "(post)/(stop) ..." label, and not a PROPOSED-new hook.

WARN (exit 0):
  - an agents/*.md agent is not represented anywhere in the diagram and is not
    on the coverage_exempt list.

Usage: python3 scripts/architecture_diagram/drift_lint.py [--repo PATH] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.M)
HOOK_SCRIPT_RE = re.compile(r"/hooks/([\w.-]+\.(?:sh|py))")
SYNTHETIC_HOOK_RE = re.compile(r"^\((post|stop)\)\s")


def agent_names(repo: Path) -> set[str]:
    # regex-extract name (frontmatter may carry inline colons that break yaml.safe_load)
    out = set()
    for md in (repo / "agents").glob("*.md"):
        m = FRONTMATTER_RE.match(md.read_text(encoding="utf-8"))
        if m:
            nm = NAME_RE.search(m.group(1))
            if nm:
                out.add(nm.group(1).strip().strip('"').strip("'").split(":")[-1])
    return out


def hook_basenames(repo: Path) -> set[str]:
    out = set()
    data = json.loads((repo / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    for blocks in data.get("hooks", {}).values():
        for blk in blocks:
            for h in blk.get("hooks", []):
                mm = HOOK_SCRIPT_RE.search(h.get("command", ""))
                if mm:
                    out.add(mm.group(1))
    return out


def lint(repo: Path) -> dict:
    flow = yaml.safe_load((repo / "architecture" / "flow.yaml").read_text(encoding="utf-8"))
    real_agents = agent_names(repo)
    real_hooks = hook_basenames(repo)
    aliases = flow.get("agent_aliases", {})
    groups = flow.get("agent_groups", {})
    exempt = set(flow.get("coverage_exempt", []))
    proposed = set(flow.get("proposed", []))
    # PROPOSED-new agents/hooks: anything the manifest declares as proposed by id; plus the
    # auditor-launch hook which is the named new hook.
    proposed_new_hooks = {name for name, ov in (flow.get("hook_overrides") or {}).items()
                          if isinstance(ov, dict) and ov.get("proposed")}

    errors: list[str] = []
    warnings: list[str] = []
    referenced_agents: set[str] = set()
    referenced_hooks: set[str] = set()

    def canon(name: str) -> str:
        return aliases.get(name, name)

    def check_agents(refs, where):
        for ref in refs or []:
            name = ref[0] if isinstance(ref, list) else ref
            c = canon(name)
            referenced_agents.add(name)
            if c == "group":
                if name not in groups:
                    errors.append(f"agent group '{name}' ({where}) has no agent_groups expansion")
                continue
            if c not in real_agents:
                errors.append(f"agent '{name}' ({where}) is not an agents/*.md name and not a known alias/group")

    for p in flow.get("phases", []):
        check_agents(p.get("agents"), f"phase {p['id']}")
        for s in p.get("steps", []):
            check_agents(s.get("agents"), f"step {s['id']}")
            for hk in s.get("hooks", []):
                referenced_hooks.add(hk)
                if hk in real_hooks or SYNTHETIC_HOOK_RE.match(hk) or hk in proposed_new_hooks:
                    continue
                errors.append(f"hook '{hk}' (step {s['id']}) is not in hooks/hooks.json and not synthetic/proposed")

    # subagent registry keys must resolve too (they back the click-for-goal panel)
    for name in (flow.get("subagents") or {}):
        c = canon(name)
        if c != "group" and c not in real_agents:
            errors.append(f"subagents registry entry '{name}' is not an agents/*.md name/alias")

    # coverage: real agents missing from the diagram
    covered = set()
    for name in referenced_agents:
        c = canon(name)
        if c == "group":
            covered.update(groups.get(name, []))
        else:
            covered.add(c)
    covered.update(flow.get("subagents", {}).keys())
    for a in sorted(real_agents):
        if a not in covered and a not in exempt:
            warnings.append(f"agent '{a}' exists in agents/ but is not represented in the diagram (add it, or add to coverage_exempt)")

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "agents_total": len(real_agents), "agents_referenced": len(covered & real_agents)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = lint(Path(args.repo).resolve())
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        for e in res["errors"]:
            print(f"ERROR  {e}")
        for w in res["warnings"]:
            print(f"WARN   {w}")
        print(("DRIFT OK ✅ " if res["ok"] else "DRIFT FAIL ❌ ")
              + f"({res['agents_referenced']}/{res['agents_total']} agents represented, "
              + f"{len(res['warnings'])} warn)")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
