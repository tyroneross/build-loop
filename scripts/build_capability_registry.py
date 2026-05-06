#!/usr/bin/env python3
"""Capability registry builder.

Crawls every surface the build-loop orchestrator can route to and emits a
unified registry at `.build-loop/capability-registry.json`. The registry
becomes the orchestrator's narrowed decision space — Anthropic's Tool Search
guidance recommends ≤8 candidates per dispatch, and we have ~78 surfaces
across this repo plus globally-installed plugins.

Surfaces crawled (always opt-in / no network):
    agents/*.md                                  (kind=agent)
    skills/<name>/SKILL.md                        (kind=skill)
    skills/architecture/<name>/SKILL.md            (kind=skill, nested)
    skills/debugging/<name>/SKILL.md               (kind=skill, nested)
    commands/*.md                                  (kind=command)
    hooks/hooks.json (parsed)                      (kind=hook)
    .mcp.json (parsed) → mcpServers + advertised  (kind=mcp_tool)
    scripts/*.py (top-level only, not _attic/)    (kind=script)

For each entry, the registry records:
    name, kind, category, triggers[], tier (opus|sonnet|haiku|n/a),
    tools_consumed, owns_files[], description, source_path.

Categories are coarse routing labels:
    architecture, debugging, validation, planning, execution, observability,
    memory, testing, deployment, ux-ui, optimization, meta, unknown

`triggers[]` is best-effort: agent/skill frontmatter `description` parsed
for verb-style trigger words; commands inherit description; scripts get
heuristic categories from filename patterns.

Stdlib only (json, re, pathlib, argparse). Idempotent. Atomic write
(temp + os.replace).

Exit codes:
    0  — success
    1  — invalid arguments / structural error
    2  — filesystem error (permission, disk full)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

# Keyword → category map. First match wins. Searches against the lowercased
# concat of (name, description, source_path). `meta` is checked first because
# orchestrator-style surfaces also mention review/validate/build in passing
# but fundamentally belong in `meta`.
CATEGORY_KEYWORDS = [
    # `meta` is checked first because orchestrator-layer surfaces also mention
    # review/validate/build in passing but fundamentally belong here. Covers
    # routing infrastructure (shortlist, project resolver, CWD→tag), model
    # selection (tiering), capability-registry tooling, slice/subagent
    # dispatch helpers, and transcript miners.
    ("meta",         ("orchestrat", "self-improv", "pattern-detector",
                      "promote-experiment", "build-orchestrator", "build_orchestrator",
                      "shortlist", "capability registry", "capability-registry",
                      "narrow", "decision space", "subagent dispatch",
                      "project tag", "project_resolver", "cwd to a project",
                      "model tier", "model_tier", "model-tier", "tier",
                      "transcript", "pattern-miner", "pattern miner",
                      "slice", "acp",
                      # Crash recovery / state.json checkpoint surfaces (M1-M4):
                      "crash recovery", "crash-recovery", "state_finalize",
                      "state.json checkpoint", "resume_resolver",
                      "subagent envelope", "subagent_result", "heartbeat",
                      "incomplete build", "run_id provenance",
                      "stop hook annotation")),
    ("architecture", ("architect", "navgator", "blast_radius", "blast-radius",
                      "blast radius", "scout", "scan repo", "component", "graph")),
    ("debugging",    ("debug", "debugger", "incident", "root cause", "root-cause",
                      "trace", "logging-tracer", "memory-first")),
    # Validation includes plugin-hygiene checks (cache sync, namesake
    # collisions) and api-dependency contract checks.
    ("validation",   ("validate", "fact-check", "fact_check", "mock", "critic",
                      "review", "rubric", "lint", "cache sync", "cache-sync",
                      "collision", "namesake", "api dependency", "api-registry",
                      "api config", "api-config")),
    ("planning",     ("plan", "spec", "rfc", "writing-plan", "plan-verify",
                      "prd")),
    ("execution",    ("implement", "execute", "implementer", "build", "ship",
                      "deploy")),
    ("observability", ("observ", "tracing", "telemetry", "logging", "log")),
    ("memory",       ("memory", "decision", "recall", "knowledge", "episodic",
                      "semantic", "embedding", "embed_backend", "retrieval",
                      "backend health", "backend_health",
                      # Phase B/D additions:
                      "search_vector", "chunk_context", "pagerank",
                      "wikilink", "graph leg", "contextual retrieval",
                      # Phase C/G additions:
                      "rerank", "wiki", "rrf", "cross-encoder",
                      "federation", "daemon")),
    ("testing",      ("test", "pytest", "jest", "vitest", "spec")),
    ("deployment",   ("deployment", "release", "publish", "version-bump",
                      "version_advisor")),
    ("ux-ui",        ("ux", "ui", "ibr", "design", "accessibility", "a11y",
                      "calm-precision", "mockup")),
    ("optimization", ("optimize", "metric", "benchmark", "doe")),
]

TRIGGER_VERBS = (
    "build", "implement", "fix", "debug", "review", "validate", "test",
    "scan", "trace", "impact", "plan", "assess", "iterate", "optimize",
    "investigate", "research", "deploy", "release", "migrate", "ingest",
    "summarize", "explain", "compare", "critique", "promote", "rewrite",
    "refactor", "lint", "audit",
)

MODEL_HINTS = {
    "opus": ("opus", "claude-opus", "claude-3-5-opus", "claude-3-7-opus", "claude-opus-4"),
    "sonnet": ("sonnet", "claude-sonnet", "claude-3-5-sonnet", "claude-3-7-sonnet"),
    "haiku": ("haiku", "claude-haiku", "claude-3-5-haiku"),
}

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _read_text(p: Path) -> Optional[str]:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parse_frontmatter(text: str) -> Dict[str, str]:
    """Parse a `name: ...` style YAML-ish frontmatter into a flat dict.

    Stdlib-only — does not handle nested mappings or lists. Designed for
    the small subset of frontmatter shapes used in agents/skills/commands
    in this repo.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    body = m.group(1)
    out: Dict[str, str] = {}
    current_key: Optional[str] = None
    current_buf: List[str] = []
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith(" ") or line.startswith("\t"):
            # continuation of a multi-line value
            if current_key is not None:
                current_buf.append(line.strip())
            continue
        # New key: previous one terminates.
        if current_key is not None:
            out[current_key] = " ".join(current_buf).strip()
        if ":" in line:
            k, _, v = line.partition(":")
            current_key = k.strip()
            current_buf = [v.strip()] if v.strip() else []
        else:
            current_key = None
            current_buf = []
    if current_key is not None:
        out[current_key] = " ".join(current_buf).strip()
    return out


