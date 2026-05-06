#!/usr/bin/env python3
"""Hybrid retrieval entry point for repo-local episodic memory.

Embeds the query via `embed_backend.embed` (MLX `mxbai-embed-large-v1`
default, Ollama `bge-m3` fallback after Phase A — both 1024-dim but in
DIFFERENT vector spaces) and runs a hybrid search against
`agent_memory.<schema>.semantic_facts` and `episode_events`.

Phase A pipeline (--mode hybrid, default):
  1. vector leg — pgvector cosine over `embedding` (HNSW index)
  2. sparse leg — tsvector + ts_rank over `search_vector` GENERATED column
  3. graph leg (Phase B) — BFS depth-2 from top-K vector+sparse seeds
     over `recall_graph.get_cached_graph()` (markdown wikilinks +
     path mentions + cross-decision citations); empty when no edges
     or `--no-graph` set
  4. RRF fusion (k=60, Cormack/Clarke/Buettcher 2009; up to 3 input legs)
  5. cross-encoder rerank (BAAI/bge-reranker-v2-m3 via sentence-transformers,
     MPS on Apple Silicon, CPU fallback) — optional dep [retrieval]
  6. quality + recency multipliers (lifted from vault_vector.py); the
     `ppr_norm` weight is now populated from `recall_graph.pagerank_prior`
  7. trim to limit

Legacy modes:
  --mode vector_only — today's pure-cosine behavior (REGRESSION BASELINE)
  --mode sparse_only — tsvector only (useful when Ollama is down)

Results are scored, deduped, and a small text summary is written to
stdout (target ~500-1500 tokens; truncated if longer).

Contract:
  stdout      -> compact text summary (top-K relevant facts)
  stderr      -> log lines
  exit 0      -> success
  exit 1      -> validation error
  exit 2      -> filesystem / DB error

Phase 1 Assess can call this script instead of reading INDEX.md
wholesale. See `skills/knowledge/references/recall-integration.md`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _paths import default_schema as _default_schema  # type: ignore  # noqa: E402
from db import query, vector_literal  # type: ignore  # noqa: E402
from embed_backend import embed as _embed  # type: ignore  # noqa: E402
from project_resolver import resolve_project  # type: ignore  # noqa: E402
from write_decision import (  # type: ignore  # noqa: E402
    CONFIDENCE_ORDER,
    log,
)

# Module-level constant kept for back-compat with tests that import it.
# Resolved at import time from $AGENT_MEMORY_SCHEMA, default
# 'personal_memory'. The CLI re-resolves at parse time so a test
# manipulating the env after import still gets the right default.
DEFAULT_SCHEMA = _default_schema()
DEFAULT_LIMIT = 5
DEFAULT_NEIGHBOR_WINDOW = 3
DEFAULT_FLOOR = "confirmed"
DEFAULT_MODE = "hybrid"
VALID_MODES = ("hybrid", "vector_only", "sparse_only")
PER_LEG_OVERFETCH = 21  # Atomize HYBRID_VECTOR_LIMIT — see research entry


def confidence_to_float(c: str) -> float:
    return {"assumed": 0.25, "inferred": 0.5, "confirmed": 0.75, "explicit": 1.0}[c]


def _safe_schema(schema: str) -> str:
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        raise ValueError(f"unsafe schema name: {schema!r}")
    return schema


def hybrid_search_facts(
    q: str,
    embedding: list[float],
    schema: str,
    limit: int,
    confidence_floor: float,
    *,
    project: str | None = None,
    projects: list[str] | None = None,
    tool: str | None = None,
    model: str | None = None,
    task_category: str | None = None,
    author: str | None = None,
    # v3 filters (design §16) — applied BEFORE cosine/BM25 ranking, same
    # typed-column-or-JSONB-fallback pattern as v2.
    domain: str | None = None,
    goal: str | None = None,
    confidence_source: str | None = None,
) -> list[dict[str, Any]]:
    """Run hybrid search over semantic_facts.

    Score = 0.6 * cosine_sim + 0.4 * trigram_sim
    Filters by status='active' and confidence >= floor.

    Metadata filters (design §15) apply BEFORE the cosine/BM25 ranking.
    Each filter prefers the typed column but falls back to JSONB metadata
    so retrieval still works if the v2 migration hasn't been applied yet.
    """
    schema = _safe_schema(schema)
    emb = vector_literal(embedding)
    where_clauses = [
        "status = 'active'",
        "embedding IS NOT NULL",
        "confidence >= %s",
    ]
    params: list[Any] = [emb, q, q, q, emb, q, q, q, confidence_floor]

    # Allowlist of fields that may be interpolated into the WHERE clause.
    # Any caller passing a `field` outside this set will raise ValueError.
    # This converts the implicit safety assumption (only called with
    # literals) into an enforced contract — defense against future
    # callers that derive `field` from external input.
    _ALLOWED_FILTER_FIELDS = frozenset({
        "project", "tool", "model", "task_category", "author",
        "domain", "goal", "confidence_source",
    })

    def _check_field(field: str) -> None:
        if field not in _ALLOWED_FILTER_FIELDS:
            raise ValueError(f"unsafe filter field: {field!r}")

    def _add_meta_filter(field: str, value: str | None) -> None:
        if value is None:
            return
        _check_field(field)
        # Typed column OR JSONB fallback.
        where_clauses.append(
            f"(COALESCE({field}, metadata->>'{field}') = %s)"
        )
        params.append(value)

    def _add_meta_in_filter(field: str, values: list[str] | None) -> None:
        """Multi-value IN filter for the project default-scoping case.

        Uses ANY(%s) so we can pass a Python list as a single param. The
        COALESCE keeps the v2-typed-column-or-JSONB-fallback pattern.
        """
        if not values:
            return
        _check_field(field)
        where_clauses.append(
            f"(COALESCE({field}, metadata->>'{field}') = ANY(%s))"
        )
        params.append(list(values))

    # `projects` (list) wins over single `project` if both are passed.
    if projects:
        _add_meta_in_filter("project", projects)
    else:
        _add_meta_filter("project", project)
    _add_meta_filter("tool", tool)
    _add_meta_filter("model", model)
    _add_meta_filter("task_category", task_category)
    _add_meta_filter("author", author)
    _add_meta_filter("domain", domain)
    _add_meta_filter("goal", goal)
    _add_meta_filter("confidence_source", confidence_source)

    where_sql = " AND ".join(where_clauses)
    sql = (
        "SELECT "
        "    id::text AS id, "
        "    subject, predicate, object, confidence, status, metadata, valid_from, "
        "    (1 - (embedding <=> %s::vector)) AS cosine_sim, "
        "    GREATEST(similarity(subject, %s), similarity(predicate, %s), similarity(object, %s)) AS trgm_sim, "
        "    (0.6 * (1 - (embedding <=> %s::vector)) "
        "        + 0.4 * GREATEST(similarity(subject, %s), similarity(predicate, %s), similarity(object, %s))) AS score "
        f"FROM {schema}.semantic_facts "
        f"WHERE {where_sql} "
        "ORDER BY score DESC "
        "LIMIT %s"
    )
    params.append(int(limit))
    return query(sql, tuple(params))


def bump_last_accessed(schema: str, fact_ids: list[str]) -> None:
    """Update `last_accessed = now()` on the semantic_facts rows that ranked top-K.

    Best-effort. Failures log and continue — recall must succeed even
    when the column is absent (pre-migration installs).
    """
    if not fact_ids:
        return
    schema = _safe_schema(schema)
    try:
        from db import execute  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return
    try:
        execute(
            f"UPDATE {schema}.semantic_facts SET last_accessed = now() WHERE id::text = ANY(%s)",
            (list(fact_ids),),
        )
    except Exception as e:  # noqa: BLE001
        log(f"recall: bump_last_accessed failed (continuing): {e}")


def hybrid_search_episodes(
    q: str,
    embedding: list[float],
    schema: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Episode-events hybrid search: cosine + ts_rank."""
    schema = _safe_schema(schema)
    emb = vector_literal(embedding)
    sql = (
        "SELECT "
        "    id::text AS id, "
        "    session_id::text AS session_id, "
        "    seq_num, occurred_at, actor, verb, object, raw_content, "
        "    (1 - (embedding <=> %s::vector)) AS cosine_sim, "
        "    ts_rank(to_tsvector('english', coalesce(raw_content,'')), "
        "            plainto_tsquery('english', %s)) AS ts_rank_score "
        f"FROM {schema}.episode_events "
        "WHERE embedding IS NOT NULL "
        "ORDER BY (1 - (embedding <=> %s::vector)) DESC "
        "LIMIT %s"
    )
    return query(sql, (emb, q, emb, int(limit)))


