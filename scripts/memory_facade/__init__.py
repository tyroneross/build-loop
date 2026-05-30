#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Unified read facade over build-loop's memory surfaces.

Phase 6 Learn must see signals from all stores. Today's reality:
  1. .build-loop/state.json.runs[]                — local file
  2. build-loop-memory indexes/project folders    — canonical files
  3. agent_memory.<schema>.semantic_facts         — Postgres
  4. claude-code-debugger MCP `search` tool       — MCP server

Four read paths, four discovery costs. This module collapses them behind one
function:

    recall(query, kind=None, project=None, limit=10) -> RecallEnvelope

`kind` filters by store name: "runs" | "decisions" | "lessons" | "semantic" |
"debugger" (or None for all). `project` filters semantic_facts by project
label. `limit` is per-store cap (the merged result returns up to
`5 * limit`).

Each backend degrades gracefully:
  - state.json runs   → returns [] silently if file missing.
  - canonical files   → returns [] silently if dir/index missing or empty.
  - Postgres          → returns [] AND records reason="db_unavailable" when
                        no DB URL is configured, psycopg is missing, or the
                        connection fails. Never raises.
  - debugger MCP      → returns [] AND records reason="mcp_unavailable" when
                        the MCP server is not running.

Public API (frozen — all consumers import these directly):
  recall, read_runs, read_lessons, read_decisions, read_semantic, read_debugger,
  set_debugger_runner, main, KINDS, KIND_ALIASES, DEFAULT_LIMIT,
  DECISION_FRONTMATTER_RE, _parse_iso, _q_match, _read_jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup — ensure scripts/ is on sys.path so helpers like _db_url,
# _paths, project_resolver are importable by sub-modules.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent        # scripts/memory_facade/
_SCRIPTS_DIR = _HERE.parent                    # scripts/
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

REPO_ROOT_DEFAULT = _SCRIPTS_DIR.parent

# ---------------------------------------------------------------------------
# Re-export from sub-modules — public API is FROZEN; all imports keep working.
# ---------------------------------------------------------------------------
from .common import (  # noqa: E402
    DECISION_FRONTMATTER_RE,
    _LESSON_FRONTMATTER_RE,
    _parse_iso,
    _q_match,
    _read_jsonl,
)
from .runs import read_runs  # noqa: E402
from .lessons import (  # noqa: E402
    _resolve_memory_dirs,
    read_lessons,
)
from .decisions import (  # noqa: E402
    _indexed_decisions,
    _resolve_decision_dirs,
    read_decisions,
)
from .semantic import read_semantic  # noqa: E402
from .debugger import read_debugger_impl  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_LIMIT = 10
KINDS = ("runs", "decisions", "lessons", "semantic", "debugger")
KIND_ALIASES = {
    "decision": "decisions",
    "lesson": "lessons",
    "semantic_facts": "semantic",
    "debug": "debugger",
}

# ---------------------------------------------------------------------------
# Debugger test-injection seam.
# Lives here (on the facade) so tests can ``monkeypatch.setattr(mf, ...)``
# against this module, then call ``mf.read_debugger(...)`` and see the same
# state without any circular import.
# ---------------------------------------------------------------------------
_DEBUGGER_RUNNER_OVERRIDE: Optional[Any] = None


def set_debugger_runner(fn: Optional[Any]) -> None:
    """Inject a callable used by `read_debugger` instead of the npx CLI.

    Tests pass ``lambda query, limit, project: '{"incidents":[...]}'``.
    """
    global _DEBUGGER_RUNNER_OVERRIDE
    _DEBUGGER_RUNNER_OVERRIDE = fn