def _classify_category(name: str, description: str, source_path: str) -> str:
    haystack = f"{name} {description} {source_path}".lower()
    for category, kws in CATEGORY_KEYWORDS:
        for kw in kws:
            if kw in haystack:
                return category
    return "unknown"


def _extract_triggers(description: str) -> List[str]:
    if not description:
        return []
    text = description.lower()
    found: List[str] = []
    for verb in TRIGGER_VERBS:
        # Word-boundary match.
        if re.search(r"\b" + re.escape(verb) + r"\b", text):
            found.append(verb)
    # Cap at 8 to keep the registry compact.
    return sorted(set(found))[:8]


def _classify_tier(model_str: str) -> str:
    if not model_str:
        return "n/a"
    m = model_str.lower()
    for tier, hints in MODEL_HINTS.items():
        if any(h in m for h in hints):
            return tier
    if "inherit" in m:
        return "n/a"
    return "n/a"


def _strip_yaml_quotes(s: str) -> str:
    s = s.strip()
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        return s[1:-1]
    return s


def _short(text: str, limit: int = 240) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Crawlers
# ---------------------------------------------------------------------------

def crawl_agents(repo: Path) -> Iterable[Dict[str, Any]]:
    agents_dir = repo / "agents"
    if not agents_dir.is_dir():
        return
    for p in sorted(agents_dir.glob("*.md")):
        text = _read_text(p) or ""
        fm = _parse_frontmatter(text)
        name = _strip_yaml_quotes(fm.get("name", p.stem))
        description = _strip_yaml_quotes(fm.get("description", ""))
        model = _strip_yaml_quotes(fm.get("model", ""))
        tools_raw = _strip_yaml_quotes(fm.get("tools", ""))
        # tools may be a JSON list inline.
        tools_consumed: List[str] = []
        if tools_raw.startswith("["):
            try:
                tools_consumed = [str(t) for t in json.loads(tools_raw)]
            except json.JSONDecodeError:
                tools_consumed = []
        rel = p.relative_to(repo).as_posix()
        yield {
            "name": name,
            "kind": "agent",
            "category": _classify_category(name, description, rel),
            "triggers": _extract_triggers(description),
            "tier": _classify_tier(model),
            "tools_consumed": tools_consumed,
            "owns_files": [rel],
            "description": _short(description),
            "source_path": rel,
        }


