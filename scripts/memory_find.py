#!/usr/bin/env python3
# capability:
#   purpose: One retrieval path for build-loop-memory — hybrid search, trust-labelled.
#   application: memory
#   status: experimental
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Hybrid retrieval over build-loop-memory: keyword + vector, fused, tier-labelled.

WHY THIS EXISTS (2026-09-16). Retrieval was fragmented: `INDEX.md` documented
`rg` (16.5s, unranked) and `blm context` (mixes trust levels silently), the
fast FTS index was undocumented, the Postgres semantic leg was unconfigured and
empty, and `_review/` quarantine captures were excluded from the database
entirely. Different agents found different subsets by accident: a Codex review
found the `rg`/`blm` paths from `INDEX.md`; a Claude session found the FTS index
by reading source. Neither was wrong, which is the problem. This module is the
ONE answer, so every agent gets the same result for the same question.

TWO LEGS, FUSED. Neither leg alone is good enough, measured on this store:
  - keyword only (FTS/tsvector) missed "how do we grade the classifier gold
    set" because the record is titled "use two judges from different vendors";
  - vector only missed "who gets named as a product maker" while nailing the
    lock-timeout question.
Reciprocal-rank fusion takes the union and ranks by agreement, so a record
needs to win on EITHER axis to surface. RRF is used rather than score blending
because the two legs' scores are not on a comparable scale (BM25-ish rank vs
cosine distance); rank is.

TRUST IS A LABEL, NOT A FILTER. Quarantined tier-3 captures are returned and
marked, never silently dropped and never presented as settled. `--tier curated`
narrows when the caller needs only confirmed decisions. Hiding them was the old
behaviour and it made 12,651 records unreachable rather than untrusted.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Rank-fusion constant. 60 is the value from the original RRF paper (Cormack
# et al. 2009); it damps the top-rank advantage enough that a strong hit on one
# leg cannot alone outrank agreement across both.
RRF_K = 60

TIER_CURATED = "curated"
TIER_QUARANTINED = "quarantined"


def _rrf(rank: int) -> float:
    return 1.0 / (RRF_K + rank)


def _row_tier(status: str | None, path: str | None) -> str:
    """Trust tier for a row. `status` is authoritative; path is the fallback.

    The path fallback matters for rows written before the status column carried
    a tier — without it those rows would silently read as curated.
    """
    if (status or "").lower() == TIER_QUARANTINED:
        return TIER_QUARANTINED
    if path and f"{os.sep}_review{os.sep}" in path:
        return TIER_QUARANTINED
    return TIER_CURATED


def _pg_search(query: str, project: str | None, limit: int, schema: str) -> tuple[list[dict], list[str]]:
    """Keyword + vector legs against Postgres. Returns ([], reasons) when unavailable."""
    reasons: list[str] = []
    try:
        import db  # type: ignore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return [], [f"pg_unavailable: {exc}"]
    if not db.resolve_db_url():
        return [], ["pg_unavailable: no DB URL configured"]

    import re as _re  # noqa: PLC0415

    if not _re.match(r"^[a-z][a-z0-9_]*$", schema):
        return [], [f"pg_unsafe_schema: {schema!r}"]

    where_project = "AND project = %(project)s" if project else ""
    fetch = max(limit * 4, 20)
    hits: dict[str, dict] = {}

    # Leg 1 — keyword. `search_vector` is a generated tsvector over
    # subject/predicate/object/chunk_context, so it covers the title AND the
    # stored excerpt. websearch_to_tsquery accepts plain human phrasing.
    try:
        rows = db.query(
            f"SELECT subject, object, status, metadata, "  # nosec: schema validated above
            f"       ts_rank(search_vector, websearch_to_tsquery('english', %(q)s)) AS score "
            f"FROM {schema}.semantic_facts "
            f"WHERE search_vector @@ websearch_to_tsquery('english', %(q)s) {where_project} "
            f"ORDER BY score DESC LIMIT %(n)s",
            {"q": query, "project": project, "n": fetch},
        )
        for rank, row in enumerate(rows, start=1):
            row = dict(row)
            key = str(row.get("subject"))
            entry = hits.setdefault(key, {"row": row, "rrf": 0.0, "legs": []})
            entry["rrf"] += _rrf(rank)
            entry["legs"].append(f"keyword#{rank}")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"keyword_leg_failed: {exc}")

    # Leg 2 — vector. Embedding the query costs ~40-130ms via the local daemon.
    try:
        from embed_backend import embed  # type: ignore  # noqa: PLC0415

        vec = embed(query)
        if isinstance(vec, list) and vec and isinstance(vec[0], list):
            vec = vec[0]
        literal = db.vector_literal(vec)
        rows = db.query(
            f"SELECT subject, object, status, metadata, "  # nosec: schema validated above
            f"       1 - (embedding <=> %(v)s::vector) AS score "
            f"FROM {schema}.semantic_facts "
            f"WHERE embedding IS NOT NULL {where_project} "
            f"ORDER BY embedding <=> %(v)s::vector LIMIT %(n)s",
            {"v": literal, "project": project, "n": fetch},
        )
        for rank, row in enumerate(rows, start=1):
            row = dict(row)
            key = str(row.get("subject"))
            entry = hits.setdefault(key, {"row": row, "rrf": 0.0, "legs": []})
            entry["rrf"] += _rrf(rank)
            entry["legs"].append(f"vector#{rank}")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"vector_leg_failed: {exc}")

    results = []
    for entry in hits.values():
        row = entry["row"]
        meta = row.get("metadata") or {}
        if isinstance(meta, str):
            import json as _json  # noqa: PLC0415

            try:
                meta = _json.loads(meta)
            except ValueError:
                meta = {}
        path = meta.get("source_path")
        results.append(
            {
                "title": row.get("object") or meta.get("file") or "",
                "tier": _row_tier(row.get("status"), path),
                "project": meta.get("project"),
                "date": meta.get("date"),
                "path": path,
                "excerpt": (meta.get("excerpt") or "")[:220],
                "score": round(entry["rrf"], 5),
                "matched": ",".join(entry["legs"]),
            }
        )
    # Curated outranks quarantined at equal fusion score: same evidence,
    # confirmed beats inferred.
    results.sort(key=lambda r: (r["score"], r["tier"] == TIER_CURATED), reverse=True)
    return results[:limit], reasons


