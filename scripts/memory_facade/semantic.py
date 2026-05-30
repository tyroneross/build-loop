#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Backend 3: agent_memory.<schema>.semantic_facts (Postgres) reader."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import _parse_iso

# _db_url lives in scripts/ (the parent of this package); importable because
# __init__.py inserts scripts/ into sys.path before any sub-module is loaded.
from _db_url import NO_URL_REASON, resolve_db_url  # type: ignore  # noqa: E402


def read_semantic(
    workdir: Path,
    query: str,
    limit: int,
    project: Optional[str],
    skip_postgres: bool = False,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read semantic_facts from Postgres.

    ``skip_postgres=True``: bypass entirely; records reason ``skipped_postgres``
    (distinct from ``db_unavailable: ...``) so consumers can tell intentional
    skip from genuine backend-down.
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
                    where.append(
                        "(subject ILIKE %s OR predicate ILIKE %s OR object ILIKE %s)"
                    )
                    params.extend([f"%{query}%"] * 3)
                sql = (
                    f"SELECT id, subject, predicate, object, project, "
                    f"confidence, last_accessed FROM {schema}.semantic_facts "
                    f"WHERE {' AND '.join(where)} "
                    f"ORDER BY last_accessed DESC LIMIT %s"
                )
                params.append(limit)
                cur.execute(sql, params)
                for r in cur.fetchall():
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
