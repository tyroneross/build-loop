#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Local SQLite semantic-facts index.

The canonical memory store remains file-backed. This module provides the
local, rebuildable semantic index used when Postgres is absent or deliberately
disabled. It is stdlib-only so fresh package installs can bootstrap memory
without database credentials or optional Python packages.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from _paths import memory_indexes_dir  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    memory_indexes_dir = None  # type: ignore[assignment]

DB_FILENAME = "semantic_facts.sqlite"
SCHEMA_VERSION = "1.0.0"
GLOBAL_PROJECT_KEY = "__GLOBAL__"
TOKEN_RE = re.compile(r"[A-Za-z0-9_.:/-]+")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_db_path() -> Path:
    if memory_indexes_dir is None:
        return Path.home() / "dev" / "git-folder" / "build-loop-memory" / "indexes" / DB_FILENAME
    return memory_indexes_dir() / DB_FILENAME


def _db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path).expanduser().resolve() if db_path else default_db_path().expanduser().resolve()


def _project_key(project: str | None) -> str:
    return project if project else GLOBAL_PROJECT_KEY


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_iso(value: Any) -> float | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 10_000_000_000 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    init(conn)
    return conn


def init(conn_or_path: sqlite3.Connection | str | Path | None = None) -> Path | None:
    owns_conn = not isinstance(conn_or_path, sqlite3.Connection)
    if owns_conn:
        path = _db_path(conn_or_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
    else:
        conn = conn_or_path
    if conn is None:
        raise RuntimeError("sqlite connection unavailable")
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_facts (
              subject TEXT NOT NULL,
              project_key TEXT NOT NULL,
              predicate TEXT NOT NULL DEFAULT '',
              object TEXT NOT NULL DEFAULT '',
              confidence REAL,
              status TEXT NOT NULL DEFAULT 'active',
              metadata_json TEXT NOT NULL DEFAULT '{}',
              project TEXT,
              tool TEXT,
              task_category TEXT,
              files_touched_json TEXT NOT NULL DEFAULT '[]',
              confidence_source TEXT,
              domain TEXT,
              source_prefix TEXT,
              embedding_json TEXT,
              last_synced TEXT NOT NULL,
              schema_version TEXT NOT NULL DEFAULT '1.0.0',
              PRIMARY KEY(subject, project_key)
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_semantic_project ON semantic_facts(project_key, status, last_synced DESC);"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_semantic_domain ON semantic_facts(domain, status, last_synced DESC);"
        )
        conn.commit()
    finally:
        if owns_conn:
            conn.close()
    return None if isinstance(conn_or_path, sqlite3.Connection) else _db_path(conn_or_path)


def upsert_fact(
    *,
    subject: str,
    predicate: str,
    object_text: str,
    project: str | None,
    confidence: float | None = None,
    status: str = "active",
    metadata: dict[str, Any] | None = None,
    tool: str | None = None,
    task_category: str | None = None,
    files_touched: list[str] | None = None,
    confidence_source: str | None = None,
    domain: str | None = None,
    source_prefix: str | None = None,
    embedding: list[float] | None = None,
    db_path: str | Path | None = None,
) -> None:
    if not subject.strip():
        raise ValueError("subject is required")
    metadata = dict(metadata or {})
    metadata.setdefault("last_synced", now_iso())
    metadata.setdefault("schema_version", SCHEMA_VERSION)
    files = [str(item) for item in (files_touched or [])]
    conn = connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO semantic_facts (
              subject, project_key, predicate, object, confidence, status,
              metadata_json, project, tool, task_category, files_touched_json,
              confidence_source, domain, source_prefix, embedding_json,
              last_synced, schema_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject, project_key) DO UPDATE SET
              predicate=excluded.predicate,
              object=excluded.object,
              confidence=excluded.confidence,
              status=excluded.status,
              metadata_json=excluded.metadata_json,
              project=excluded.project,
              tool=excluded.tool,
              task_category=excluded.task_category,
              files_touched_json=excluded.files_touched_json,
              confidence_source=excluded.confidence_source,
              domain=excluded.domain,
              source_prefix=excluded.source_prefix,
              embedding_json=excluded.embedding_json,
              last_synced=excluded.last_synced,
              schema_version=excluded.schema_version;
            """,
            (
                subject,
                _project_key(project),
                predicate or "",
                object_text or "",
                confidence,
                status or "active",
                _json(metadata),
                project,
                tool,
                task_category,
                _json(files),
                confidence_source,
                domain,
                source_prefix,
                _json(embedding) if embedding else None,
                metadata["last_synced"],
                SCHEMA_VERSION,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_lesson(
    *,
    lesson: dict[str, Any],
    project: str | None,
    subject_prefix: str = "lesson:nav:",
    confidence: float | None = None,
    confidence_source: str | None = None,
    embedding: list[float] | None = None,
    db_path: str | Path | None = None,
    tool: str = "navgator",
    domain: str = "architecture",
) -> None:
    lesson_id = str(lesson.get("id", "") or "").strip()
    if not lesson_id:
        raise ValueError("lesson id is required")
    context = lesson.get("context") or {}
    files_touched: list[str] = []
    if isinstance(context, dict) and isinstance(context.get("files_affected"), list):
        files_touched = [str(item) for item in context["files_affected"]]
    promoted = bool(lesson.get("promoted", False))
    metadata = {
        "lesson_id": lesson_id,
        "promoted": promoted,
        "navgator_lesson": lesson,
    }
    upsert_fact(
        subject=f"{subject_prefix}{lesson_id}",
        predicate=str(lesson.get("category", "") or "uncategorized"),
        object_text=str(lesson.get("pattern", "") or ""),
        project=project,
        confidence=confidence,
        metadata=metadata,
        tool=tool,
        task_category="research",
        files_touched=files_touched,
        confidence_source=confidence_source,
        domain=domain,
        source_prefix=subject_prefix,
        embedding=embedding,
        db_path=db_path,
    )


def _tokens(query: str) -> list[str]:
    return [m.group(0).lower() for m in TOKEN_RE.finditer(query or "")]


def _score(row: sqlite3.Row, tokens: list[str]) -> int:
    if not tokens:
        return 1
    haystack = " ".join(
        str(row[key] or "") for key in ("subject", "predicate", "object", "project", "domain", "tool")
    ).lower()
    return sum(1 for token in tokens if token in haystack)


def query_facts(
    *,
    query: str = "",
    limit: int = 10,
    project: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    path = _db_path(db_path)
    if not path.exists():
        return []
    conn = connect(path)
    try:
        where = ["status = 'active'"]
        params: list[Any] = []
        if project:
            where.append("project = ?")
            params.append(project)
        rows = conn.execute(
            f"""
            SELECT rowid, subject, predicate, object, project, confidence,
                   last_synced, metadata_json, tool, domain, confidence_source
            FROM semantic_facts
            WHERE {' AND '.join(where)}
            ORDER BY last_synced DESC, rowid DESC
            LIMIT ?
            """,
            [*params, max(limit * 20, limit)],
        ).fetchall()
    finally:
        conn.close()
    tokens = _tokens(query)
    ranked: list[tuple[int, sqlite3.Row]] = []
    for row in rows:
        score = _score(row, tokens)
        if tokens and score <= 0:
            continue
        ranked.append((score, row))
    ranked.sort(key=lambda item: (item[0], _parse_iso(item[1]["last_synced"]) or 0), reverse=True)
    out: list[dict[str, Any]] = []
    for _, row in ranked[:limit]:
        out.append({
            "_kind": "semantic",
            "_recency_ts": _parse_iso(row["last_synced"]),
            "id": f"sqlite:{row['rowid']}",
            "subject": row["subject"],
            "predicate": row["predicate"],
            "object": row["object"],
            "project": row["project"],
            "confidence": row["confidence"],
            "last_accessed": row["last_synced"],
            "backend": "sqlite",
            "tool": row["tool"],
            "domain": row["domain"],
            "confidence_source": row["confidence_source"],
        })
    return out


def stats(db_path: str | Path | None = None) -> dict[str, Any]:
    path = _db_path(db_path)
    if not path.exists():
        return {"db_path": str(path), "exists": False, "rows": 0}
    conn = connect(path)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM semantic_facts").fetchone()[0]
    finally:
        conn.close()
    return {"db_path": str(path), "exists": True, "rows": int(rows)}