def neighbor_expand(
    schema: str,
    seed_ids: list[str],
    window: int,
) -> list[dict[str, Any]]:
    """For each episode id, return surrounding events in the same session
    (seq_num within +/- window). Deduped on id.
    """
    if not seed_ids:
        return []
    schema = _safe_schema(schema)
    sql = (
        "WITH seeds AS ( "
        "  SELECT id, session_id, seq_num "
        f"  FROM {schema}.episode_events "
        "  WHERE id::text = ANY(%s) "
        ") "
        "SELECT DISTINCT ON (e.id::text) "
        "  e.id::text AS id, e.session_id::text AS session_id, "
        "  e.seq_num, e.occurred_at, e.actor, e.verb, e.raw_content "
        f"FROM {schema}.episode_events e "
        "JOIN seeds s ON e.session_id = s.session_id "
        "            AND abs(e.seq_num - s.seq_num) <= %s "
        "ORDER BY e.id::text, e.session_id, e.seq_num"
    )
    return query(sql, (list(seed_ids), int(window)))


def render(
    query: str,
    facts: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    expanded: list[dict[str, Any]],
    confidence_floor: str,
    char_budget: int,
) -> str:
    out: list[str] = []
    out.append(f"# Recall — query: {query!r}")
    out.append(f"_confidence floor: {confidence_floor}; semantic facts={len(facts)}; episodes={len(episodes)}_")
    out.append("")
    if facts:
        out.append("## Top semantic facts")
        for f in facts:
            md = f.get("metadata") or {}
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except Exception:  # noqa: BLE001
                    md = {}
            score = f.get("score")
            label = f"[{f.get('subject')} | {f.get('predicate')}]"
            obj = (f.get("object") or "").replace("\n", " ")
            confidence_label = (
                f"conf={f.get('confidence', '?')}"
                + (f", confidence_label={md.get('confidence')}" if md.get("confidence") else "")
            )
            tags = md.get("tags") or []
            entity = md.get("entity")
            line = (
                f"- score={score:.3f} {label}\n"
                f"  object: {obj}\n"
                f"  {confidence_label}, entity={entity}, tags={tags}\n"
            )
            out.append(line)
    if episodes:
        out.append("")
        out.append("## Top episode events")
        for e in episodes:
            content = (e.get("raw_content") or "").replace("\n", " ")
            score = e.get("cosine_sim")
            out.append(f"- score={score:.3f} actor={e.get('actor')} verb={e.get('verb')} object={e.get('object')}")
            out.append(f"  excerpt: {content[:200]}")
        if expanded:
            out.append("")
            out.append("## Neighbor-expanded context")
            for e in expanded[:20]:
                content = (e.get("raw_content") or "").replace("\n", " ")
                out.append(f"- session={e.get('session_id')[:8]} seq={e.get('seq_num')} {e.get('actor')}: {content[:120]}")
    text = "\n".join(out)
    if len(text) > char_budget:
        text = text[: char_budget - 50] + "\n\n[truncated to char budget]"
    return text


