#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""SQLite schema + connection helper for the portable lessons index.

FTS5 sync approach: EXTERNAL-CONTENT FTS5 (content='facts', content_rowid='id')
kept in sync by INSERT/UPDATE/DELETE triggers.

Rationale: external-content avoids duplicating the body text into the FTS
shadow (the `facts` table is the single content store; FTS holds only the
inverted index). The triggers below maintain the shadow on every write so
MATCH queries stay current without a manual rebuild. As a safety net for the
edge case where the shadow drifts (e.g. the content table is modified
out-of-band, or a trigger was missing at creation time), `query._rebuild_fts()`
does a full DELETE + INSERT('rebuild') — a trivially correct O(N) operation at
our target sizes (<100K lessons). Note: COUNT(*) on an external-content FTS
table reads through to `facts`, so the post-ingest non-empty check is reliable.

Tables:
  facts(id, lane, project, name, description, body, frontmatter_json,
        source_path UNIQUE, mtime REAL, sha256 TEXT)
  facts_fts — FTS5 over (name, description, body), rebuilt on ingest
  embeddings(fact_id PK REFERENCES facts, model, dim, vec BLOB)
  meta(key TEXT PK, value TEXT)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = "1"

DDL = """\
CREATE TABLE IF NOT EXISTS facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lane            TEXT    NOT NULL DEFAULT '',
    project         TEXT    NOT NULL DEFAULT '',
    name            TEXT    NOT NULL DEFAULT '',
    description     TEXT    NOT NULL DEFAULT '',
    body            TEXT    NOT NULL DEFAULT '',
    frontmatter_json TEXT   NOT NULL DEFAULT '{}',
    source_path     TEXT    NOT NULL UNIQUE,
    mtime           REAL    NOT NULL DEFAULT 0.0,
    sha256          TEXT    NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    name,
    description,
    body,
    content='facts',
    content_rowid='id'
);

CREATE TABLE IF NOT EXISTS embeddings (
    fact_id INTEGER PRIMARY KEY REFERENCES facts(id) ON DELETE CASCADE,
    model   TEXT    NOT NULL DEFAULT '',
    dim     INTEGER NOT NULL DEFAULT 0,
    vec     BLOB    NOT NULL DEFAULT x''
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

# Triggers to keep the standalone FTS shadow in sync when facts rows change.
# These ensure that if facts rows are modified outside of ingest (e.g. direct
# SQL in tests), the FTS index stays consistent without a full rebuild.
_FTS_TRIGGERS = """\
CREATE TRIGGER IF NOT EXISTS facts_fts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, name, description, body)
    VALUES (new.id, new.name, new.description, new.body);
END;

CREATE TRIGGER IF NOT EXISTS facts_fts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, name, description, body)
    VALUES ('delete', old.id, old.name, old.description, old.body);
END;

CREATE TRIGGER IF NOT EXISTS facts_fts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, name, description, body)
    VALUES ('delete', old.id, old.name, old.description, old.body);
    INSERT INTO facts_fts(rowid, name, description, body)
    VALUES (new.id, new.name, new.description, new.body);
END;
"""


def open_db(db_path: str | Path) -> sqlite3.Connection:
    """Open (or create) the lessons index DB, apply schema, return connection.

    The returned connection has WAL mode enabled for better concurrent read
    access and foreign_keys enforced. Caller is responsible for closing.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Apply schema DDL (all CREATE IF NOT EXISTS — idempotent).
    conn.executescript(DDL)

    # Apply FTS triggers (CREATE TRIGGER IF NOT EXISTS — idempotent).
    conn.executescript(_FTS_TRIGGERS)

    # Seed schema_version in meta if missing.
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return conn