def crawl_skills(repo: Path) -> Iterable[Dict[str, Any]]:
    skills_dir = repo / "skills"
    if not skills_dir.is_dir():
        return
    # Walk up to two levels (skills/<name>/SKILL.md and skills/<group>/<name>/SKILL.md).
    for skill_md in sorted(skills_dir.rglob("SKILL.md")):
        text = _read_text(skill_md) or ""
        fm = _parse_frontmatter(text)
        # Derive name: prefer frontmatter, else parent dir.
        name = _strip_yaml_quotes(fm.get("name", skill_md.parent.name))
        description = _strip_yaml_quotes(fm.get("description", ""))
        # Some skills set `model:` (rare) or document tier in body.
        model = _strip_yaml_quotes(fm.get("model", ""))
        rel = skill_md.relative_to(repo).as_posix()
        yield {
            "name": name,
            "kind": "skill",
            "category": _classify_category(name, description, rel),
            "triggers": _extract_triggers(description),
            "tier": _classify_tier(model),
            "tools_consumed": [],
            "owns_files": [rel],
            "description": _short(description),
            "source_path": rel,
        }


def crawl_commands(repo: Path) -> Iterable[Dict[str, Any]]:
    cmd_dir = repo / "commands"
    if not cmd_dir.is_dir():
        return
    for p in sorted(cmd_dir.glob("*.md")):
        text = _read_text(p) or ""
        fm = _parse_frontmatter(text)
        # Commands have description but no name in frontmatter; derive name
        # from filename.
        name = p.stem
        description = _strip_yaml_quotes(fm.get("description", ""))
        rel = p.relative_to(repo).as_posix()
        yield {
            "name": f"/{name}",
            "kind": "command",
            "category": _classify_category(name, description, rel),
            "triggers": _extract_triggers(description),
            "tier": "n/a",
            "tools_consumed": [],
            "owns_files": [rel],
            "description": _short(description),
            "source_path": rel,
        }


