#!/usr/bin/env python3
"""Lexical FTS5 index for markdown documents in build-loop-memory.

The index deliberately stores only document bodies in the searchable FTS
column.  Metadata is returned to callers but cannot inflate recall.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterator

from _paths import memory_store_root


_SKIP_PARTS = frozenset({
    ".git",
    "node_modules",
    "archive",
    "raw-originals",
    "indexes",
    ".venv",
    "__pycache__",
    ".build-loop",
    ".rally",
})
_MAX_FILE_BYTES = 1 << 20
_BATCH_SIZE = 500
_ROW_KEYS = {
    "_kind", "_scope", "_recency_ts", "id", "name", "title", "path",
    "_relevance", "snippet",
}
_QUERY_TOKEN_RE = re.compile(r"\S+", re.UNICODE)


def _match_expression(query: str) -> str:
    """Return a syntax-safe OR expression for index-tokenized query terms."""
    tokens = [
        token for token in _QUERY_TOKEN_RE.findall(query)
        if any(character.isalnum() for character in token)
    ]
    return " OR ".join(
        f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens
    )


def default_db_path(store: Path | None = None) -> Path:
    """Return the standard content-index database path for *store*."""
    root = memory_store_root() if store is None else Path(store)
    return root / "indexes" / "content_fts.sqlite"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS content USING fts5("
            "path UNINDEXED, scope UNINDEXED, file_id UNINDEXED, "
            "name UNINDEXED, title UNINDEXED, body, "
            "tokenize='porter unicode61')"
        )
    except sqlite3.OperationalError as exc:
        connection.close()
        raise RuntimeError(
            "SQLite FTS5 is unavailable in this Python sqlite3 build; "
            "content indexing requires FTS5."
        ) from exc
    connection.execute(
        "CREATE TABLE IF NOT EXISTS files ("
        "path TEXT PRIMARY KEY, rowid INTEGER NOT NULL, "
        "mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL)"
    )
    return connection


def _indexable(path: Path, store: Path) -> bool:
    try:
        relative = path.relative_to(store)
    except ValueError:
        return False
    return path.suffix.lower() == ".md" and not any(
        part in _SKIP_PARTS for part in relative.parts
    )


def _markdown_files(store: Path) -> Iterator[Path]:
    for path in store.rglob("*.md"):
        if _indexable(path, store):
            yield path


def _scope(path: Path, store: Path) -> str:
    relative = path.relative_to(store)
    if len(relative.parts) >= 2 and relative.parts[0] == "projects":
        return relative.parts[1]
    return "global"


def _title_and_body(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    title = path.stem
    if lines and lines[0].rstrip("\r\n") == "---":
        for index, line in enumerate(lines[1:], start=1):
            if line.rstrip("\r\n") != "---":
                continue
            for field in lines[1:index]:
                key, separator, value = field.partition(":")
                if separator and key.strip().lower() == "title":
                    candidate = value.strip().strip("\"'")
                    if candidate:
                        title = candidate
                    break
            return title, "".join(lines[index + 1:])
    return title, text


def _delete_stale_rows(connection: sqlite3.Connection, store: Path) -> int:
    deleted = 0
    for path_text, rowid in connection.execute("SELECT path, rowid FROM files"):
        path = Path(path_text)
        if path.exists() and _indexable(path, store):
            continue
        connection.execute("DELETE FROM content WHERE rowid = ?", (rowid,))
        connection.execute("DELETE FROM files WHERE path = ?", (path_text,))
        deleted += 1
    return deleted


def build(
    store: Path,
    *,
    incremental: bool = True,
    db_path: Path | None = None,
    max_files: int | None = None,
) -> dict:
    """Build or incrementally update the markdown body index for *store*."""
    started = time.monotonic()
    store = Path(store).resolve()
    target = default_db_path(store) if db_path is None else Path(db_path)
    connection = _connect(target)
    indexed = skipped = deleted = processed = 0
    try:
        if not incremental:
            connection.execute("DELETE FROM content")
            connection.execute("DELETE FROM files")
        else:
            deleted = _delete_stale_rows(connection, store)

        for path in _markdown_files(store):
            if max_files is not None and processed >= max_files:
                break
            processed += 1
            try:
                stat = path.stat()
                if stat.st_size > _MAX_FILE_BYTES:
                    skipped += 1
                    continue
                absolute = str(path.resolve())
                previous = connection.execute(
                    "SELECT rowid, mtime_ns, size FROM files WHERE path = ?",
                    (absolute,),
                ).fetchone()
                if (
                    incremental
                    and previous is not None
                    and previous[1] == stat.st_mtime_ns
                    and previous[2] == stat.st_size
                ):
                    skipped += 1
                    continue
                title, body = _title_and_body(path)
                if previous is not None:
                    connection.execute("DELETE FROM content WHERE rowid = ?", (previous[0],))
                    connection.execute("DELETE FROM files WHERE path = ?", (absolute,))
                cursor = connection.execute(
                    "INSERT INTO content(path, scope, file_id, name, title, body) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (absolute, _scope(path, store), path.stem, path.name, title, body),
                )
                connection.execute(
                    "INSERT INTO files(path, rowid, mtime_ns, size) VALUES (?, ?, ?, ?)",
                    (absolute, cursor.lastrowid, stat.st_mtime_ns, stat.st_size),
                )
                indexed += 1
                if indexed % _BATCH_SIZE == 0:
                    connection.commit()
            except OSError:
                skipped += 1
        connection.commit()
        total_docs = connection.execute("SELECT count(*) FROM files").fetchone()[0]
    finally:
        connection.close()
    return {
        "indexed": indexed,
        "skipped": skipped,
        "deleted": deleted,
        "elapsed_s": time.monotonic() - started,
        "db_path": str(target),
        "total_docs": total_docs,
    }


def query(
    q: str,
    *,
    limit: int = 20,
    project: str | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """Return body-search matches; failure is empty because this is a hot path."""
    if not q or limit <= 0:
        return []
    match_expression = _match_expression(q)
    if not match_expression:
        return []
    target = default_db_path() if db_path is None else Path(db_path)
    if not target.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            where = "content MATCH ?"
            params: list[object] = [match_expression]
            if project is not None:
                where += " AND (scope = ? OR scope = 'global')"
                params.append(project)
            params.append(limit)
            rows = connection.execute(
                "SELECT content.path, content.scope, content.file_id, content.name, "
                "content.title, files.mtime_ns, -bm25(content) AS relevance, "
                "snippet(content, 5, '[', ']', '…', 24) AS excerpt "
                "FROM content JOIN files ON files.rowid = content.rowid "
                f"WHERE {where} ORDER BY relevance DESC LIMIT ?",
                params,
            )
            return [
                {
                    "_kind": "content",
                    "_scope": row[1],
                    "_recency_ts": row[5] / 1_000_000_000,
                    "id": row[2],
                    "name": row[3],
                    "title": row[4],
                    "path": row[0],
                    "_relevance": max(float(row[6]), sys.float_info.min),
                    "snippet": row[7],
                }
                for row in rows
            ]
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("content index query failed: %s", exc)
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--store", type=Path)
    build_parser.add_argument("--full", action="store_true")
    build_parser.add_argument("--max-files", type=int)
    query_parser = commands.add_parser("query")
    query_parser.add_argument("text")
    query_parser.add_argument("--limit", type=int, default=20)
    query_parser.add_argument("--project")
    query_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "build":
        result = build(
            memory_store_root() if args.store is None else args.store,
            incremental=not args.full,
            max_files=args.max_files,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    rows = query(args.text, limit=args.limit, project=args.project)
    if args.json:
        print(json.dumps(rows, sort_keys=True))
    else:
        for row in rows:
            print(f"{row['title']}\t{row['path']}\t{row['_relevance']:.6g}\t{row['snippet']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
