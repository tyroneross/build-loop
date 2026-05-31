#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""lessons_index — portable SQLite/FTS5 lessons index for build-loop.

Zero external dependencies. Works out-of-box on a fresh install.
Postgres recall.py remains the optional power-tier; this is the PRIMARY.

Public API:
  ingest(project, db_path)  — walk memory lanes, upsert markdown → SQLite
  query(goal_text, project, limit, db_path) — BM25 + optional cosine rerank
  stats(db_path)            — counts + schema_version + last_ingest_ts
  open_db(db_path)          — connection helper (creates schema if needed)
"""
from __future__ import annotations

import sys
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
for _p in (str(_PKG_DIR), str(_PKG_DIR.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import importlib.util as _iutil  # noqa: E402

def _load_pkg_module(name: str):
    spec = _iutil.spec_from_file_location(
        f"lessons_index._{name}", _PKG_DIR / f"{name}.py"
    )
    mod = _iutil.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_schema = _load_pkg_module("schema")
_ingest = _load_pkg_module("ingest")
_query = _load_pkg_module("query")

open_db = _schema.open_db
ingest = _ingest.ingest
query = _query.query


def stats(db_path=None) -> dict:
    """Return a stats dict: {total_facts, schema_version, last_ingest_ts}.

    Safe to call on a non-existent DB (returns zeros).
    """
    import sqlite3  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415

    if db_path is None:
        try:
            from _paths import memory_store_root  # type: ignore  # noqa: PLC0415
            db_path = memory_store_root() / "indexes" / "lessons_index.db"
        except ImportError:
            return {"total_facts": 0, "schema_version": None, "last_ingest_ts": None}

    db_path = _Path(db_path)
    if not db_path.exists():
        return {"total_facts": 0, "schema_version": None, "last_ingest_ts": None}

    conn = open_db(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        meta_rows = conn.execute("SELECT key, value FROM meta").fetchall()
        meta = {r["key"]: r["value"] for r in meta_rows}
        return {
            "total_facts": total,
            "schema_version": meta.get("schema_version"),
            "last_ingest_ts": meta.get("last_ingest_ts"),
        }
    finally:
        conn.close()


__all__ = ["ingest", "query", "stats", "open_db"]
