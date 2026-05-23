#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Unified read facade over build-loop's four memory stores.

Phase 6 Learn must see signals from all stores. Today's reality:
  1. .build-loop/state.json.runs[]                — local file
  2. .episodic/decisions/*.md                     — local files
  3. agent_memory.<schema>.semantic_facts         — Postgres
  4. claude-code-debugger MCP `search` tool       — MCP server

Four read paths, four discovery costs. This module collapses them behind one
function:

    recall(query, kind=None, project=None, limit=10) -> RecallEnvelope

`kind` filters by store name: "runs" | "decisions" | "semantic" | "debugger"
(or None for all). `project` filters semantic_facts by project label. `limit`
is per-store cap (the merged result returns up to `4 * limit`).

Each backend degrades gracefully:
  - state.json runs   → returns [] silently if file missing.
  - episodic dirs     → returns [] silently if dir missing or empty.
  - Postgres          → returns [] AND records reason="db_unavailable" when
                        no DB URL is configured (BUILD_LOOP_DATABASE_URL /
                        DATABASE_URL / connection.env all unset), psycopg is
                        missing, or the connection fails. Never raises.
  - debugger MCP      → returns [] AND records reason="mcp_unavailable" when
                        the MCP server is not running. Detection is via the
                        bundled `dist/src/mcp/server.js` reachability check;
                        we never spawn it from here.

Output envelope:

    {
      "query": "<echo>",
      "kind_filter": null | "runs" | ...,
      "project": null | "<resolved>",
      "results_by_kind": {
        "runs":      [{ ...run_entry... }],
        "decisions": [{ id, title, path, summary, ... }],
        "semantic":  [{ ...row... }],
        "debugger":  [{ ...incident... }]
      },
      "merged":   [...top-N by recency, mixed kinds...],
      "reasons":  ["db_unavailable: psycopg not installed", ...]
    }

`merged` is sorted by `_recency_ts` descending (best-effort timestamps; rows
without a parseable timestamp sink to the bottom in stable order).

Stdlib only at import. Postgres backend imports psycopg lazily inside the
function so unavailable systems don't crash the import.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]

# Shared DB-URL resolver. `_db_url` is stdlib-only (os, pathlib) so this
# import keeps memory_facade stdlib-only-at-import (it must NOT import
# `db.py`, which pulls psycopg at module top).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from _db_url import NO_URL_REASON, resolve_db_url  # noqa: E402

DEFAULT_LIMIT = 10
KINDS = ("runs", "decisions", "lessons", "semantic", "debugger")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso(ts: Any) -> Optional[float]:
    """Best-effort parse of an ISO-8601 timestamp into a float (Unix seconds)."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # Heuristic: bare ms vs s.
        return ts / 1000.0 if ts > 1e12 else float(ts)
    if not isinstance(ts, str):
        return None
    s = ts.strip().rstrip("Z")
    try:
        # datetime.fromisoformat handles most shapes; fall back on failure.
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, AttributeError):
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, AttributeError):
            return None


def _q_match(text: str, query: str) -> bool:
    """Case-insensitive substring match. Empty query matches everything."""
    if not query:
        return True
    return query.lower() in (text or "").lower()


# ---------------------------------------------------------------------------
# Backend 1: state.json.runs[]
# ---------------------------------------------------------------------------

def read_runs(workdir: Path, query: str, limit: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    state_path = workdir / ".build-loop" / "state.json"
    reasons: List[str] = []
    if not state_path.is_file():
        return [], reasons
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        reasons.append(f"runs_read_error: {e}")
        return [], reasons
    runs = state.get("runs") or []
    out: List[Dict[str, Any]] = []
    for r in runs:
        text = " ".join([
            str(r.get("goal", "")),
            str(r.get("outcome", "")),
            " ".join(r.get("filesTouched", []) or []),
        ])
        if not _q_match(text, query):
            continue
        ts = _parse_iso(r.get("date"))
        out.append({
            "_kind": "runs",
            "_recency_ts": ts,
            "run_id": r.get("run_id"),
            "goal": r.get("goal"),
            "outcome": r.get("outcome"),
            "date": r.get("date"),
            "files_touched": r.get("filesTouched", []),
        })
    out.sort(key=lambda x: x["_recency_ts"] or 0, reverse=True)
    return out[:limit], reasons


# ---------------------------------------------------------------------------
# Backend 2: .episodic/decisions/*.md
# ---------------------------------------------------------------------------

DECISION_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _resolve_decision_dirs(workdir: Path) -> List[Path]:
    """Decisions may live in two places after the v0.10.0 cutover:

      1. <workdir>/.episodic/decisions/                    (legacy per-repo)
      2. ~/dev/git-folder/build-loop-memory/decisions/<project>/
         (new repo-deletion-survivable global store; project resolves via
         scripts/_paths.py + scripts/project_resolver.py)

    Both shapes are read; results merge. The resolver is best-effort —
    when imports fail we still fall back to the legacy path.
    """
    dirs: List[Path] = []
    legacy = workdir / ".episodic" / "decisions"
    if legacy.is_dir():
        dirs.append(legacy)
    try:
        from _paths import decisions_dir_for_project  # type: ignore  # noqa: PLC0415
        from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415
        proj = resolve_project(workdir)
        if proj:
            new_dir = decisions_dir_for_project(proj)
            if new_dir.is_dir() and new_dir not in dirs:
                dirs.append(new_dir)
    except Exception:  # noqa: BLE001 — best-effort path resolution
        pass
    return dirs


# ---------------------------------------------------------------------------
# Backend 2.5: lessons — free-form feedback/pattern/reference/decision_* files
# in ~/.build-loop/memory/ (global) + ~/.build-loop/memory/projects/<slug>/
# (project, NEW PR 1) + <workdir>/.build-loop/memory/ (legacy project path,
# read-only during PR 1/2 transition).
#
# Distinct from `decisions` (Backend 2) — decisions are the project-tagged
# sequence-numbered store written by write_decision.py. Lessons are the
# free-form taxonomized markdown files written by hand or by memory_writer.py.
# Both backends read the same physical filesystem in some cases but apply
# different filename / frontmatter conventions.
# ---------------------------------------------------------------------------


def _resolve_memory_dirs(workdir: Path) -> List[Tuple[Path, str]]:
    """Return ``[(dir, scope), ...]`` for the build-loop memory tree.

    Returns (in this exact order — order matters):
      1. ``(global_dir, "global")`` — ``~/.build-loop/memory`` (always probed)
      2. ``(project_dir, "project")`` — ``~/.build-loop/memory/projects/<slug>``
         (only when the resolved project tag is not ``_unscoped``)

    Callers dedup by filename with **later entries OVERRIDING earlier ones**,
    so the precedence is:

        project > global

    Only existing directories are returned; missing ones are silently
    dropped.

    PR 3 (2026-05-13): the transitional ``legacy_project`` tier that read
    from the per-repo location has been REMOVED. Any content still at the
    legacy path is now invisible to recall — operators holding
    pre-migration content should run ``scripts/migrate_project_memory.py
    --apply`` or merge it manually. The ``.MOVED.md`` stubs left by the
    migration script are inert post-PR-3 and can be deleted.
    """
    out: List[Tuple[Path, str]] = []
    try:
        from _paths import (  # type: ignore  # noqa: PLC0415
            build_loop_memory_root,
            project_memory_dir_for_project,
        )
        from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — best-effort
        return out

    global_dir = build_loop_memory_root()
    if global_dir.is_dir():
        out.append((global_dir, "global"))

    proj = resolve_project(workdir)
    if proj and proj != "_unscoped":
        try:
            project_dir = project_memory_dir_for_project(proj)
        except ValueError:
            project_dir = None  # type: ignore[assignment]
        if project_dir is not None and project_dir.is_dir():
            out.append((project_dir, "project"))

    return out


# Lessons frontmatter pattern — same shape as decisions.
_LESSON_FRONTMATTER_RE = DECISION_FRONTMATTER_RE


def read_lessons(
    workdir: Path, query: str, limit: int
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read free-form lessons across global + project tiers.

    Dedup rule: same filename across tiers — later-listed tier wins
    (project > global per ``_resolve_memory_dirs`` order). The result
    entry carries ``_scope`` ("global" | "project") so callers can see
    which tier won.

    Filename convention: ``feedback_*.md``, ``pattern_*.md``, ``reference_*.md``,
    ``decision_*.md`` (the free-form variant), plus operator-named files like
    ``gotcha_*.md`` or ``lesson_*.md``. ``MEMORY.md``, ``constitution.md``, and
    ``INDEX.jsonl`` are skipped (they're indexes, not lessons).
    """
    reasons: List[str] = []
    dirs = _resolve_memory_dirs(workdir)
    if not dirs:
        return [], reasons

    # Walk in order; later overrides earlier on filename collision.
    by_name: Dict[str, Dict[str, Any]] = {}
    for mem_dir, scope in dirs:
        for p in sorted(mem_dir.glob("*.md")):
            name = p.name
            # Skip indexes / scaffolding files
            if name in {"MEMORY.md", "constitution.md", "README.md"}:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError as e:
                reasons.append(f"lesson_read_error: {p.name} {e}")
                continue
            title = ""
            ts_raw: Optional[str] = None
            mtype = ""
            m = _LESSON_FRONTMATTER_RE.match(text)
            if m:
                for line in m.group(1).splitlines():
                    s = line.strip()
                    if s.startswith("name:"):
                        title = s.split(":", 1)[1].strip().strip('"').strip("'")
                    elif s.startswith("description:"):
                        # description is the relevance hook
                        if not title:
                            title = s.split(":", 1)[1].strip().strip('"').strip("'")
                    elif s.startswith("type:") or s.startswith("metadata:"):
                        # type lives inside metadata block; capture if present
                        pass
                    elif s.startswith("- type:") or (s.startswith("type:") and "metadata" not in s):
                        mtype = s.split(":", 1)[1].strip().strip('"').strip("'")
            if not _q_match(text + " " + title + " " + name, query):
                continue
            # Best-effort recency from mtime (no created_at field yet on legacy entries).
            try:
                ts = p.stat().st_mtime
            except OSError:
                ts = None
            entry = {
                "_kind": "lessons",
                "_scope": scope,
                "_recency_ts": ts,
                "id": p.stem,
                "name": name,
                "title": title or p.stem,
                "metadata_type": mtype,
                "path": str(p),
            }
            # Later tier wins (project > global).
            by_name[name] = entry

    out = list(by_name.values())
    out.sort(key=lambda x: x.get("_recency_ts") or 0, reverse=True)
    return out[:limit], reasons


def read_decisions(workdir: Path, query: str, limit: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    reasons: List[str] = []
    dec_dirs = _resolve_decision_dirs(workdir)
    if not dec_dirs:
        return [], reasons
    out: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for dec_dir in dec_dirs:
        for p in sorted(dec_dir.glob("*.md")):
            stem = p.stem
            # Skip browseable index files; only NNNN-... shape decisions count.
            if stem.upper().startswith("INDEX") or stem.startswith("_"):
                continue
            if stem in seen_ids:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError as e:
                reasons.append(f"decision_read_error: {p.name} {e}")
                continue
            m = DECISION_FRONTMATTER_RE.match(text)
            title = ""
            ts_raw: Optional[str] = None
            primary_tag = ""
            if m:
                for line in m.group(1).splitlines():
                    if line.startswith("title:"):
                        title = line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("date:"):
                        ts_raw = line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("primary_tag:"):
                        primary_tag = line.split(":", 1)[1].strip().strip('"').strip("'")
            if not _q_match(text + " " + title, query):
                continue
            body = text[m.end():] if m else text
            summary_lines = [
                ln.strip() for ln in body.splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            summary = summary_lines[0][:240] if summary_lines else ""
            try:
                rel_path = str(p.relative_to(workdir))
            except ValueError:
                # File lives outside workdir (global store) — record absolute.
                rel_path = str(p)
            out.append({
                "_kind": "decisions",
                "_recency_ts": _parse_iso(ts_raw),
                "id": stem,
                "title": title,
                "primary_tag": primary_tag,
                "path": rel_path,
                "summary": summary,
            })
            seen_ids.add(stem)
    out.sort(key=lambda x: x["_recency_ts"] or 0, reverse=True)
    return out[:limit], reasons


# ---------------------------------------------------------------------------
# Backend 3: agent_memory.<schema>.semantic_facts (Postgres)
# ---------------------------------------------------------------------------

def read_semantic(
    workdir: Path,
    query: str,
    limit: int,
    project: Optional[str],
    skip_postgres: bool = False,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read semantic_facts from Postgres.

    `skip_postgres=True` (Priority 21): bypass the backend entirely without
    even attempting a connection. Reason recorded as `skipped_postgres`
    (distinct from `db_unavailable: ...` so the consumer can tell whether
    the skip was intentional vs the backend genuinely down). Used by the
    Phase 5 Iterate Backend Short-circuit when `state.json.architecture.
    backendHealth.semantic.ok == false` — saves the 3-second connect_timeout
    on every recall during the iterate cycle.
    """
    reasons: List[str] = []
    if skip_postgres:
        reasons.append("skipped_postgres")
        return [], reasons
    db_url = resolve_db_url()
    if not db_url:
        reasons.append(f"db_unavailable: {NO_URL_REASON}")
        return [], reasons
    try:
        # Lazy import — many environments don't have psycopg.
        import psycopg  # type: ignore  # noqa: PLC0415
    except ImportError:
        reasons.append("db_unavailable: psycopg not installed")
        return [], reasons

    schema = os.environ.get("AGENT_MEMORY_SCHEMA", "personal_memory")
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        reasons.append(f"db_unavailable: unsafe schema {schema!r}")
        return [], reasons

    out: List[Dict[str, Any]] = []
    try:
        with psycopg.connect(db_url, connect_timeout=3) as conn:  # type: ignore
            with conn.cursor() as cur:
                where = ["status = 'active'"]
                params: List[Any] = []
                if project:
                    where.append("project = %s")
                    params.append(project)
                if query:
                    where.append("(subject ILIKE %s OR predicate ILIKE %s OR object ILIKE %s)")
                    params.extend([f"%{query}%"] * 3)
                sql = (
                    f'SELECT id, subject, predicate, object, project, '
                    f'confidence, last_accessed FROM {schema}.semantic_facts '
                    f'WHERE {" AND ".join(where)} '
                    f'ORDER BY last_accessed DESC LIMIT %s'
                )
                params.append(limit)
                cur.execute(sql, params)
                rows = cur.fetchall()
                for r in rows:
                    fact_id, subject, predicate, obj, proj, conf, last = r
                    out.append({
                        "_kind": "semantic",
                        "_recency_ts": _parse_iso(last) or (
                            last.timestamp() if hasattr(last, "timestamp") else None
                        ),
                        "id": fact_id,
                        "subject": subject,
                        "predicate": predicate,
                        "object": obj,
                        "project": proj,
                        "confidence": conf,
                        "last_accessed": str(last) if last else None,
                    })
    except Exception as e:  # noqa: BLE001 — graceful degradation contract
        reasons.append(f"db_unavailable: {type(e).__name__}: {e}")
        return [], reasons
    return out, reasons


# ---------------------------------------------------------------------------
# Backend 4: build-loop debugger MCP (claude-code-debugger)
# ---------------------------------------------------------------------------

def read_debugger(
    workdir: Path,
    query: str,
    limit: int,
    project: Optional[str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Best-effort MCP read.

    The MCP server is bundled at `dist/src/mcp/server.js` (relative to the
    plugin root). We do NOT spawn the server from here — that's the
    orchestrator's job. Instead we attempt to invoke the CLI mode of the
    same package if it's installed; otherwise we return an empty list with
    a `mcp_unavailable` reason. Tests inject a mock at
    `_DEBUGGER_RUNNER_OVERRIDE`.
    """
    reasons: List[str] = []
    runner = _DEBUGGER_RUNNER_OVERRIDE
    if runner is None:
        # Probe for the npx-based CLI.
        try:
            proc = subprocess.run(
                ["npx", "--no-install", "@tyroneross/claude-code-debugger",
                 "search", "--query", query or "*",
                 "--limit", str(limit), "--json"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            reasons.append(f"mcp_unavailable: {type(e).__name__}: {e}")
            return [], reasons
        if proc.returncode != 0:
            reasons.append(f"mcp_unavailable: cli rc={proc.returncode}")
            return [], reasons
        out_text = proc.stdout
    else:
        out_text = runner(query=query, limit=limit, project=project)

    try:
        payload = json.loads(out_text) if out_text else {"incidents": []}
    except json.JSONDecodeError as e:
        reasons.append(f"mcp_unavailable: bad json: {e}")
        return [], reasons
    incidents = payload.get("incidents") or payload.get("results") or []
    out: List[Dict[str, Any]] = []
    for inc in incidents[:limit]:
        out.append({
            "_kind": "debugger",
            "_recency_ts": _parse_iso(inc.get("created_at") or inc.get("date")),
            "id": inc.get("id") or inc.get("incident_id"),
            "symptom": inc.get("symptom"),
            "root_cause": inc.get("root_cause"),
            "fix": inc.get("fix"),
            "project": inc.get("project"),
        })
    return out, reasons


# Test-injection seam.
_DEBUGGER_RUNNER_OVERRIDE: Optional[Any] = None


def set_debugger_runner(fn: Optional[Any]) -> None:
    """Inject a callable used by `read_debugger` instead of the npx CLI.

    Tests pass `lambda query, limit, project: '{"incidents":[...]}'`.
    """
    global _DEBUGGER_RUNNER_OVERRIDE
    _DEBUGGER_RUNNER_OVERRIDE = fn


# ---------------------------------------------------------------------------
# Top-level: recall()
# ---------------------------------------------------------------------------

def recall(
    query: str = "",
    kind: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    workdir: Optional[Path] = None,
    skip_postgres: bool = False,
) -> Dict[str, Any]:
    """Unified read across the four memory backends. See module docstring.

    `skip_postgres=True` (Priority 21): the Postgres-backed semantic backend
    is bypassed entirely. Used by Phase 5 Iterate's Backend Short-circuit
    step when `state.json.architecture.backendHealth.semantic.ok == false`.
    The `reasons[]` envelope marks the skip as `skipped_postgres` (distinct
    from `db_unavailable: ...`) so consumers can tell intentional skip
    from genuine backend-down.
    """
    if kind is not None and kind not in KINDS:
        raise ValueError(f"invalid kind {kind!r}; expected one of {KINDS}")
    workdir = (workdir or Path.cwd()).resolve()
    results: Dict[str, List[Dict[str, Any]]] = {k: [] for k in KINDS}
    reasons: List[str] = []

    if kind in (None, "runs"):
        results["runs"], r = read_runs(workdir, query, limit)
        reasons.extend(r)
    if kind in (None, "decisions"):
        results["decisions"], r = read_decisions(workdir, query, limit)
        reasons.extend(r)
    if kind in (None, "lessons"):
        results["lessons"], r = read_lessons(workdir, query, limit)
        reasons.extend(r)
    if kind in (None, "semantic"):
        results["semantic"], r = read_semantic(
            workdir, query, limit, project, skip_postgres=skip_postgres,
        )
        reasons.extend(r)
    if kind in (None, "debugger"):
        results["debugger"], r = read_debugger(workdir, query, limit, project)
        reasons.extend(r)

    # Merge: sort by recency desc, falling back to stable per-kind order.
    merged: List[Dict[str, Any]] = []
    for k in KINDS:
        merged.extend(results[k])
    merged.sort(key=lambda x: (x.get("_recency_ts") or 0), reverse=True)

    # M5 + Step 8: emit memory-read telemetry (separate file from M5
    # INDEX.jsonl; preserves discovery schema untouched). Fire-and-forget.
    # `correlation_id` is returned so the caller can later emit a follow-up
    # memory-effect row once the consumer acts on the result.
    correlation_id: Optional[str] = None
    try:
        from scripts import memory_telemetry as _mt  # local import keeps module optional
        seen_ids = [r.get("id") or r.get("slug") or r.get("path") or "" for r in merged]
        correlation_id = _mt.emit_read(
            phase="unknown",  # facade has no phase context; callers may emit follow-up rows
            reader="memory_facade.recall",
            query=query,
            memory_ids_seen=[s for s in seen_ids if s],
            effect=None,  # consumer reports effect via emit_effect(correlation_id, ...)
            reason="",
        )
    except Exception as exc:  # noqa: BLE001 — fire-and-forget per protocol
        print(f"WARN: memory_telemetry emit_read failed: {exc}", file=sys.stderr)

    return {
        "query": query,
        "kind_filter": kind,
        "project": project,
        "results_by_kind": results,
        "merged": merged[: limit * len(KINDS)],
        "reasons": reasons,
        "telemetry_correlation_id": correlation_id,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="")
    parser.add_argument("--kind", choices=list(KINDS), default=None)
    parser.add_argument("--project", default=None)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--workdir", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument(
        "--skip-postgres",
        action="store_true",
        help="Skip the Postgres semantic backend entirely (no env-var read, no connect attempt). "
             "Use when state.json.architecture.backendHealth.semantic.ok is false.",
    )
    args = parser.parse_args(argv)

    env = recall(
        query=args.query,
        kind=args.kind,
        project=args.project,
        limit=args.limit,
        workdir=Path(args.workdir).resolve(),
        skip_postgres=args.skip_postgres,
    )
    json.dump(env, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
