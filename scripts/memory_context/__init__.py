#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Fast, host-neutral context surface for build-loop-memory.

This is the core API behind the `blm` CLI.  It deliberately stays file-first:
no MCP server, no HTTP daemon, and no Postgres requirement.  The generated
`CURRENT.json`/`CURRENT.md` files are the L0 hot capsule; expand mode can add
the existing SQLite lessons index without changing the capsule contract.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _paths import (  # type: ignore  # noqa: E402
    memory_indexes_dir,
    memory_store_root,
    project_decisions_dir,
    project_lessons_dir,
    project_root,
    top_level_lessons_dir,
)
from project_resolver import resolve_project  # type: ignore  # noqa: E402

SCHEMA_VERSION = "1.0.0"
KIND_CURRENT = "build-loop-memory-current"
KIND_CONTEXT = "build-loop-memory-context"
KIND_STATUS = "build-loop-memory-status"
VALID_MODES = {"fast", "expand"}
DEFAULT_LIMIT = 5
DEFAULT_MAX_CHARS = 900


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_text(path: Path, max_chars: int | None = None) -> tuple[str | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"missing: {path}"
    except OSError as exc:
        return None, f"read_error: {path}: {exc}"
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars], f"truncated: {path}: first {max_chars} chars"
    return text, None


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _git_info(path: Path) -> dict[str, Any]:
    def run(args: list[str]) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(path),
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            return False, str(exc)
        return True, proc.stdout.strip()

    ok_branch, branch = run(["rev-parse", "--abbrev-ref", "HEAD"])
    ok_commit, commit = run(["rev-parse", "--short", "HEAD"])
    ok_dirty, status = run(["status", "--porcelain"])
    if not (ok_branch and ok_commit):
        return {
            "ok": False,
            "branch": "",
            "commit": "",
            "dirty_count": None,
            "error": branch if not ok_branch else commit,
        }
    return {
        "ok": True,
        "branch": branch,
        "commit": commit,
        "dirty_count": len([line for line in status.splitlines() if line.strip()]) if ok_dirty else None,
    }


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    block = text[4:end]
    body = text[end + 5 :]
    out: dict[str, Any] = {}
    for line in block.splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out, body


def _first_heading_or_stem(path: Path, text: str, fm: dict[str, Any]) -> str:
    for key in ("title", "name", "id", "canonical_id"):
        if fm.get(key):
            return str(fm[key])
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
    return path.stem


def _compact_body(body: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", body.strip())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _recent_markdown(
    directory: Path,
    *,
    prefix: str,
    limit: int,
    max_chars: int,
) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    candidates = [p for p in directory.glob("*.md") if p.name not in {"INDEX.md", "README.md", "MEMORY.md"}]
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    rows: list[dict[str, Any]] = []
    for path in candidates[:limit]:
        text, reason = _read_text(path, max_chars=max_chars * 2)
        if text is None:
            rows.append({"id": f"{prefix}:{path.stem}", "path": str(path), "exists": False, "reason": reason})
            continue
        fm, body = _parse_frontmatter(text)
        rows.append(
            {
                "id": str(fm.get("canonical_id") or fm.get("id") or f"{prefix}:{path.stem}"),
                "title": _first_heading_or_stem(path, text, fm),
                "path": str(path),
                "summary": _compact_body(body, max_chars=max_chars),
            }
        )
    return rows


def _context_summary(project_dir: Path, max_chars: int) -> dict[str, Any]:
    path = project_dir / "context" / "CONTEXT.md"
    text, reason = _read_text(path, max_chars=max_chars * 4)
    if text is None:
        return {"path": str(path), "exists": False, "summary": "", "reason": reason}
    _fm, body = _parse_frontmatter(text)
    summary = ""
    marker = "## Governing Summary"
    if marker in body:
        after = body.split(marker, 1)[1]
        next_section = re.split(r"\n##\s+", after, maxsplit=1)[0]
        summary = next_section.strip()
    if not summary:
        summary = body.strip()
    return {
        "id": "context:CONTEXT",
        "path": str(path),
        "exists": True,
        "summary": _compact_body(summary, max_chars=max_chars),
    }


