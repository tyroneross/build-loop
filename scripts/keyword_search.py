#!/usr/bin/env python3
"""Sparse-retrieval (tsvector + ts_rank) leg for hybrid recall.

Phase A chunk 2. Mirrors Example App's `makeTsvectorKeywordSearch`
(`example-app/lib/search/retrieval/pipeline-rag.ts:136-188`) over
build-loop's semantic_facts table.

The `search_vector` GENERATED column added by `migrate_add_fts_column.py`
contains `to_tsvector('english', subject||predicate||object)` and is
GIN-indexed. Queries use `websearch_to_tsquery('english', $1)` so users
can write natural phrases (`package-level dead detection`) without
needing to know `&`/`|` tsquery syntax.

The function signature mirrors `recall.hybrid_search_facts` for drop-in
fan-out from a future `--mode hybrid` orchestrator, but the leg itself
is independent — no embedding required, no dimension constraint, no
silent failures when the embedder is down. That makes it the resilience
backstop too: `--mode sparse_only` works with just Postgres.

Same metadata-filter semantics as `hybrid_search_facts`: typed-column
OR JSONB-fallback, allowlisted field names.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from db import query  # type: ignore  # noqa: E402

SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_ALLOWED_FILTER_FIELDS = frozenset({
    "project", "tool", "model", "task_category", "author",
    "domain", "goal", "confidence_source",
})


def _safe_schema(s: str) -> str:
    if not SCHEMA_RE.match(s):
        raise ValueError(f"unsafe schema name: {s!r}")
    return s


def keyword_search_facts(
    q: str,
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
    domain: str | None = None,
    goal: str | None = None,
    confidence_source: str | None = None,
) -> list[dict[str, Any]]:
    """Sparse retrieval over `semantic_facts.search_vector`.

    Returns rows ordered by `ts_rank(search_vector, websearch_to_tsquery(q))`
    descending, sliced to `limit`. Status='active' and confidence>=floor
    apply, identical to the dense leg.

    Score contract: `score` field carries the raw `ts_rank` value
    (typically 0.0-0.3). RRF fusion uses rank position, not absolute
    score, so the magnitude difference between this leg and the cosine
    leg is intentionally not normalized here.
    """
    if not q or not q.strip():
        return []
    schema = _safe_schema(schema)

    where_clauses = [
        "status = 'active'",
        "confidence >= %s",
        "search_vector @@ websearch_to_tsquery('english', %s)",
    ]
    params: list[Any] = [confidence_floor, q]

    def _check_field(field: str) -> None:
        if field not in _ALLOWED_FILTER_FIELDS:
            raise ValueError(f"unsafe filter field: {field!r}")

    def _add_meta_filter(field: str, value: str | None) -> None:
        if value is None:
            return
        _check_field(field)
        where_clauses.append(
            f"(COALESCE({field}, metadata->>'{field}') = %s)"
        )
        params.append(value)

    def _add_meta_in_filter(field: str, values: list[str] | None) -> None:
        if not values:
            return
        _check_field(field)
        where_clauses.append(
            f"(COALESCE({field}, metadata->>'{field}') = ANY(%s))"
        )
        params.append(list(values))

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
        "    ts_rank(search_vector, websearch_to_tsquery('english', %s)) AS score "
        f"FROM {schema}.semantic_facts "
        f"WHERE {where_sql} "
        "ORDER BY score DESC, valid_from DESC NULLS LAST "
        "LIMIT %s"
    )
    # ts_rank in SELECT references q again, then LIMIT.
    params = [q] + params + [int(limit)]
    return query(sql, tuple(params))


def has_search_vector_column(schema: str) -> bool:
    """Cheap precondition check — caller can use this to decide whether
    to fall back to pg_trgm semantics when the migration hasn't been
    applied yet.
    """
    schema = _safe_schema(schema)
    rows = query(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = 'semantic_facts' "
        "  AND column_name = 'search_vector'",
        (schema,),
    )
    return bool(rows)