def crawl_hooks(repo: Path) -> Iterable[Dict[str, Any]]:
    hooks_json = repo / "hooks" / "hooks.json"
    if not hooks_json.is_file():
        return
    try:
        data = json.loads(hooks_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    rel = hooks_json.relative_to(repo).as_posix()
    for event_name, entries in (data.get("hooks") or {}).items():
        for idx, entry in enumerate(entries or []):
            matcher = entry.get("matcher", "")
            for hook_idx, hook in enumerate(entry.get("hooks") or []):
                cmd_str = (hook.get("command") or "")[:240]
                handle = f"{event_name}[{idx}.{hook_idx}]"
                if matcher:
                    handle += f" matcher={matcher}"
                yield {
                    "name": handle,
                    "kind": "hook",
                    "category": _classify_category(handle, cmd_str, rel),
                    "triggers": [event_name.lower()],
                    "tier": "n/a",
                    "tools_consumed": [],
                    "owns_files": [rel],
                    "description": _short(cmd_str),
                    "source_path": rel,
                }


def crawl_mcp(repo: Path) -> Iterable[Dict[str, Any]]:
    mcp = repo / ".mcp.json"
    if not mcp.is_file():
        return
    try:
        data = json.loads(mcp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    rel = mcp.relative_to(repo).as_posix()
    for server_name, cfg in (data.get("mcpServers") or {}).items():
        # Each MCP server is one capability; we don't know the tool list
        # without runtime inspection, so we register the SERVER as a
        # capability and tag the category by name.
        cmd = cfg.get("command", "")
        args = " ".join(cfg.get("args", []))
        desc = f"MCP server `{server_name}`: {cmd} {args}".strip()
        yield {
            "name": f"mcp:{server_name}",
            "kind": "mcp_tool",
            "category": _classify_category(server_name, desc, rel),
            "triggers": [],
            "tier": "n/a",
            "tools_consumed": [],
            "owns_files": [rel],
            "description": _short(desc),
            "source_path": rel,
        }


def crawl_scripts(repo: Path) -> Iterable[Dict[str, Any]]:
    scripts_dir = repo / "scripts"
    if not scripts_dir.is_dir():
        return
    for p in sorted(scripts_dir.glob("*.py")):
        # Skip private/helper-style and tests.
        if p.name.startswith("_") or p.name.startswith("test_"):
            continue
        text = _read_text(p) or ""
        # Take the first docstring as description, if present.
        m = re.search(r'"""(.+?)"""', text, re.DOTALL)
        description = (m.group(1).strip().splitlines()[0] if m else "").strip()
        rel = p.relative_to(repo).as_posix()
        name = p.stem
        yield {
            "name": name,
            "kind": "script",
            "category": _classify_category(name, description, rel),
            "triggers": _extract_triggers(description),
            "tier": "n/a",
            "tools_consumed": [],
            "owns_files": [rel],
            "description": _short(description),
            "source_path": rel,
        }


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def build_registry(repo: Path) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    for fn in (crawl_agents, crawl_skills, crawl_commands,
               crawl_hooks, crawl_mcp, crawl_scripts):
        entries.extend(fn(repo))
    # Sort for stable output: kind, then name.
    entries.sort(key=lambda e: (e["kind"], e["name"]))
    by_kind: Dict[str, int] = {}
    by_category: Dict[str, int] = {}
    for e in entries:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        by_category[e["category"]] = by_category.get(e["category"], 0) + 1
    return {
        "schema_version": "1.0.0",
        "generator": "build_capability_registry.py",
        "generator_version": "0.1.0",
        "repo_root": str(repo),
        "total": len(entries),
        "counts_by_kind": by_kind,
        "counts_by_category": by_category,
        "entries": entries,
    }


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workdir",
        default=str(REPO_ROOT_DEFAULT),
        help="Repo root to crawl (default: build-loop repo).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Override output path (default: <workdir>/.build-loop/capability-registry.json).",
    )
    parser.add_argument("--json", action="store_true",
                        help="Print the registry to stdout instead of writing to disk.")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    if not workdir.is_dir():
        print(f"workdir does not exist: {workdir}", file=sys.stderr)
        return 1

    registry = build_registry(workdir)

    if args.json:
        json.dump(registry, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    out_path = (
        Path(args.out).resolve()
        if args.out
        else workdir / ".build-loop" / "capability-registry.json"
    )
    try:
        atomic_write_json(out_path, registry)
    except OSError as e:
        print(f"filesystem error: {e}", file=sys.stderr)
        return 2
    print(
        f"wrote {registry['total']} capabilities to {out_path} "
        f"(by_kind={registry['counts_by_kind']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