def read_debugger(
    workdir: Path,
    query: str,
    limit: int,
    project: Optional[str],
) -> tuple[List[Dict[str, Any]], List[str]]:
    """Best-effort MCP read.

    The MCP server is bundled at ``dist/src/mcp/server.js`` (relative to the
    plugin root). We do NOT spawn the server from here — that is the
    orchestrator's job. Instead we attempt to invoke the CLI mode of the same
    package if it's installed; otherwise we return an empty list with a
    ``mcp_unavailable`` reason. Tests inject a mock at
    ``_DEBUGGER_RUNNER_OVERRIDE``.
    """
    return read_debugger_impl(
        workdir=workdir,
        query=query,
        limit=limit,
        project=project,
        runner=_DEBUGGER_RUNNER_OVERRIDE,
    )


# ---------------------------------------------------------------------------
# Top-level: recall()
# ---------------------------------------------------------------------------

def _emit_telemetry(merged: List[Dict[str, Any]], query: str) -> Optional[str]:
    """Fire-and-forget telemetry emit.  Returns correlation_id or None."""
    try:
        try:
            from scripts import memory_telemetry as _mt  # type: ignore  # noqa: PLC0415
        except ImportError:
            import memory_telemetry as _mt  # type: ignore  # noqa: PLC0415
        seen_ids = [r.get("id") or r.get("slug") or r.get("path") or "" for r in merged]
        return _mt.emit_read(
            phase="unknown",
            reader="memory_facade.recall",
            query=query,
            memory_ids_seen=[s for s in seen_ids if s],
            effect=None,
            reason="",
        )
    except Exception as exc:  # noqa: BLE001 — fire-and-forget per protocol
        print(f"WARN: memory_telemetry emit_read failed: {exc}", file=sys.stderr)
        return None


def _fan_out(
    workdir: Path,
    query: str,
    limit: int,
    kind: Optional[str],
    project: Optional[str],
    skip_postgres: bool,
) -> tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    """Invoke each backend if its kind is requested; collect results + reasons."""
    _backends = {
        "runs":      lambda: read_runs(workdir, query, limit),
        "decisions": lambda: read_decisions(workdir, query, limit),
        "lessons":   lambda: read_lessons(workdir, query, limit),
        "semantic":  lambda: read_semantic(workdir, query, limit, project, skip_postgres=skip_postgres),
        "debugger":  lambda: read_debugger(workdir, query, limit, project),
    }
    results: Dict[str, List[Dict[str, Any]]] = {k: [] for k in KINDS}
    reasons: List[str] = []
    for k in KINDS:
        if kind not in (None, k):
            continue
        results[k], r = _backends[k]()
        reasons.extend(r)
    return results, reasons


def recall(
    query: str = "",
    kind: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    workdir: Optional[Path] = None,
    skip_postgres: bool = False,
) -> Dict[str, Any]:
    """Unified read across the four memory backends. See module docstring.

    ``skip_postgres=True`` (Priority 21): the Postgres-backed semantic backend
    is bypassed entirely. Used by Phase 5 Iterate's Backend Short-circuit
    step when ``state.json.architecture.backendHealth.semantic.ok == false``.
    The ``reasons[]`` envelope marks the skip as ``skipped_postgres`` (distinct
    from ``db_unavailable: ...``) so consumers can tell intentional skip
    from genuine backend-down.
    """
    if kind is not None and kind not in KINDS:
        raise ValueError(f"invalid kind {kind!r}; expected one of {KINDS}")
    workdir = (workdir or Path.cwd()).resolve()
    results, reasons = _fan_out(workdir, query, limit, kind, project, skip_postgres)

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
        "telemetry_correlation_id": _emit_telemetry(merged, query),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    # Back-compat: ``python3 scripts/memory_facade.py recall --query "..."``
    if argv_list and argv_list[0] == "recall":
        argv_list.pop(0)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="")
    parser.add_argument("--kind", choices=list(KINDS) + sorted(KIND_ALIASES), default=None)
    parser.add_argument("--project", default=None)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--workdir", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument(
        "--skip-postgres",
        action="store_true",
        help="Skip the Postgres semantic backend entirely. "
             "Use when state.json.architecture.backendHealth.semantic.ok is false.",
    )
    args = parser.parse_args(argv_list)
    kind = KIND_ALIASES.get(args.kind, args.kind)

    env = recall(
        query=args.query,
        kind=kind,
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