def run_search(
    query: str,
    schema: str,
    limit: int,
    confidence_floor: float,
    *,
    mode: str = DEFAULT_MODE,
    project: str | None = None,
    projects: list[str] | None = None,
    tool: str | None = None,
    model: str | None = None,
    task_category: str | None = None,
    author: str | None = None,
    domain: str | None = None,
    goal: str | None = None,
    confidence_source: str | None = None,
    rerank_disabled: bool = False,
    graph_disabled: bool = False,
    decisions_dir: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute the search pipeline for the requested mode.

    Returns (facts, leg_stats). leg_stats carries per-leg timings and
    counts for --stats observability and is shaped:
        {
          "mode": str,
          "vector_count": int | None,   # None = leg not run
          "sparse_count": int | None,
          "fused_count":  int | None,
          "reranked_count": int | None,
          "embed_ms": int,              # 0 if no embed run
          "vector_ms": int,
          "sparse_ms": int,
          "rrf_ms":    int,
          "rerank_ms": int,
          "multiplier_ms": int,
        }

    Modes:
      vector_only  - REGRESSION BASELINE. Today's hybrid_search_facts
                     (cosine 0.6 + pg_trgm 0.4). No FTS, no rerank, no
                     multipliers. Byte-identical to pre-Phase-A behavior.
      sparse_only  - tsvector + ts_rank only. No embed required —
                     usable when Ollama is down. No rerank.
      hybrid       - Full pipeline: vector + sparse + RRF fuse +
                     cross-encoder rerank + quality/recency multipliers.
    """
    import time as _time
    if mode not in VALID_MODES:
        raise ValueError(f"unknown --mode {mode!r}; valid: {VALID_MODES}")

    leg_stats: dict[str, Any] = {
        "mode": mode,
        "vector_count": None,
        "sparse_count": None,
        "graph_count": None,
        "fused_count": None,
        "reranked_count": None,
        "embed_ms": 0,
        "vector_ms": 0,
        "sparse_ms": 0,
        "graph_ms": 0,
        "rrf_ms": 0,
        "rerank_ms": 0,
        "multiplier_ms": 0,
    }

    # ---------------- vector_only: legacy path, byte-identical contract.
    if mode == "vector_only":
        t0 = _time.monotonic()
        embedding = _embed(query)
        leg_stats["embed_ms"] = int((_time.monotonic() - t0) * 1000)

        t0 = _time.monotonic()
        facts = hybrid_search_facts(
            query, embedding, schema, limit, confidence_floor,
            project=project, projects=projects, tool=tool, model=model,
            task_category=task_category, author=author,
            domain=domain, goal=goal, confidence_source=confidence_source,
        )
        leg_stats["vector_ms"] = int((_time.monotonic() - t0) * 1000)
        leg_stats["vector_count"] = len(facts)
        return facts, leg_stats

    # ---------------- sparse_only: no embed required.
    if mode == "sparse_only":
        from keyword_search import keyword_search_facts  # type: ignore  # noqa: PLC0415

        t0 = _time.monotonic()
        facts = keyword_search_facts(
            query, schema, limit, confidence_floor,
            project=project, projects=projects, tool=tool, model=model,
            task_category=task_category, author=author,
            domain=domain, goal=goal, confidence_source=confidence_source,
        )
        leg_stats["sparse_ms"] = int((_time.monotonic() - t0) * 1000)
        leg_stats["sparse_count"] = len(facts)
        return facts, leg_stats

    # ---------------- hybrid: full pipeline.
    from keyword_search import keyword_search_facts  # type: ignore  # noqa: PLC0415
    from rrf import rrf_fuse  # type: ignore  # noqa: PLC0415
    from rerank import rerank as cross_rerank  # type: ignore  # noqa: PLC0415
    from recall_multipliers import apply_multipliers  # type: ignore  # noqa: PLC0415

    # Vector leg (cosine over embedding column).
    t0 = _time.monotonic()
    embedding = _embed(query)
    leg_stats["embed_ms"] = int((_time.monotonic() - t0) * 1000)

    t0 = _time.monotonic()
    vector_hits = hybrid_search_facts(
        query, embedding, schema, PER_LEG_OVERFETCH, confidence_floor,
        project=project, projects=projects, tool=tool, model=model,
        task_category=task_category, author=author,
        domain=domain, goal=goal, confidence_source=confidence_source,
    )
    leg_stats["vector_ms"] = int((_time.monotonic() - t0) * 1000)
    leg_stats["vector_count"] = len(vector_hits)

    # Sparse leg (tsvector + ts_rank). Tolerate missing column —
    # falls back to empty list so the hybrid pipeline still produces
    # results even if migrate_add_fts_column hasn't been applied.
    t0 = _time.monotonic()
    try:
        sparse_hits = keyword_search_facts(
            query, schema, PER_LEG_OVERFETCH, confidence_floor,
            project=project, projects=projects, tool=tool, model=model,
            task_category=task_category, author=author,
            domain=domain, goal=goal, confidence_source=confidence_source,
        )
    except Exception as e:  # noqa: BLE001
        log(f"recall: sparse leg failed ({e}); continuing vector-only this query")
        sparse_hits = []
    leg_stats["sparse_ms"] = int((_time.monotonic() - t0) * 1000)
    leg_stats["sparse_count"] = len(sparse_hits)

    # Graph leg (Phase B). Seeds from the top-K of vector + sparse so
    # we walk *from* the most query-relevant nodes rather than the
    # whole graph. Empty when no edges exist or --no-graph is set.
    graph_hits: list[dict[str, Any]] = []
    ppr_for_multipliers: dict[str, float] = {}
    if not graph_disabled:
        try:
            from recall_graph import (  # type: ignore  # noqa: PLC0415
                get_cached_graph,
                graph_walk_leg,
            )
            ddir = Path(decisions_dir) if decisions_dir else Path(".episodic/decisions")
            t0 = _time.monotonic()
            g, ppr = get_cached_graph(ddir)
            # Seeds: dedup ids from the top of vector + sparse legs.
            seeds_seen: set[str] = set()
            seed_ids: list[str] = []
            for row in (vector_hits[:5] + sparse_hits[:5]):
                rid = str(row.get("id") or "")
                if not rid or rid in seeds_seen:
                    continue
                seeds_seen.add(rid)
                seed_ids.append(rid)
            graph_hits = graph_walk_leg(seed_ids, g, ppr=ppr)
            leg_stats["graph_ms"] = int((_time.monotonic() - t0) * 1000)
            leg_stats["graph_count"] = len(graph_hits)
            ppr_for_multipliers = ppr
        except Exception as e:  # noqa: BLE001
            log(f"recall: graph leg failed ({e}); continuing without graph")
            graph_hits = []
            leg_stats["graph_count"] = 0
    else:
        leg_stats["graph_count"] = 0

    # RRF fuse — three legs when graph hits exist, two otherwise.
    t0 = _time.monotonic()
    legs_for_rrf: list[list[dict[str, Any]]] = [vector_hits, sparse_hits]
    if graph_hits:
        legs_for_rrf.append(graph_hits)
    fused = rrf_fuse(legs_for_rrf, k=60, limit=limit * 2)
    leg_stats["rrf_ms"] = int((_time.monotonic() - t0) * 1000)
    leg_stats["fused_count"] = len(fused)

    # Cross-encoder rerank (no-op when sentence-transformers absent or
    # rerank explicitly disabled). Pool size capped inside rerank.py.
    if rerank_disabled:
        reranked = fused[:limit]
    else:
        t0 = _time.monotonic()
        reranked = cross_rerank(query, fused, top_k=limit)
        leg_stats["rerank_ms"] = int((_time.monotonic() - t0) * 1000)
    leg_stats["reranked_count"] = len(reranked)

    # Quality + recency multipliers, with the activated PPR weight.
    t0 = _time.monotonic()
    final = apply_multipliers(
        reranked,
        query,
        ppr=ppr_for_multipliers or None,
    )[:limit]
    leg_stats["multiplier_ms"] = int((_time.monotonic() - t0) * 1000)

    return final, leg_stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Hybrid recall over agent_memory")
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p.add_argument(
        "--mode",
        default=DEFAULT_MODE,
        choices=VALID_MODES,
        help=(
            "Search pipeline: hybrid (default; vector+sparse+RRF+rerank+multipliers), "
            "vector_only (today's pre-Phase-A behavior; regression baseline), "
            "sparse_only (tsvector only; usable when Ollama is down)."
        ),
    )
    p.add_argument(
        "--no-rerank",
        action="store_true",
        help=(
            "In --mode hybrid, skip the cross-encoder rerank stage. "
            "Useful for A/B comparing fusion-only vs full pipeline, or "
            "when sentence-transformers is intentionally not installed."
        ),
    )
    p.add_argument(
        "--no-graph",
        action="store_true",
        help=(
            "In --mode hybrid, skip the Phase B knowledge-graph leg "
            "(BFS over markdown wikilinks + path mentions + decision "
            "citations). Useful for A/B safety even within hybrid mode."
        ),
    )
    p.add_argument(
        "--decisions-dir",
        default=None,
        help=(
            "Override the markdown decisions directory used by the "
            "Phase B graph leg. Defaults to '.episodic/decisions'."
        ),
    )
    p.add_argument(
        "--confidence-floor",
        default=DEFAULT_FLOOR,
        choices=sorted(CONFIDENCE_ORDER),
    )
    p.add_argument("--neighbor-window", type=int, default=DEFAULT_NEIGHBOR_WINDOW)
    p.add_argument(
        "--schema",
        default=None,
        help="Postgres schema. Default: $AGENT_MEMORY_SCHEMA or 'personal_memory'.",
    )
    p.add_argument(
        "--embed-model",
        default="bge-m3",
        help="Legacy flag; ignored. Backend chosen via $EMBED_BACKEND.",
    )
    p.add_argument("--char-budget", type=int, default=8000)  # ~1500 tokens
    p.add_argument("--no-episodes", action="store_true", help="Skip episode_events search")
    # v2 metadata filters (design §15). Applied BEFORE cosine/BM25 ranking.
    p.add_argument(
        "--project",
        default=None,
        help=(
            "Filter facts to this project. Repeat or comma-separate to pass "
            "multiple. Default (when omitted): the project tag for the current "
            "cwd (via projects.yaml) plus '_unscoped'. Use --all-projects to "
            "disable default scoping."
        ),
    )
    p.add_argument(
        "--all-projects",
        action="store_true",
        help="Disable default project scoping; return matches across every project tag.",
    )
    p.add_argument("--tool", default=None, help="Filter facts to this authoring tool")
    p.add_argument("--model", default=None, help="Filter facts to this model")
    p.add_argument("--task-category", default=None, help="Filter facts to this task category")
    p.add_argument("--author", default=None, help="Filter facts to this author")
    # v3 metadata filters (design §16). Applied BEFORE cosine/BM25 ranking.
    p.add_argument("--domain", default=None, help="Filter facts to this domain")
    p.add_argument("--goal", default=None, help="Filter facts to this goal")
    p.add_argument(
        "--confidence-source",
        default=None,
        help="Filter facts to this confidence_source (e.g. user_statement)",
    )
    p.add_argument("--no-bump-last-accessed", action="store_true", help="Skip the last_accessed bump on returned rows")
    p.add_argument(
        "--stats",
        action="store_true",
        help="Emit per-leg timing JSON to stderr (embed_ms, facts_ms, episodes_ms, neighbor_ms, total_ms, backend, dim)",
    )
    args = p.parse_args(argv)

    if args.schema is None:
        args.schema = _default_schema()

    # Resolve the effective project filter:
    #   --all-projects → no project filter
    #   --project foo[,bar] → those tags exactly
    #   neither → resolve_project(cwd) + '_unscoped' (default scoping)
    explicit_projects: list[str] | None = None
    single_project: str | None = None
    if args.all_projects:
        explicit_projects = None
        single_project = None
    elif args.project:
        parts = [p.strip() for p in args.project.split(",") if p.strip()]
        if len(parts) > 1:
            explicit_projects = parts
        elif len(parts) == 1:
            single_project = parts[0]
    else:
        cwd_project = resolve_project(Path.cwd())
        # Always include _unscoped so cross-project lessons surface in any
        # cwd. If the resolver itself returned _unscoped, dedupe.
        if cwd_project == "_unscoped":
            explicit_projects = ["_unscoped"]
        else:
            explicit_projects = [cwd_project, "_unscoped"]

    import time as _time
    timing: dict[str, Any] = {}
    started_total = _time.monotonic()

    floor = confidence_to_float(args.confidence_floor)

    try:
        facts, leg_stats = run_search(
            args.query,
            args.schema,
            args.limit,
            floor,
            mode=args.mode,
            project=single_project,
            projects=explicit_projects,
            tool=args.tool,
            model=args.model,
            task_category=args.task_category,
            author=args.author,
            domain=args.domain,
            goal=args.goal,
            confidence_source=args.confidence_source,
            rerank_disabled=args.no_rerank,
            graph_disabled=args.no_graph,
            decisions_dir=args.decisions_dir,
        )
    except RuntimeError as e:
        # Embed backend down — only fatal in vector_only / hybrid modes.
        log(f"recall: {e}")
        return 2
    except Exception as e:  # noqa: BLE001
        log(f"recall: {e}")
        return 2
    timing.update(leg_stats)

    # Episodes search uses the cosine path; only run when we have an
    # embedding (sparse_only mode skips episode events too — they have
    # no tsvector column today and re-adding one is Phase B work).
    episodes: list[dict[str, Any]] = []
    expanded: list[dict[str, Any]] = []
    if not args.no_episodes and args.mode != "sparse_only":
        try:
            t0 = _time.monotonic()
            embedding = _embed(args.query)  # cached by embed_backend after run_search
            episodes = hybrid_search_episodes(args.query, embedding, args.schema, args.limit)
            timing["episodes_ms"] = int((_time.monotonic() - t0) * 1000)
            if episodes:
                t0 = _time.monotonic()
                expanded = neighbor_expand(args.schema, [e["id"] for e in episodes], args.neighbor_window)
                timing["neighbor_ms"] = int((_time.monotonic() - t0) * 1000)
            else:
                timing["neighbor_ms"] = 0
        except Exception as e:  # noqa: BLE001
            log(f"recall: episodes leg failed ({e}); continuing")
            timing["episodes_ms"] = 0
            timing["neighbor_ms"] = 0
    else:
        timing["episodes_ms"] = 0
        timing["neighbor_ms"] = 0

    if not args.no_bump_last_accessed and facts:
        bump_last_accessed(args.schema, [f["id"] for f in facts])

    text = render(args.query, facts, episodes, expanded, args.confidence_floor, args.char_budget)
    print(text)

    if args.stats:
        timing["total_ms"] = int((_time.monotonic() - started_total) * 1000)
        try:
            from embed_backend import EMBED_DIM, active_backend, active_model  # type: ignore  # noqa: E402,PLC0415
            timing["embed_dim"] = EMBED_DIM
            timing["backend"] = active_backend()
            timing["embed_model"] = active_model()
        except Exception:  # noqa: BLE001
            timing.setdefault("backend", "unknown")
        timing["facts_count"] = len(facts)
        timing["episodes_count"] = len(episodes)
        print(f"[recall.stats] {json.dumps(timing)}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
