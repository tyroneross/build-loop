#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Generate the living architecture model for the build-loop flow diagram.

Source of truth: architecture/ARCHITECTURE.md
  - Components (agents/skills/scripts/hooks) are AUTO-discovered from the repo.
  - The Flow (phases/sub-steps/gates/edges/current-vs-proposed) is AUTHORED in the
    fenced ```yaml block under the `<!-- arch:flow -->` marker.

Outputs:
  - architecture/model.json            (git-tracked; its git log is the changelog)
  - the BL_MODEL block injected into docs/build-loop-flow-mockup.html
  - the Components section injected back into architecture/ARCHITECTURE.md

Usage: python3 scripts/architecture_diagram/generate.py [--repo PATH] [--check] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
GEN_VERSION = "2.0.0"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.M)
MODEL_RE = re.compile(r"^model:\s*(.+?)\s*$", re.M)
DESC_RE = re.compile(r"^description:\s*(.+?)\s*$", re.M)
HOOK_SCRIPT_RE = re.compile(r"/hooks/([\w.-]+\.(?:sh|py))")
FLOW_BLOCK_RE = re.compile(r"<!-- arch:flow -->\s*```ya?ml\s*\n(.*?)\n```", re.DOTALL)
COMPONENTS_RE = re.compile(r"<!-- ARCH_COMPONENTS_START -->.*?<!-- ARCH_COMPONENTS_END -->", re.DOTALL)
DOCSTRING_RE = re.compile(r'^\s*(?:[ru]?["\']{3})(.*?)$', re.M)


def _strip(v: str) -> str:
    return v.strip().strip('"').strip("'")


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO))


def _short(text: str, n: int = 110) -> str:
    text = " ".join(text.split())
    return text[: n - 1] + "…" if len(text) > n else text


def git_last_updated(repo: Path, relpath: str) -> dict:
    """Last commit that touched a file -> {author, date}. Stable unless the file changes
    (component files are never modified by the generator), so it doesn't churn model.json."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "log", "-1", "--format=%an|%aI", "--", relpath],
            text=True, stderr=subprocess.DEVNULL).strip()
        if out and "|" in out:
            a, d = out.split("|", 1)
            return {"author": a, "date": d[:10]}
    except Exception:
        pass
    return {"author": "", "date": ""}


def _extract_desc(fm: str) -> str:
    """Pull the human description out of agent/skill frontmatter (drops <example> blocks)."""
    dm = re.search(r"^description:\s*\|?\s*(.+?)(?=\n[a-z_]+:\s|\Z)", fm, re.DOTALL | re.M)
    raw = dm.group(1) if dm else ""
    raw = raw.split("<example>")[0].split("<commentary>")[0]
    return _short(raw, 420)


# ---------------- auto-discovered inventories ----------------

def parse_agents(repo: Path) -> dict[str, dict]:
    """agents/*.md -> {name: {model, description, file, last_updated{author,date}}}.

    Rich enough that the diagram is a source of truth for what each agent does.
    """
    out: dict[str, dict] = {}
    for md in sorted((repo / "agents").glob("*.md")):
        m = FRONTMATTER_RE.match(md.read_text(encoding="utf-8"))
        if not m:
            continue
        nm = NAME_RE.search(m.group(1))
        if not nm:
            continue
        name = _strip(nm.group(1)).split(":")[-1]
        mo = MODEL_RE.search(m.group(1))
        rel = str(md.relative_to(repo))
        out[name] = {
            "model": _strip(mo.group(1)) if mo else "",
            "description": _extract_desc(m.group(1)),
            "file": rel,
            "last_updated": git_last_updated(repo, rel),
        }
    return out


def parse_skills(repo: Path) -> dict[str, dict]:
    """skills/**/SKILL.md -> {name: {description, file, last_updated{author,date}}}."""
    out: dict[str, dict] = {}
    sk = repo / "skills"
    if not sk.exists():
        return out
    for md in sorted(sk.glob("**/SKILL.md")):
        m = FRONTMATTER_RE.match(md.read_text(encoding="utf-8"))
        name, desc = None, ""
        if m:
            nm = NAME_RE.search(m.group(1))
            if nm:
                name = _strip(nm.group(1)).split(":")[-1]
            desc = _extract_desc(m.group(1))
        name = name or md.parent.name
        rel = str(md.relative_to(repo))
        out[name] = {"description": desc, "file": rel, "last_updated": git_last_updated(repo, rel)}
    return out


def parse_scripts(repo: Path) -> dict[str, str]:
    """scripts/**/*.py (excluding tests/__pycache__) -> {relative path: first docstring line}."""
    out: dict[str, str] = {}
    for py in sorted((repo / "scripts").glob("**/*.py")):
        rel = py.relative_to(repo)
        if "__pycache__" in rel.parts or py.name.startswith("test_"):
            continue
        first = ""
        try:
            text = py.read_text(encoding="utf-8")
            dm = DOCSTRING_RE.search(text)
            if dm:
                first = _short(dm.group(1))
        except Exception:
            pass
        out[str(rel)] = first
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
                mm = HOOK_SCRIPT_RE.search(h.get("command", ""))
                if mm:
                    names.append(mm.group(1))
                    basename_event.setdefault(mm.group(1), event)
        if names:
            by_event[event] = names
    return by_event, basename_event


# ---------------- authored flow ----------------

def load_flow(repo: Path) -> dict:
    """Extract + parse the authored flow yaml block from architecture/ARCHITECTURE.md."""
    doc = (repo / "architecture" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    m = FLOW_BLOCK_RE.search(doc)
    if not m:
        raise SystemExit("architecture/ARCHITECTURE.md: no `<!-- arch:flow -->` yaml block found")
    return yaml.safe_load(m.group(1))


def _static_provenance() -> dict:
    # content-derived only (no git sha) so model.json doesn't churn per-commit.
    return {
        "generator": f"scripts/architecture_diagram/generate.py@{GEN_VERSION}",
        "source": "architecture/ARCHITECTURE.md",
        "auto_sources": ["agents/*.md", "skills/**/SKILL.md", "scripts/**/*.py", "hooks/hooks.json"],
    }


def _canonical(name: str, aliases: dict[str, str]) -> str:
    return aliases.get(name, name)


def _fill_tiers(agents: list, registry: dict[str, str], aliases: dict[str, str]) -> list:
    out = []
    for ref in agents or []:
        name, tier, by = (ref + ["", "", ""])[:3]
        if not tier:
            tier = registry.get(_canonical(name, aliases), "")
        out.append([name, tier, by])
    return out


def build_model(repo: Path) -> dict:
    flow = load_flow(repo)
    agents = parse_agents(repo)
    skills = parse_skills(repo)
    scripts = parse_scripts(repo)
    hooks_by_event, hook_event = parse_hooks(repo)
    aliases = flow.get("agent_aliases", {})
    tier_map = {n: d.get("model", "") for n, d in agents.items()}  # for chip tier-fill

    phases = []
    for p in flow["phases"]:
        p = dict(p)
        p["agents"] = _fill_tiers(p.get("agents", []), tier_map, aliases)
        p["steps"] = [{**s, "agents": _fill_tiers(s.get("agents", []), tier_map, aliases)}
                      for s in p.get("steps", [])]
        phases.append(p)

    subagents = {k: [v.get("goal", ""), v.get("does", "")]
                 for k, v in (flow.get("subagents") or {}).items()}

    hook_desc: dict[str, list[str]] = {}
    for name, ov in (flow.get("hook_overrides") or {}).items():
        hook_desc[name] = [ov.get("event") or hook_event.get(name, "Hook"),
                           ov.get("purpose", "(purpose inferred)")]
    for p in phases:
        for s in p.get("steps", []):
            for hk in s.get("hooks", []):
                hook_desc.setdefault(hk, [hook_event.get(hk, "Hook"), "(purpose inferred)"])

    body = {
        "pipe_in": flow["pipeline"]["in"],
        "pipe_out": flow["pipeline"]["out"],
        "proposed": {pid: 1 for pid in flow.get("proposed", [])},
        "gate_after": flow.get("gate_after", {}),
        "roles": flow.get("roles", {}),
        "phases": phases,
        "subagents": subagents,
        "hook_desc": hook_desc,
        "registries": {
            "agents": agents,
            "skills": skills,
            "scripts": scripts,
            "hooks_by_event": hooks_by_event,
        },
    }
    prov = _static_provenance()
    prov["content_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"_provenance": prov, **body}


# ---------------- injection ----------------

def inject_html(html: str, model: dict) -> str:
    block = ('<!-- BL_MODEL_START - generated by scripts/architecture_diagram/generate.py; do not edit by hand -->\n'
             '<script id="bl-model">window.BL_MODEL = '
             + json.dumps(model, ensure_ascii=False, indent=2) + ';</script>\n'
             '<!-- BL_MODEL_END -->')
    pat = re.compile(r"<!-- BL_MODEL_START.*?BL_MODEL_END -->", re.DOTALL)
    if pat.search(html):
        return pat.sub(lambda _: block, html)
    return html.replace("<script>\n(function(){", block + "\n<script>\n(function(){", 1)


def components_md(model: dict) -> str:
    reg = model["registries"]
    a, s, sc, h = reg["agents"], reg["skills"], reg["scripts"], reg["hooks_by_event"]
    nh = sum(len(v) for v in h.values())
    lines = [
        "<!-- ARCH_COMPONENTS_START -->",
        "<!-- run: python3 scripts/architecture_diagram/generate.py -->",
        f"**{len(a)} agents · {len(s)} skills · {len(sc)} scripts · {nh} hooks** "
        f"(auto-discovered {model['_provenance']['content_sha256'][:8]})",
        "",
        "<details><summary>agents</summary>",
        "",
        *[f"- `{n}` — {d.get('model') or '—'} · _updated {d['last_updated']['date']} "
          f"by {d['last_updated']['author'] or '—'}_ — {d.get('description', '')}"
          for n, d in sorted(a.items())],
        "</details>",
        "<details><summary>skills</summary>",
        "",
        *[f"- `{n}`" for n in sorted(s)],
        "</details>",
        "<details><summary>scripts</summary>",
        "",
        *[f"- `{n}`" for n in sorted(sc)],
        "</details>",
        "<!-- ARCH_COMPONENTS_END -->",
    ]
    return "\n".join(lines)


def inject_components(doc: str, model: dict) -> str:
    block = components_md(model)
    if COMPONENTS_RE.search(doc):
        return COMPONENTS_RE.sub(lambda _: block, doc)
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()

    model = build_model(repo)
    model_path = repo / "architecture" / "model.json"
    html_path = repo / "docs" / "build-loop-flow-mockup.html"
    arch_path = repo / "architecture" / "ARCHITECTURE.md"

    new_json = json.dumps(model, ensure_ascii=False, indent=2) + "\n"
    new_html = inject_html(html_path.read_text(encoding="utf-8"), model)
    new_arch = inject_components(arch_path.read_text(encoding="utf-8"), model)

    stale = {
        "model_json": (not model_path.exists()) or model_path.read_text(encoding="utf-8") != new_json,
        "html": html_path.read_text(encoding="utf-8") != new_html,
        "architecture_md": arch_path.read_text(encoding="utf-8") != new_arch,
    }
    reg = model["registries"]
    result = {"ok": True, "stale": stale,
              "agents": len(reg["agents"]), "skills": len(reg["skills"]),
              "scripts": len(reg["scripts"]), "phases": len(model["phases"]),
              "content_sha": model["_provenance"]["content_sha256"][:8]}

    if args.check:
        result["ok"] = not any(stale.values())
        print(json.dumps(result) if args.json else
              ("FRESH" if result["ok"] else f"STALE {stale} — run generate.py"))
        return 0 if result["ok"] else 1

    model_path.write_text(new_json, encoding="utf-8")
    html_path.write_text(new_html, encoding="utf-8")
    arch_path.write_text(new_arch, encoding="utf-8")
    print(json.dumps(result) if args.json else
          f"wrote model.json + injected HTML + ARCHITECTURE.md Components "
          f"({result['agents']} agents · {result['skills']} skills · {result['scripts']} scripts · "
          f"{result['phases']} phases) @ sha {result['content_sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