def _sqlite_search(query: str, project: str | None, limit: int) -> tuple[list[dict], list[str]]:
    """Fallback: the local FTS index. Works with no server and no config."""
    try:
        import content_index  # type: ignore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return [], [f"fts_unavailable: {exc}"]
    try:
        rows = content_index.query(query, limit=limit, scope=project) or []
    except TypeError:
        rows = content_index.query(query, limit=limit) or []
    except Exception as exc:  # noqa: BLE001
        return [], [f"fts_failed: {exc}"]
    out = []
    for row in rows:
        row = dict(row) if not isinstance(row, dict) else row
        path = str(row.get("path") or "")
        out.append(
            {
                "title": row.get("title") or Path(path).stem,
                "tier": _row_tier(None, path),
                "project": project,
                "date": row.get("date"),
                "path": path,
                "excerpt": (row.get("snippet") or "")[:220],
                "score": row.get("score"),
                "matched": "fts",
            }
        )
    return out, []


def _resolve_schema() -> tuple[str, list[str]]:
    """Pick the schema that actually holds decisions, not the one merely configured.

    `_paths.default_schema()` returns `personal_memory` unless
    `$AGENT_MEMORY_SCHEMA` is set, and on this machine that schema is empty
    while `build_loop_memory` holds every synced decision. An unconfigured
    host therefore queried an empty schema and fell through to FTS reporting
    NO reason — the silent-empty failure this module exists to end. Probe for
    rows and say which schema answered.
    """
    reasons: list[str] = []
    candidates: list[str] = []
    try:
        from _paths import default_schema  # type: ignore  # noqa: PLC0415

        candidates.append(default_schema())
    except Exception:  # noqa: BLE001
        pass
    for fallback in (os.environ.get("AGENT_MEMORY_SCHEMA"), "build_loop_memory", "personal_memory"):
        if fallback and fallback not in candidates:
            candidates.append(fallback)
    try:
        import db  # type: ignore  # noqa: PLC0415

        import re as _re  # noqa: PLC0415

        for candidate in candidates:
            if not _re.match(r"^[a-z][a-z0-9_]*$", candidate):
                continue
            try:
                row = db.query_one(
                    f"SELECT count(*) AS c FROM {candidate}.semantic_facts"  # nosec: validated identifier
                )
            except Exception:  # noqa: BLE001
                continue
            if row and int(dict(row).get("c") or 0) > 0:
                if candidate != candidates[0]:
                    reasons.append(f"schema_fallback: {candidates[0]} empty, used {candidate}")
                return candidate, reasons
    except Exception:  # noqa: BLE001
        pass
    return (candidates[0] if candidates else "build_loop_memory"), reasons


def find(
    query: str,
    project: str | None = None,
    limit: int = 5,
    tier: str = "all",
    schema: str | None = None,
) -> dict[str, Any]:
    """Search memory. Always returns an envelope; never raises on backend gaps."""
    schema_reasons: list[str] = []
    if schema is None:
        schema, schema_reasons = _resolve_schema()

    results, reasons = _pg_search(query, project, limit, schema)
    reasons = schema_reasons + reasons
    backend = "postgres-hybrid"
    if not results:
        results, fts_reasons = _sqlite_search(query, project, limit)
        reasons.extend(fts_reasons)
        backend = "sqlite-fts"
    if tier != "all":
        results = [r for r in results if r["tier"] == tier]
    return {
        "query": query,
        "project": project,
        "schema": schema,
        "backend": backend,
        "results": results,
        "reasons": reasons,
    }


def render(envelope: dict[str, Any]) -> str:
    """Token-lean text rendering: one line per hit, evidence path included."""
    lines = []
    for item in envelope.get("results") or []:
        mark = "✓" if item["tier"] == TIER_CURATED else "?"
        lines.append(f"{mark} {item['title']}".rstrip())
        if item.get("excerpt"):
            lines.append(f"    {item['excerpt']}")
        if item.get("path"):
            lines.append(f"    {item['path']}")
    if not lines:
        reasons = "; ".join(envelope.get("reasons") or []) or "no match"
        return f"no results ({reasons})\n"
    legend = "✓ confirmed decision   ? unreviewed capture (verify before relying on it)"
    return "\n".join(lines) + f"\n\n{legend}\n"
