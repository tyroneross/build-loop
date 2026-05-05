#!/usr/bin/env python3
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
                        BUILD_LOOP_DATABASE_URL is unset, psycopg is missing,
                        or the connection fails. Never raises.
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
DEFAULT_LIMIT = 10
KINDS = ("runs", "decisions", "semantic", "debugger")


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


def read_decisions(workdir: Path, query: str, limit: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    dec_dir = workdir / ".episodic" / "decisions"
    reasons: List[str] = []
    if not dec_dir.is_dir():
        return [], reasons
    out: List[Dict[str, Any]] = []
    for p in sorted(dec_dir.glob("*.md")):
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
        # Derive a brief summary: first non-frontmatter, non-empty body line.
        body = text[m.end():] if m else text
        summary_lines = [
            ln.strip() for ln in body.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        summary = summary_lines[0][:240] if summary_lines else ""
        out.append({
            "_kind": "decisions",
            "_recency_ts": _parse_iso(ts_raw),
            "id": p.stem,
            "title": title,
            "primary_tag": primary_tag,
            "path": str(p.relative_to(workdir)),
            "summary": summary,
        })
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
) -> Tuple[List[Dict[str, Any]], List[str]]:
    reasons: List[str] = []
    db_url = os.environ.get("BUILD_LOOP_DATABASE_URL", "").strip()
    if not db_url:
        reasons.append("db_unavailable: BUILD_LOOP_DATABASE_URL unset")
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
) -> Dict[str, Any]:
    """Unified read across the four memory backends. See module docstring."""
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
    if kind in (None, "semantic"):
        results["semantic"], r = read_semantic(workdir, query, limit, project)
        reasons.extend(r)
    if kind in (None, "debugger"):
        results["debugger"], r = read_debugger(workdir, query, limit, project)
        reasons.extend(r)

    # Merge: sort by recency desc, falling back to stable per-kind order.
    merged: List[Dict[str, Any]] = []
    for k in KINDS:
        merged.extend(results[k])
    merged.sort(key=lambda x: (x.get("_recency_ts") or 0), reverse=True)

    return {
        "query": query,
        "kind_filter": kind,
        "project": project,
        "results_by_kind": results,
        "merged": merged[: limit * len(KINDS)],
        "reasons": reasons,
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
    args = parser.parse_args(argv)

    env = recall(
        query=args.query,
        kind=args.kind,
        project=args.project,
        limit=args.limit,
        workdir=Path(args.workdir).resolve(),
    )
    json.dump(env, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
