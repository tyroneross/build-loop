#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Unified read facade over build-loop's memory surfaces.

Phase 6 Learn must see signals from all stores. Today's reality:
  1. .build-loop/state.json.runs[]                — local file
  2. build-loop-memory indexes/project folders    — canonical files
  3. agent_memory.<schema>.semantic_facts         — Postgres
  4. .claude/memory/incidents/*.json             — native debugging incidents

Four read paths, four discovery costs. This module collapses them behind one
function:

    recall(query, kind=None, project=None, limit=10) -> RecallEnvelope

`kind` filters by store name: "runs" | "decisions" | "lessons" | "backlog" |
"semantic" | "debugger" (or None for all). `project` filters project stores
label. `limit` is per-store cap (the merged result returns up to
`len(KINDS) * limit`).

Each backend degrades gracefully:
  - state.json runs   → returns [] silently if file missing.
  - canonical files   → returns [] silently if dir/index missing or empty.
  - Postgres          → returns [] AND records reason="db_unavailable" when
                        no DB URL is configured, psycopg is missing, or the
                        connection fails. Never raises.
  - debugger incidents → returns [] AND records reason="debugger_unavailable"
                        when the structured incident store is absent.

Public API (frozen — all consumers import these directly):
  recall, read_runs, read_lessons, read_decisions, read_semantic, read_debugger,
  set_debugger_runner, main, KINDS, KIND_ALIASES, DEFAULT_LIMIT,
  DECISION_FRONTMATTER_RE, _parse_iso, _q_match, _read_jsonl
"""
from __future__ import annotations

import argparse
import json
import os
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
if __name__ == "__main__" and not __package__:
    __package__ = _HERE.name

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
from .backlog import read_backlog  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_LIMIT = 10
KINDS = ("runs", "decisions", "lessons", "backlog", "semantic", "debugger", "content")
KIND_ALIASES = {
    "decision": "decisions",
    "lesson": "lessons",
    "work": "backlog",
    "backlogs": "backlog",
    "semantic_facts": "semantic",
    "debug": "debugger",
    "body": "content",
    "fulltext": "content",
    "fts": "content",
    # Research/reference recall stays in the lessons lane; deferred work has a
    # dedicated backlog lane so derived INDEX files cannot hide it.
    "research": "lessons",
    "reference": "lessons",
}

# ---------------------------------------------------------------------------
# Debugger test-injection seam.
# Lives here (on the facade) so tests can ``monkeypatch.setattr(mf, ...)``
# against this module, then call ``mf.read_debugger(...)`` and see the same
# state without any circular import.
# ---------------------------------------------------------------------------
_DEBUGGER_RUNNER_OVERRIDE: Optional[Any] = None


def set_debugger_runner(fn: Optional[Any]) -> None:
    """Inject a callable used by `read_debugger` for structured incident payloads.

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
    """Best-effort native debugger incident read.

    By default this reads the same ``.claude/memory/incidents/*.json`` store
    used by Build Loop's native debugger writer. Tests inject a mock at
    ``_DEBUGGER_RUNNER_OVERRIDE`` for structured incident payloads.
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

def _emit_telemetry(merged: List[Dict[str, Any]], query: str,
                    project: Optional[str] = None,
                    workdir: Optional[Path] = None) -> Optional[str]:
    """Fire-and-forget telemetry emit.  Returns correlation_id or None.

    Emits `returned_paths` alongside `memory_ids_seen`. This is the join key,
    and its absence was the single reason usefulness could not be measured.

    A PostToolUse hook (`scripts/tool_trace.py`, registered in `hooks/hooks.json`
    with an empty matcher) already records every tool call as an OTel span
    carrying `session.id` and the file path. So the file-open half of the signal
    has existed all along. The read row carried memory IDS and no PATHS; the
    span carries PATHS and no ids. There was nothing to join on.

    `memory_locator.locate()` already passed `returned_paths`, but it emitted 70
    of 41,935 rows. This function emitted 39,987 and passed nothing, which is
    also why `phase` is `unknown` on 97% of the corpus.
    """
    try:
        try:
            from scripts import memory_telemetry as _mt  # type: ignore  # noqa: PLC0415
        except ImportError:
            import memory_telemetry as _mt  # type: ignore  # noqa: PLC0415
        seen_ids = [r.get("id") or r.get("slug") or r.get("path") or "" for r in merged]
        kept = [(i, r) for i, r in enumerate(merged)
                if (r.get("id") or r.get("slug") or r.get("path"))]
        # Exposure record. Position bias means a later use-signal ("this memory
        # was opened") is uninterpretable without knowing WHERE it was shown --
        # top-ranked items are examined more regardless of relevance, so naive
        # feedback builds a rich-get-richer loop. Propensity cannot be
        # reconstructed after the fact, so rank and score are captured here or
        # never. Both are free: the list is already ordered, and memory_rank
        # computes the score anyway.
        # Absolute paths, aligned with `kept`. An offline reconciler joins these
        # to tool-trace spans by (session, path, time-order) -- no agent
        # cooperation, no convention to decay.
        paths = [str(_r.get("path") or "") for _i, _r in kept]
        return _mt.emit_read(
            phase="unknown",
            reader="memory_facade.recall",
            query=query,
            returned_paths=[p for p in paths if p],
            memory_ids_seen=[s for s in seen_ids if s],
            ranks=[i for i, _r in kept],
            scores=[_r.get("_rank_score") for _i, _r in kept],
            shown_count=len(merged),
            effect=None,
            reason="",
        )
    except Exception as exc:  # noqa: BLE001 — fire-and-forget per protocol
        print(f"WARN: memory_telemetry emit_read failed: {exc}", file=sys.stderr)
        return None


def read_content(
    workdir: Path,
    query: str,
    limit: int,
    project: Optional[str],
) -> tuple[List[Dict[str, Any]], List[str]]:
    """Backend 7: FTS5 full-text index over document BODIES.

    Exists because every other file-backed backend searches a metadata surface
    (id / title / status / tags). Measured 2026-08-31: only 0.07% of
    body-relevant documents are findable from that surface, median 0.0000 --
    a structural recall ceiling no matching-semantics change can beat. The
    semantic backend that was meant to cover content has no database on disk
    and returns sqlite_semantic_empty on every query.

    Degrades to an empty result with a reason rather than raising; this is on
    the recall hot path and must never be able to break it.
    """
    if os.environ.get("BUILD_LOOP_MEMORY_CONTENT", "1") == "0":
        return [], ["content_index_disabled: BUILD_LOOP_MEMORY_CONTENT=0"]
    try:
        try:
            from scripts import content_index as _ci  # type: ignore  # noqa: PLC0415
        except ImportError:
            import content_index as _ci  # type: ignore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return [], [f"content_index_unavailable: {exc}"]
    try:
        db = _ci.default_db_path(workdir)
        if not Path(db).is_file():
            return [], [
                "content_index_absent: no FTS index on disk; build it with "
                "`python3 scripts/content_index.py build`"
            ]
        rows = _ci.query(query, limit=limit, project=project, db_path=db)
        return list(rows), ([] if rows else ["content_index_empty_result"])
    except Exception as exc:  # noqa: BLE001
        return [], [f"content_index_error: {exc}"]


def _order(merged: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """Order merged candidates by relevance, falling back to recency.

    This used to be ``merged.sort(key=_recency_ts, reverse=True)`` -- relevance
    was never computed. Measured against the live store on 2026-08-31 over six
    real runtime queries, the top result matched ZERO query terms in five of
    them while a better match sat in the same pool. Graded on 33 real runtime
    queries with an independent body-text oracle, relevance ordering lifted
    P@1 from 0.030 to 0.455 and MRR from 0.104 to 0.575 (threshold 0.5), and
    won at every threshold from 0.3 to 1.0.

    Ordering only -- never filters, so recall cannot drop. Set
    ``BUILD_LOOP_MEMORY_RANK=0`` to restore recency ordering.
    """
    if not merged:
        return merged
    if os.environ.get("BUILD_LOOP_MEMORY_RANK", "1") == "0":
        return sorted(merged, key=lambda x: (x.get("_recency_ts") or 0), reverse=True)
    try:
        try:
            from scripts import memory_rank as _mr  # type: ignore  # noqa: PLC0415
        except ImportError:
            import memory_rank as _mr  # type: ignore  # noqa: PLC0415
        return _mr.rank(merged, query)
    except Exception as exc:  # noqa: BLE001 — ordering must never break recall
        print(f"WARN: memory_rank unavailable, using recency order: {exc}",
              file=sys.stderr)
        return sorted(merged, key=lambda x: (x.get("_recency_ts") or 0), reverse=True)


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
        "backlog":   lambda: read_backlog(workdir, query, limit, project),
        "semantic":  lambda: read_semantic(workdir, query, limit, project, skip_postgres=skip_postgres),
        "debugger":  lambda: read_debugger(workdir, query, limit, project),
        "content":   lambda: read_content(workdir, query, limit, project),
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
    kind = KIND_ALIASES.get(kind, kind)
    if kind is not None and kind not in KINDS:
        raise ValueError(f"invalid kind {kind!r}; expected one of {KINDS}")
    workdir = (workdir or Path.cwd()).resolve()
    results, reasons = _fan_out(workdir, query, limit, kind, project, skip_postgres)

    merged: List[Dict[str, Any]] = []
    for k in KINDS:
        merged.extend(results[k])
    merged = _order(merged, query)

    return {
        "query": query,
        "kind_filter": kind,
        "project": project,
        "results_by_kind": results,
        # NOT truncated in practice, and deliberately kept: each backend already
        # caps its own return at `limit` and there are len(KINDS) backends, so
        # this bound always equals the maximum possible length. Verified at
        # limits 5/10/30 -> totals 14/24/64 against caps 35/70/210. It reads
        # like a second safety limit and is a no-op; it stays only as the
        # backstop for a future backend that ignores `limit`.
        "merged": merged[: limit * len(KINDS)],
        "reasons": reasons,
        "telemetry_correlation_id": _emit_telemetry(merged, query, project, workdir),
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
