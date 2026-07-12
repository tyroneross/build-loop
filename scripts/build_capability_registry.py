#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Crawl repo surfaces into the capability registry the orchestrator routes against.
#   application: meta
#   status: active
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
    skills/architecture/<name>/SKILL.md            (kind=skill, nested)
    commands/*.md                                  (kind=command)
    hooks/hooks.json (parsed)                      (kind=hook)
    .mcp.json (parsed) → mcpServers + advertised  (kind=mcp_tool)
    scripts/*.py (top-level only, not _attic/)    (kind=script)

For each entry, the registry records:
    name, kind, category, triggers[], tier (taxonomy ladder rung T0-T5/T-S,
    or n/a), segment (work-role, agents only), tools_consumed, owns_files[],
    description, source_path.

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
                      # Model selection / dispatch resolution surfaces (the
                      # taxonomy, resolver, dispatch fallback, classification):
                      "model selection", "model-selection", "model outage",
                      "model-outage", "model availability", "model_availability",
                      "model-availability", "dispatch fallback", "re-resolve",
                      "taxonomy", "segment", "model resolver", "model_resolver",
                      "classify model", "model_overrides", "model overrides",
                      "transcript", "pattern-miner", "pattern miner",
                      "slice", "acp",
                      # Crash recovery / state.json checkpoint surfaces (M1-M4):
                      "crash recovery", "crash-recovery", "state_finalize",
                      "stop_finalize", "finalize",
                      "state.json checkpoint", "resume_resolver",
                      "subagent envelope", "subagent_result", "heartbeat",
                      "incomplete build", "run_id provenance",
                      "stop hook annotation",
                      # Self-recursion / dogfood signals:
                      "self-recursive", "self_recursive", "dogfood",
                      "drift", "manifest version", "working-copy branch",
                      "working copy", "branch echo",
                      # Worktree GC + completed-but-uncommitted state + the
                      # operator-question resolver are single-run lifecycle infra:
                      "worktree-gc", "worktree gc", "commit_state",
                      "uncommitted", "operator question",
                      # Session-start lifecycle hooks (plugin self-heal,
                      # post-push memory-closeout baton drain) and the host
                      # capability resolver (which wakeup/resume primitives a
                      # host exposes) are run/host lifecycle infrastructure:
                      "plugin-heal", "plugin self-heal", "self-heal",
                      "closeout", "armed baton", "host capability",
                      "host_capabilities", "wakeup", "resume primitive",
                      "coding host",
                      # Marketplace autoupdate compensator (plugin-cache drift
                      # → registry reconcile) is plugin-lifecycle infra:
                      "marketplace", "autoupdate", "installed_plugins",
                      "catalog drift",
                      # Learned-extension lifecycle scripts/hooks route draft
                      # skills through pending/check/approval and are runtime
                      # lifecycle infrastructure, not product execution.
                      "extensions_", "session-start-extensions",
                      "learned skill", "pending drafts", "candidate_aging",
                      "promotion_queue")),
    # Multi-session coordination surfaces — Rally Point bridge, presence,
    # handoffs, and the per-run coord file. Distinct from `meta` (single-run
    # orchestration) because these surfaces coordinate ACROSS agents/sessions.
    ("coordination", ("rally", "coordination", "handoff", "presence", "roster",
                      "inbox", "leadership", "mece", "channel")),
    ("architecture", ("architect", "navgator", "blast_radius", "blast-radius",
                      "blast radius", "scout", "scan repo", "component", "graph",
                      "mermaid", "diagram")),
    ("debugging",    ("debug", "debugger", "incident", "root cause", "root-cause",
                      "trace", "logging-tracer", "memory-first", "sourcekit")),
    # Validation includes plugin-hygiene checks (cache sync, namesake
    # collisions) and api-dependency contract checks.
    ("validation",   ("validate", "fact-check", "fact_check", "mock", "critic",
                      "review", "rubric", "lint", "cache sync", "cache-sync",
                      # Commit-time drift/hygiene gates: artifact regen guard
                      # (keeps checked-in generated artifacts in sync) sits with
                      # the lint/cache-sync hygiene family.
                      "artifact_guard", "artifact guard", "checked-in artifact",
                      "checked-in generated", "artifacts in sync",
                      "collision", "namesake", "api dependency", "api-registry",
                      "api config", "api-config",
                      # Supply-chain + autonomy gating hooks (PreToolUse Bash
                      # gates). Matches the hook command strings so they
                      # classify as validation infrastructure rather than
                      # falling through to the 'unknown' keyword fallback.
                      "supply-chain", "dependency_cooldown",
                      "dependency-cooldown", "pre_bash_autonomy",
                      "pre_bash_dependency", "pre_bash_dispatch",
                      "autonomy_gate", "cooldown",
                      "risk_surface", "risk surface", "risksurfacechange",
                      # Commit/repo-hygiene guards + plugin-cache pruning +
                      # git-hook installation (all validation infrastructure):
                      "audit_before_commit", "attribution", "private slug",
                      "private app slug", "prune", "plugin cache", "git-hooks",
                      # Acceptance-probe contract (gate #1: binds an Assess
                      # criterion to a deterministic Review re-run) and the
                      # reference-activation audit gate (a reference is
                      # reachable when needed) are both validation gates:
                      "acceptance_probe", "acceptance-probe", "acceptance probe",
                      "reference activation", "activation audit",
                      "reference_activation",
                      # Skill-routing guard (PreToolUse Skill + UserPromptSubmit
                      # hooks that route build tasks to build-loop rather than
                      # superpowers:brainstorming) — routing infrastructure:
                      "route-guard", "route guard", "pre-skill",
                      "prompt-submit", "skill-routing", "skill routing",
                      # Privacy/secret scanners are validation gates.
                      "privacy", "secret", "pii")),
    ("planning",     ("plan", "spec", "rfc", "writing-plan", "plan-verify",
                      "prd")),
    ("execution",    ("implement", "execute", "implementer", "build", "ship",
                      "deploy")),
    ("observability", ("observ", "tracing", "telemetry", "logging", "log",
                       "cost-ledger", "cost ledger")),
    ("memory",       ("memory", "decision", "recall", "knowledge", "episodic",
                      "semantic", "embedding", "embed_backend", "retrieval",
                      "backend health", "backend_health",
                      # Phase B/D additions:
                      "search_vector", "chunk_context", "pagerank",
                      "wikilink", "graph leg", "contextual retrieval",
                      # Phase C/G additions:
                      "rerank", "wiki", "rrf", "cross-encoder",
                      "federation", "daemon", "lesson")),
    ("testing",      ("test", "pytest", "jest", "vitest", "spec",
                      "verify gate", "spot-check", "perturbation")),
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

# Tier classification is sourced from the model taxonomy (the single source of
# truth). The old per-model MODEL_HINTS={opus,sonnet,haiku} vocabulary was a
# SECOND, stale tier vocabulary — removed here so there is one taxonomy. An
# agent's tier comes from its frontmatter `tier:` (legacy token or ladder rung)
# first, then a fall-back mapping of its frontmatter `model:` id via the
# taxonomy. `_classify_tier` below returns the taxonomy ladder rung (or "n/a").
try:  # pragma: no cover - import shim
    import model_taxonomy as _mt
except ImportError:  # pragma: no cover
    sys.path.insert(0, str((REPO_ROOT_DEFAULT / "scripts")))
    try:
        import model_taxonomy as _mt  # type: ignore[no-redefine]
    except ImportError:
        _mt = None  # taxonomy unavailable -> tier classification degrades to n/a

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# G5 — authored capability header. A script declares its lifecycle by
# embedding a `# capability:` block near the top, e.g.:
#
#   # capability:
#   #   purpose: Build the capability registry from repo surfaces.
#   #   application: meta
#   #   status: active
#
# The header is read instead of heuristic guessing. An absent header
# yields `status: unknown` so the relevance detector can flag it.
# `status` is one of: active | deprecated | oneshot-complete | experimental.
_VALID_STATUSES = ("active", "deprecated", "oneshot-complete", "experimental")
# Match a `# capability:` comment block — the header lines are `#`-prefixed
# `key: value` pairs immediately following. Scans the first ~60 lines only.
_CAP_HEADER_KEY_RE = re.compile(r"^#\s*(purpose|application|status)\s*:\s*(.+?)\s*$")


def _parse_capability_header(text: str) -> Dict[str, str]:
    """Extract an authored `# capability:` header from a script.

    Returns a dict with any of {purpose, application, status} that were
    declared. Absent header or absent keys -> empty/partial dict. The
    caller fills `status: unknown` when `status` is missing.
    """
    out: Dict[str, str] = {}
    lines = text.splitlines()[:60]
    in_block = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^#\s*capability\s*:\s*$", stripped):
            in_block = True
            continue
        if in_block:
            m = _CAP_HEADER_KEY_RE.match(stripped)
            if m:
                out[m.group(1)] = m.group(2).strip()
                continue
            # A non-`#` line or a `#` line that is not a header key ends
            # the block.
            if not stripped.startswith("#") or stripped == "#":
                break
            # A `#` comment line that is not a recognised key also ends it
            # unless it is blank-ish; be strict to avoid swallowing prose.
            break
    return out


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


def _classify_tier(tier_str: str, model_str: str = "") -> str:
    """Return the taxonomy ladder rung for an agent.

    Priority: explicit frontmatter `tier:` (legacy token or ladder rung) ->
    fall back to mapping the frontmatter `model:` id via the taxonomy seed
    registry. `inherit` / unknown -> "n/a". Sourced from references/
    model-taxonomy.json (the single tier vocabulary)."""
    if _mt is None:
        return "n/a"
    # 1. Explicit tier token wins.
    if tier_str:
        try:
            return _mt.normalize_tier(tier_str.strip())
        except ValueError:
            pass  # not a recognized tier token; fall through to model mapping
    # 2. Map the concrete model id to its tier via the taxonomy.
    if model_str:
        mid = model_str.strip().lower()
        if mid == "inherit":
            return "n/a"
        meta = _mt.model_meta(mid)
        if meta and meta.get("tier"):
            return meta["tier"]
    return "n/a"


def _classify_segment(segment_str: str, model_str: str = "") -> str:
    """Return the agent's work-role segment.

    Priority: explicit frontmatter `segment:` -> map the frontmatter `model:`
    id's segment via the taxonomy -> "n/a"."""
    if _mt is None:
        return "n/a"
    if segment_str and segment_str.strip() in _mt.segments():
        return segment_str.strip()
    if model_str:
        mid = model_str.strip().lower()
        if mid == "inherit":
            return "n/a"
        meta = _mt.model_meta(mid)
        if meta and meta.get("segment"):
            return meta["segment"]
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
        tier_fm = _strip_yaml_quotes(fm.get("tier", ""))
        segment_fm = _strip_yaml_quotes(fm.get("segment", ""))
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
            "tier": _classify_tier(tier_fm, model),
            "segment": _classify_segment(segment_fm, model),
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
        # Some skills set `model:`/`tier:` (rare) or document tier in body.
        model = _strip_yaml_quotes(fm.get("model", ""))
        tier_fm = _strip_yaml_quotes(fm.get("tier", ""))
        rel = skill_md.relative_to(repo).as_posix()
        yield {
            "name": name,
            "kind": "skill",
            "category": _classify_category(name, description, rel),
            "triggers": _extract_triggers(description),
            "tier": _classify_tier(tier_fm, model),
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

        # G5 — prefer the authored capability header over heuristics.
        header = _parse_capability_header(text)
        status = header.get("status", "").strip().lower()
        if status not in _VALID_STATUSES:
            # Absent or malformed header -> flagged for the relevance detector.
            status = "unknown"
        authored_app = header.get("application", "").strip().lower()
        if authored_app:
            category = authored_app
            category_source = "authored"
        else:
            category = _classify_category(name, description, rel)
            category_source = "heuristic"
        purpose = header.get("purpose", "").strip()

        yield {
            "name": name,
            "kind": "script",
            "category": category,
            "category_source": category_source,
            "status": status,
            "purpose": purpose or _short(description),
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
    # Seed counts_by_kind with every structurally-supported surface kind so a
    # kind with zero observed instances in this repo (e.g. `mcp_tool` when there
    # is no .mcp.json) still appears in the schema. Routing code reads this as
    # "kind X is a recognized surface", not "X has N instances".
    SUPPORTED_KINDS = ("agent", "skill", "command", "hook", "mcp_tool", "script")
    by_kind: Dict[str, int] = {k: 0 for k in SUPPORTED_KINDS}
    by_category: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for e in entries:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        by_category[e["category"]] = by_category.get(e["category"], 0) + 1
        # `status` is script-only (G5 authored header); other kinds omit it.
        if "status" in e:
            by_status[e["status"]] = by_status.get(e["status"], 0) + 1
    return {
        "schema_version": "1.1.0",
        "generator": "build_capability_registry.py",
        "generator_version": "0.2.0",
        "repo_root": str(repo),
        "total": len(entries),
        "counts_by_kind": by_kind,
        "counts_by_category": by_category,
        "counts_by_script_status": by_status,
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
