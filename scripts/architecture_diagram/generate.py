#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Generate the living architecture model for the build-loop flow diagram.

Merges the AUTO-DERIVED layer (agent model tiers from agents/*.md frontmatter,
hook events from hooks/hooks.json) with the AUTHORED layer (architecture/flow.yaml)
into architecture/model.json, then injects that model into the standalone HTML
renderer between the BL_MODEL markers so the diagram regenerates from source and
cannot drift.

Usage:
    python3 scripts/architecture_diagram/generate.py [--repo PATH] [--check] [--json]

--check : do not write; exit 1 if model.json or the HTML injection is stale.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml  # PyYAML 6.x (verified available in the build-loop dev env)

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
GEN_VERSION = "1.0.0"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.M)
MODEL_RE = re.compile(r"^model:\s*(.+?)\s*$", re.M)
HOOK_SCRIPT_RE = re.compile(r"/hooks/([\w.-]+\.(?:sh|py))")


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO))


def _strip(v: str) -> str:
    return v.strip().strip('"').strip("'")


def parse_agents(repo: Path) -> dict[str, str]:
    """agents/*.md frontmatter -> {agent name: model tier}.

    Regex-extract name/model rather than yaml.safe_load the whole frontmatter:
    several agents use an unquoted inline `description:` containing colons, which
    is not valid YAML mapping content and would drop the agent silently.
    """
    out: dict[str, str] = {}
    for md in sorted((repo / "agents").glob("*.md")):
        m = FRONTMATTER_RE.match(md.read_text(encoding="utf-8"))
        if not m:
            continue
        fm = m.group(1)
        nm = NAME_RE.search(fm)
        if not nm:
            continue
        mo = MODEL_RE.search(fm)
        name = _strip(nm.group(1)).split(":")[-1]  # strip any plugin namespace prefix
        out[name] = _strip(mo.group(1)) if mo else ""
    return out


def parse_hooks(repo: Path) -> tuple[dict[str, list[str]], dict[str, str]]:
    """hooks/hooks.json -> ({event: [script basenames]}, {basename: event})."""
    by_event: dict[str, list[str]] = {}
    basename_event: dict[str, str] = {}
    data = json.loads((repo / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    for event, blocks in data.get("hooks", {}).items():
        names: list[str] = []
        for blk in blocks:
            for h in blk.get("hooks", []):
                cmd = h.get("command", "")
                mm = HOOK_SCRIPT_RE.search(cmd)
                if mm:
                    nm = mm.group(1)
                    names.append(nm)
                    basename_event.setdefault(nm, event)
        if names:
            by_event[event] = names
    return by_event, basename_event


def _static_provenance() -> dict:
    # NO git sha / dirty flag here on purpose: those move on every commit and would make
    # model.json perpetually "stale", failing the drift gate after each commit. Provenance
    # is content-derived (content_sha256, set in build_model) so it changes ONLY when the
    # derived architecture changes.
    return {
        "generator": f"scripts/architecture_diagram/generate.py@{GEN_VERSION}",
        "sources": ["architecture/flow.yaml", "agents/*.md (frontmatter)",
                    "hooks/hooks.json", "scripts/model_overrides.py (tier names)"],
    }


def _canonical(name: str, aliases: dict[str, str]) -> str:
    return aliases.get(name, name)


def _fill_tiers(agents: list, registry: dict[str, str], aliases: dict[str, str]) -> list:
    """Fill the empty tier slot of each [name, tier, by] ref from the agent registry."""
    out = []
    for ref in agents or []:
        name, tier, by = (ref + ["", "", ""])[:3]
        if not tier:
            canon = _canonical(name, aliases)
            tier = registry.get(canon, "")  # "group"/unknown -> "" (no tier chip)
        out.append([name, tier, by])
    return out


def build_model(repo: Path) -> dict:
    flow = yaml.safe_load((repo / "architecture" / "flow.yaml").read_text(encoding="utf-8"))
    agents = parse_agents(repo)
    hooks_by_event, hook_event = parse_hooks(repo)
    aliases = flow.get("agent_aliases", {})

    # phases: pass through authored structure, auto-fill agent tiers from frontmatter
    phases = []
    for p in flow["phases"]:
        p = dict(p)
        p["agents"] = _fill_tiers(p.get("agents", []), agents, aliases)
        steps = []
        for s in p.get("steps", []):
            s = dict(s)
            s["agents"] = _fill_tiers(s.get("agents", []), agents, aliases)
            steps.append(s)
        p["steps"] = steps
        phases.append(p)

    # subagents registry (SUB shape): {name: [goal, does]}
    subagents = {k: [v.get("goal", ""), v.get("does", "")]
                 for k, v in (flow.get("subagents") or {}).items()}

    # hook_desc (HOOK_DESC shape): {name: [event, purpose]}
    hook_desc: dict[str, list[str]] = {}
    overrides = flow.get("hook_overrides") or {}
    for name, ov in overrides.items():
        event = ov.get("event") or hook_event.get(name, "Hook")
        hook_desc[name] = [event, ov.get("purpose", "(purpose inferred)")]
    # any hook referenced in steps but missing an override -> auto event, inferred purpose
    for p in phases:
        for s in p.get("steps", []):
            for hk in s.get("hooks", []):
                if hk not in hook_desc:
                    hook_desc[hk] = [hook_event.get(hk, "Hook"), "(purpose inferred)"]

    proposed = {pid: 1 for pid in flow.get("proposed", [])}

    body = {
        "pipe_in": flow["pipeline"]["in"],
        "pipe_out": flow["pipeline"]["out"],
        "proposed": proposed,
        "gate_after": flow.get("gate_after", {}),
        "roles": flow.get("roles", {}),
        "phases": phases,
        "subagents": subagents,
        "hook_desc": hook_desc,
        "registries": {
            "agents": agents,                      # auto-derived: name -> model tier
            "hooks_by_event": hooks_by_event,      # auto-derived: event -> [scripts]
        },
    }
    prov = _static_provenance()
    prov["content_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"_provenance": prov, **body}


def inject_html(html: str, model: dict) -> str:
    block = ('<!-- BL_MODEL_START - generated by scripts/architecture_diagram/generate.py; do not edit by hand -->\n'
             '<script id="bl-model">window.BL_MODEL = '
             + json.dumps(model, ensure_ascii=False, indent=2)
             + ';</script>\n'
             '<!-- BL_MODEL_END -->')
    pat = re.compile(r"<!-- BL_MODEL_START.*?BL_MODEL_END -->", re.DOTALL)
    if pat.search(html):
        return pat.sub(lambda _: block, html)
    # first run: insert right before the main renderer <script> (the IIFE)
    return html.replace("<script>\n(function(){", block + "\n<script>\n(function(){", 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--check", action="store_true", help="verify freshness; do not write")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()

    model = build_model(repo)
    model_path = repo / "architecture" / "model.json"
    html_path = repo / "docs" / "build-loop-flow-mockup.html"
    new_json = json.dumps(model, ensure_ascii=False, indent=2) + "\n"
    html = html_path.read_text(encoding="utf-8")
    new_html = inject_html(html, model)

    stale_json = (not model_path.exists()) or model_path.read_text(encoding="utf-8") != new_json
    stale_html = html != new_html
    result = {"ok": True, "stale_model_json": stale_json, "stale_html": stale_html,
              "agents": len(model["registries"]["agents"]),
              "phases": len(model["phases"]),
              "content_sha": model["_provenance"]["content_sha256"][:8]}

    if args.check:
        result["ok"] = not (stale_json or stale_html)
        print(json.dumps(result) if args.json else
              ("FRESH" if result["ok"] else "STALE — run generate.py to refresh"))
        return 0 if result["ok"] else 1

    model_path.write_text(new_json, encoding="utf-8")
    html_path.write_text(new_html, encoding="utf-8")
    print(json.dumps(result) if args.json else
          f"wrote architecture/model.json ({result['agents']} agents, {result['phases']} phases) "
          f"+ injected into {_rel(html_path)} @ sha {result['content_sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