def _count_jsonl(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return None


def _index_state() -> dict[str, Any]:
    idx = memory_indexes_dir()
    index_path = idx / "INDEX.jsonl"
    graph_nodes = idx / "graph-nodes.jsonl"
    graph_edges = idx / "graph-edges.jsonl"
    try:
        index_mtime = datetime.fromtimestamp(index_path.stat().st_mtime, timezone.utc).isoformat() if index_path.exists() else None
    except OSError:
        index_mtime = None
    return {
        "index_path": str(index_path),
        "index_rows": _count_jsonl(index_path),
        "graph_nodes": _count_jsonl(graph_nodes),
        "graph_edges": _count_jsonl(graph_edges),
        "index_updated_at": index_mtime,
    }


def _freshness(workdir: Path, project: str, project_dir: Path, warnings: list[str]) -> dict[str, Any]:
    source_git = _git_info(workdir)
    memory_root = memory_store_root().resolve()
    memory_git = _git_info(memory_root)
    validity = "clean"
    if warnings:
        validity = "unknown"
    for info in (source_git, memory_git):
        if not info.get("ok"):
            validity = "unknown"
        elif info.get("dirty_count"):
            validity = "stale"
    if not project_dir.exists():
        validity = "unknown"
    return {
        "validity": validity,
        "generated_at": utc_now(),
        "source_workdir": str(workdir),
        "source_branch": source_git.get("branch", ""),
        "source_commit": source_git.get("commit", ""),
        "source_dirty_count": source_git.get("dirty_count"),
        "memory_root": str(memory_root),
        "memory_project": project,
        "memory_project_dir": str(project_dir),
        "memory_branch": memory_git.get("branch", ""),
        "memory_commit": memory_git.get("commit", ""),
        "memory_dirty_count": memory_git.get("dirty_count"),
        "index": _index_state(),
    }


def _evidence_from_current(current: dict[str, Any]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    ctx = current.get("context") or {}
    if ctx.get("exists") and ctx.get("path"):
        evidence.append({"id": ctx.get("id", "context:CONTEXT"), "path": ctx["path"], "type": "context"})
    for lane in ("decisions", "lessons"):
        for item in current.get(lane, []):
            if item.get("id") and item.get("path"):
                evidence.append({"id": str(item["id"]), "path": str(item["path"]), "type": lane[:-1]})
    return evidence


def build_current(
    workdir: Path | str,
    query: str = "",
    *,
    project: str | None = None,
    limit: int = DEFAULT_LIMIT,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Build the L0 hot capsule without writing it."""
    workdir = Path(workdir).resolve()
    project_tag = project or resolve_project(workdir)
    proj_dir = project_root(project_tag)
    warnings: list[str] = []
    if not proj_dir.exists():
        warnings.append(f"project_memory_missing: {proj_dir}")

    context = _context_summary(proj_dir, max_chars=max_chars)
    if not context.get("exists"):
        warnings.append(str(context.get("reason") or "context_missing"))

    project_decisions = _recent_markdown(
        project_decisions_dir(project_tag),
        prefix="decision",
        limit=limit,
        max_chars=max_chars,
    )
    project_lessons = _recent_markdown(
        project_lessons_dir(project_tag),
        prefix="lesson",
        limit=limit,
        max_chars=max_chars,
    )
    global_lessons = _recent_markdown(
        top_level_lessons_dir(),
        prefix="lesson",
        limit=max(0, limit - len(project_lessons)),
        max_chars=max_chars,
    )
    lessons = project_lessons + global_lessons

    current: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_CURRENT,
        "project": project_tag,
        "workdir": str(workdir),
        "query": query,
        "generated_at": utc_now(),
        "context": context,
        "decisions": project_decisions,
        "lessons": lessons[:limit],
        "next_commands": [
            'blm context --workdir "$PWD" --mode fast --json',
            'blm context --workdir "$PWD" --mode expand --json',
            "blm open --id <evidence-id>",
        ],
        "warnings": warnings,
    }
    current["freshness"] = _freshness(workdir, project_tag, proj_dir, warnings)
    current["evidence"] = _evidence_from_current(current)
    validate_current(current)
    return current


def current_paths(project: str) -> dict[str, Path]:
    context_dir = project_root(project) / "context"
    return {
        "json": context_dir / "CURRENT.json",
        "markdown": context_dir / "CURRENT.md",
        "freshness": context_dir / "freshness.json",
    }


def render_current_markdown(current: dict[str, Any]) -> str:
    freshness = current.get("freshness", {})
    lines = [
        f"# Current Context: {current.get('project', '_unscoped')}",
        "",
        f"- Generated: {current.get('generated_at', '')}",
        f"- Validity: {freshness.get('validity', 'unknown')}",
        f"- Source: {freshness.get('source_branch', '')}@{freshness.get('source_commit', '')}",
        f"- Memory: {freshness.get('memory_branch', '')}@{freshness.get('memory_commit', '')}",
        "",
        "## Immediate Context",
        "",
        str((current.get("context") or {}).get("summary") or "(none)"),
        "",
    ]
    if current.get("decisions"):
        lines.extend(["## Recent Decisions", ""])
        for item in current["decisions"]:
            lines.append(f"- {item.get('title') or item.get('id')} ({item.get('id')})")
        lines.append("")
    if current.get("lessons"):
        lines.extend(["## Relevant Lessons", ""])
        for item in current["lessons"]:
            lines.append(f"- {item.get('title') or item.get('id')} ({item.get('id')})")
        lines.append("")
    if current.get("warnings"):
        lines.extend(["## Warnings", ""])
        for warning in current["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_current(current: dict[str, Any]) -> dict[str, str]:
    validate_current(current)
    paths = current_paths(str(current["project"]))
    _atomic_write_json(paths["json"], current)
    _atomic_write_text(paths["markdown"], render_current_markdown(current))
    _atomic_write_json(paths["freshness"], current["freshness"])
    return {key: str(path) for key, path in paths.items()}


def describe_access(
    workdir: Path | str,
    *,
    project: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8777,
) -> dict[str, Any]:
    """Return the fast-access map agents can use before deeper retrieval."""
    workdir = Path(workdir).resolve()
    project_tag = project or resolve_project(workdir)
    paths = current_paths(project_tag)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_STATUS,
        "project": project_tag,
        "workdir": str(workdir),
        "memory_root": str(memory_store_root().resolve()),
        "project_dir": str(project_root(project_tag)),
        "current_paths": {key: str(path) for key, path in paths.items()},
        "current_exists": {key: path.exists() for key, path in paths.items()},
        "cli": {
            "fast": 'python3 scripts/blm.py context --workdir "$PWD" --mode fast --json',
            "expand": 'python3 scripts/blm.py context --workdir "$PWD" --mode expand --json',
            "open": 'python3 scripts/blm.py open --id <evidence-id> --workdir "$PWD" --json',
            "status": 'python3 scripts/blm.py status --workdir "$PWD" --json',
        },
        "api": {
            "default_host": host,
            "default_port": port,
            "base_url": f"http://{host}:{port}",
            "serve": f"python3 scripts/blm.py serve --host {host} --port {port}",
            "endpoints": [
                "GET /health",
                "GET /context?workdir=<path>&query=<goal>&mode=fast",
                "POST /context",
                "GET /open?id=<evidence-id>&workdir=<path>",
                "POST /open",
            ],
            "write_default": False,
        },
    }


def validate_current(current: dict[str, Any]) -> None:
    required = {"schema_version", "kind", "project", "workdir", "generated_at", "context", "freshness", "evidence", "warnings"}
    missing = required - set(current)
    if missing:
        raise ValueError(f"CURRENT missing required fields: {sorted(missing)}")
    if current["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported CURRENT schema_version: {current['schema_version']!r}")
    if current["kind"] != KIND_CURRENT:
        raise ValueError(f"unsupported CURRENT kind: {current['kind']!r}")
    if not isinstance(current.get("freshness"), dict):
        raise ValueError("CURRENT freshness must be an object")
    if current["freshness"].get("validity") not in {"clean", "stale", "unknown"}:
        raise ValueError("CURRENT freshness.validity must be clean|stale|unknown")
    if not isinstance(current.get("evidence"), list):
        raise ValueError("CURRENT evidence must be a list")


def _expand_with_lessons(query: str, project: str, limit: int) -> dict[str, Any]:
    reasons: list[str] = []
    try:
        import lessons_index as li  # type: ignore  # noqa: PLC0415
    except ImportError as exc:
        return {"lessons": [], "stats": {}, "reasons": [f"lessons_index_import_failed: {exc}"]}
    try:
        ingest_global = li.ingest(project=None)
        ingest_project = li.ingest(project=project)
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"lessons_index_ingest_failed: {exc}")
        ingest_global = {}
        ingest_project = {}
    try:
        lessons = li.query(goal_text=query or project, project=project, limit=limit)
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"lessons_index_query_failed: {exc}")
        lessons = []
    try:
        stats = li.stats()
    except Exception as exc:  # noqa: BLE001
        stats = {"error": str(exc)}
    return {
        "lessons": lessons,
        "stats": stats,
        "ingest": {"global": ingest_global, "project": ingest_project},
        "reasons": reasons,
    }


def _expand_with_graph(current: dict[str, Any], limit: int) -> dict[str, Any]:
    project = str(current.get("project") or "")
    seeds = [f"project:{project}"] if project else []
    if not seeds:
        for item in current.get("evidence", []):
            item_id = str(item.get("id") or "")
            if item_id and not item_id.startswith("context:"):
                seeds.append(item_id)
    # Preserve order while deduping.
    seeds = list(dict.fromkeys(seeds))
    if not seeds:
        return {"related": [], "stats": {}, "seeds": [], "reasons": ["graph_no_seeds"]}

    try:
        from memory_graph import GraphStore  # type: ignore  # noqa: PLC0415
    except ImportError as exc:
        return {
            "related": [],
            "stats": {},
            "seeds": seeds,
            "reasons": [f"memory_graph_import_failed: {exc}"],
        }
    graph = None
    try:
        graph = GraphStore.open()
        result = graph.related(seeds, depth=2, limit=max(limit * 5, 25), project=project)
    except Exception as exc:  # noqa: BLE001
        return {
            "related": [],
            "stats": {},
            "seeds": seeds,
            "reasons": [f"memory_graph_query_failed: {exc}"],
        }
    finally:
        if graph is not None and hasattr(graph, "close"):
            graph.close()

    related = []
    for node in result.get("nodes", []):
        if not node.get("path"):
            continue
        related.append(
            {
                "id": node.get("id"),
                "title": node.get("title"),
                "path": str(memory_store_root() / str(node.get("path"))),
                "project": node.get("project"),
                "memory_type": node.get("memory_type"),
                "hop": node.get("hop"),
                "score": node.get("score"),
            }
        )
    related = related[:limit]
    return {
        "backend": result.get("backend"),
        "query_shape": result.get("query_shape"),
        "related": related,
        "stats": result.get("stats", {}),
        "seeds": result.get("seeds", seeds),
        "reasons": result.get("reasons", []),
    }


def build_context(
    workdir: Path | str,
    query: str = "",
    *,
    mode: str = "fast",
    project: str | None = None,
    write: bool = True,
    limit: int = DEFAULT_LIMIT,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Return a context envelope and optionally persist the L0 current capsule."""
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode {mode!r}; expected one of {sorted(VALID_MODES)}")
    current = build_current(workdir, query=query, project=project, limit=limit, max_chars=max_chars)
    written: dict[str, str] = {}
    if write:
        written = write_current(current)
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_CONTEXT,
        "mode": mode,
        "generated_at": utc_now(),
        "current": current,
        "written": written,
    }
    if mode == "expand":
        expansion = _expand_with_lessons(query, current["project"], limit)
        expansion["graph"] = _expand_with_graph(current, limit)
        envelope["expansion"] = expansion
    return envelope


def open_artifact(
    artifact_id: str,
    *,
    workdir: Path | str,
    project: str | None = None,
    max_chars: int = 8000,
) -> dict[str, Any]:
    """Resolve an evidence id or safe memory-store path and return text."""
    current = build_current(workdir, project=project)
    by_id = {item["id"]: item for item in current.get("evidence", [])}
    target: Path | None = None
    if artifact_id in by_id:
        target = Path(by_id[artifact_id]["path"])
    else:
        raw = Path(os.path.expanduser(artifact_id))
        if not raw.is_absolute():
            raw = memory_store_root() / raw
        try:
            root = memory_store_root().resolve()
            resolved = raw.resolve()
            if str(resolved) == str(root) or str(resolved).startswith(str(root) + os.sep):
                target = resolved
        except (OSError, RuntimeError):
            target = None
    if target is None:
        return {"id": artifact_id, "exists": False, "reason": "unresolved"}
    text, reason = _read_text(target, max_chars=max_chars)
    return {
        "id": artifact_id,
        "path": str(target),
        "exists": text is not None,
        "text": text or "",
        "truncated": bool(reason and reason.startswith("truncated:")),
        "reason": reason,
    }


__all__ = [
    "build_context",
    "build_current",
    "current_paths",
    "describe_access",
    "open_artifact",
    "render_current_markdown",
    "validate_current",
    "write_current",
]
