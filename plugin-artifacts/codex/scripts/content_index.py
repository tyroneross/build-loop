#!/usr/bin/env python3
"""Lexical FTS5 index and structured filters for build-loop-memory markdown."""
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


_SCHEMA_VERSION = 2
_SKIP_PARTS = frozenset({
    ".git", "node_modules", "archive", "raw-originals", "indexes", ".venv",
    "__pycache__", ".build-loop", ".rally",
})
_MAX_FILE_BYTES = 1 << 20
_BATCH_SIZE = 500
_QUERY_TOKEN_RE = re.compile(r"\S+", re.UNICODE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DSL_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\S+', re.UNICODE)
_DSL_KEYS = {"type": "doc_type", "status": "status", "project": "project",
             "tag": "tag", "since": "since", "until": "until"}


def _match_expression(query: str, *, mode: str = "any", exclude: list[str] | None = None) -> str:
    """Return a syntax-safe FTS expression made from literal query tokens."""
    terms = [token for token in _QUERY_TOKEN_RE.findall(query) if any(char.isalnum() for char in token)]
    excluded = [token for value in exclude or [] for token in _QUERY_TOKEN_RE.findall(value)
                if any(char.isalnum() for char in token)]
    if not terms:
        return ""
    quote = lambda token: f'"{token.replace(chr(34), chr(34) * 2)}"'
    expression = f" {('AND' if mode == 'all' else 'OR')} ".join(quote(term) for term in terms)
    for term in excluded:
        expression = f"({expression}) NOT {quote(term)}"
    return expression


def default_db_path(store: Path | None = None) -> Path:
    """Return the standard content-index database path for *store*."""
    root = memory_store_root() if store is None else Path(store)
    return root / "indexes" / "content_fts.sqlite"


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE VIRTUAL TABLE content USING fts5("
        "path UNINDEXED, scope UNINDEXED, file_id UNINDEXED, name UNINDEXED, "
        "title, tags, body, tokenize='porter unicode61')"
    )
    connection.execute(
        "CREATE TABLE meta("
        "rowid INTEGER PRIMARY KEY, path TEXT UNIQUE, doc_type TEXT, status TEXT, "
        "project TEXT, primary_tag TEXT, confidence TEXT, created TEXT, updated TEXT, "
        "tags TEXT)"
    )
    for column in ("doc_type", "status", "project", "primary_tag", "confidence", "created", "updated"):
        connection.execute(f"CREATE INDEX meta_{column}_idx ON meta({column})")
    connection.execute(
        "CREATE TABLE files(path TEXT PRIMARY KEY, rowid INTEGER NOT NULL, "
        "mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL)"
    )
    connection.execute("CREATE TABLE index_state(schema_version INTEGER NOT NULL)")
    connection.execute("INSERT INTO index_state(schema_version) VALUES (?)", (_SCHEMA_VERSION,))


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        has_state = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'index_state'"
        ).fetchone()
        version = connection.execute("SELECT schema_version FROM index_state").fetchone()[0] if has_state else None
        if version != _SCHEMA_VERSION:
            # Rebuild v2 from source. A full build is cheap; migration code is bug surface.
            connection.executescript(
                "DROP TABLE IF EXISTS content; DROP TABLE IF EXISTS meta; "
                "DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS index_state;"
            )
            _create_schema(connection)
            connection.commit()
    except sqlite3.OperationalError as exc:
        connection.close()
        raise RuntimeError(f"content index schema setup failed: {exc}") from exc
    return connection


def _indexable(path: Path, store: Path) -> bool:
    try:
        relative = path.relative_to(store)
    except ValueError:
        return False
    return path.suffix.lower() == ".md" and not any(part in _SKIP_PARTS for part in relative.parts)


def _markdown_files(store: Path) -> Iterator[Path]:
    for path in store.rglob("*.md"):
        if _indexable(path, store):
            yield path


def _scope(path: Path, store: Path) -> str:
    relative = path.relative_to(store)
    return relative.parts[1] if len(relative.parts) >= 2 and relative.parts[0] == "projects" else "global"


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip()


def _tags(value: str) -> list[str]:
    value = _scalar(value)
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [tag for part in value.split(",") if (tag := _scalar(part))]


def _frontmatter_and_body(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return {}, text
    end = next((index for index, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") == "---"), None)
    if end is None:
        return {}, text
    fields: dict[str, object] = {}
    active_list: str | None = None
    for line in lines[1:end]:
        stripped = line.strip()
        if active_list and stripped.startswith("-"):
            existing = fields.setdefault(active_list, [])
            if isinstance(existing, list):
                existing.append(_scalar(stripped[1:]))
            continue
        key, separator, value = line.partition(":")
        if not separator:
            active_list = None
            continue
        key, value = key.strip().lower(), value.strip()
        active_list = key if key == "tags" and not value else None
        if key == "tags":
            fields[key] = _tags(value) if value else []
        elif value:
            fields[key] = _scalar(value)
    return fields, "".join(lines[end + 1:])


def _metadata(path: Path) -> tuple[str, str, dict[str, object]]:
    fields, body = _frontmatter_and_body(path)
    tags = [str(tag) for tag in fields.get("tags", []) if str(tag)]
    return str(fields.get("title") or path.stem), body, {
        "doc_type": fields.get("type"), "status": fields.get("status"),
        "project": fields.get("project"), "primary_tag": fields.get("primary_tag"),
        "confidence": fields.get("confidence"), "created": fields.get("created") or fields.get("date"),
        "updated": fields.get("updated"), "tags": tags,
    }


def _stored_tags(tags: list[str]) -> str:
    return "".join(f"|{tag.lower()}|" for tag in tags)


def _delete_stale_rows(connection: sqlite3.Connection, store: Path) -> int:
    deleted = 0
    for path_text, rowid in connection.execute("SELECT path, rowid FROM files"):
        path = Path(path_text)
        if path.exists() and _indexable(path, store):
            continue
        connection.execute("DELETE FROM content WHERE rowid = ?", (rowid,))
        connection.execute("DELETE FROM meta WHERE rowid = ?", (rowid,))
        connection.execute("DELETE FROM files WHERE path = ?", (path_text,))
        deleted += 1
    return deleted


def build(store: Path, *, incremental: bool = True, db_path: Path | None = None,
          max_files: int | None = None) -> dict:
    """Build or incrementally update the markdown index for *store*."""
    started, store = time.monotonic(), Path(store).resolve()
    target = default_db_path(store) if db_path is None else Path(db_path)
    connection = _connect(target)
    indexed = skipped = deleted = processed = 0
    try:
        if not incremental:
            connection.execute("DELETE FROM content")
            connection.execute("DELETE FROM meta")
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
                previous = connection.execute("SELECT rowid, mtime_ns, size FROM files WHERE path = ?", (absolute,)).fetchone()
                if incremental and previous is not None and previous[1:] == (stat.st_mtime_ns, stat.st_size):
                    skipped += 1
                    continue
                title, body, meta = _metadata(path)
                tags = list(meta["tags"])
                if previous is not None:
                    connection.execute("DELETE FROM content WHERE rowid = ?", (previous[0],))
                    connection.execute("DELETE FROM meta WHERE rowid = ?", (previous[0],))
                    connection.execute("DELETE FROM files WHERE path = ?", (absolute,))
                cursor = connection.execute(
                    "INSERT INTO content(path, scope, file_id, name, title, tags, body) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (absolute, _scope(path, store), path.stem, path.name, title, " ".join(tags), body),
                )
                connection.execute(
                    "INSERT INTO meta(rowid, path, doc_type, status, project, primary_tag, confidence, created, updated, tags) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (cursor.lastrowid, absolute, meta["doc_type"], meta["status"], meta["project"], meta["primary_tag"],
                     meta["confidence"], meta["created"], meta["updated"], _stored_tags(tags)),
                )
                connection.execute("INSERT INTO files(path, rowid, mtime_ns, size) VALUES (?, ?, ?, ?)",
                                   (absolute, cursor.lastrowid, stat.st_mtime_ns, stat.st_size))
                indexed += 1
                if indexed % _BATCH_SIZE == 0:
                    connection.commit()
            except OSError:
                skipped += 1
        connection.commit()
        total_docs = connection.execute("SELECT count(*) FROM files").fetchone()[0]
    finally:
        connection.close()
    return {"indexed": indexed, "skipped": skipped, "deleted": deleted, "elapsed_s": time.monotonic() - started,
            "db_path": str(target), "total_docs": total_docs}


def _values(value: str | list[str] | None) -> list[str]:
    return [str(item) for item in (value if isinstance(value, list) else [value]) if item is not None and str(item)]


def _filters(project: str | list[str] | None, doc_type: str | list[str] | None,
             status: str | list[str] | None, tag: str | list[str] | None,
             since: str | None, until: str | None) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    # project keeps the original path-scope contract for existing recall callers.
    projects = _values(project)
    if projects:
        clauses.append("(content.scope IN (%s) OR content.scope = 'global')" % ",".join("?" * len(projects)))
        params.extend(projects)
    for column, value in (("meta.doc_type", doc_type), ("meta.status", status)):
        values = _values(value)
        if values:
            clauses.append(f"{column} IN ({','.join('?' * len(values))})")
            params.extend(values)
    tags = _values(tag)
    if tags:
        clauses.append("(" + " OR ".join("instr(meta.tags, ?) > 0" for _ in tags) + ")")
        params.extend(f"|{value.lower()}|" for value in tags)
    for operator, value in ((">=", since), ("<=", until)):
        if value is not None and _DATE_RE.fullmatch(value):
            clauses.append(f"COALESCE(meta.created, meta.updated) {operator} ?")
            params.append(value)
    return clauses, params


def query(q: str, *, limit: int = 20, project: str | list[str] | None = None,
          db_path: Path | None = None, doc_type: str | list[str] | None = None,
          status: str | list[str] | None = None, tag: str | list[str] | None = None,
          since: str | None = None, until: str | None = None, mode: str = "any",
          exclude: list[str] | None = None) -> list[dict]:
    """Return search matches; positive structured filters exclude NULL field values.

    Failures return ``[]`` because this is on the recall hot path. ``project`` retains
    its original path-scope behavior: the selected scope plus global documents.
    """
    if not q or limit <= 0 or mode not in {"any", "all"}:
        return []
    expression = _match_expression(q, mode=mode, exclude=exclude)
    if not expression:
        return []
    target = default_db_path() if db_path is None else Path(db_path)
    if not target.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            clauses, params = _filters(project, doc_type, status, tag, since, until)
            where = "content MATCH ?" + (" AND " + " AND ".join(clauses) if clauses else "")
            rows = connection.execute(
                "SELECT content.path, content.scope, content.file_id, content.name, content.title, files.mtime_ns, "
                "-bm25(content, 0, 0, 0, 0, 10.0, 4.0, 1.0) AS relevance, "
                "snippet(content, 6, '[', ']', '…', 24), meta.doc_type, meta.status, meta.created "
                "FROM content JOIN files ON files.rowid = content.rowid JOIN meta ON meta.rowid = content.rowid "
                f"WHERE {where} ORDER BY relevance DESC LIMIT ?", [expression, *params, limit])
            return [{"_kind": "content", "_scope": row[1], "_recency_ts": row[5] / 1_000_000_000,
                     "id": row[2], "name": row[3], "title": row[4], "path": row[0],
                     "_relevance": max(float(row[6]), sys.float_info.min), "snippet": row[7],
                     "doc_type": row[8], "status": row[9], "created": row[10]} for row in rows]
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("content index query failed: %s", exc)
        return []


def parse_query(text: str) -> dict:
    """Parse the safe compact query DSL into :func:`query` keyword arguments."""
    result: dict[str, object] = {"q": "", "mode": "any"}
    bare: list[str] = []
    for token in _DSL_TOKEN_RE.findall(text):
        if token == "all:":
            result["mode"] = "all"
        elif token.startswith("-") and len(token) > 1:
            result.setdefault("exclude", []).append(token[1:])  # type: ignore[union-attr]
        elif ":" in token:
            key, value = token.split(":", 1)
            mapped = _DSL_KEYS.get(key.lower())
            if mapped and value:
                existing = result.get(mapped)
                result[mapped] = [*existing, value] if isinstance(existing, list) else ([existing, value] if existing else value)
            else:
                bare.append(token)
        else:
            bare.append(token)
    result["q"] = " ".join(bare)
    return result


def facets(q: str, *, db_path: Path | None = None, **filters: object) -> dict:
    """Return top structured facets for the matching set; failures return ``{}``."""
    mode, exclude = str(filters.pop("mode", "any")), filters.pop("exclude", None)
    expression = _match_expression(str(q), mode=mode, exclude=exclude if isinstance(exclude, list) else None)
    target = default_db_path() if db_path is None else Path(db_path)
    if not expression or not target.is_file():
        return {}
    try:
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            clauses, params = _filters(filters.get("project"), filters.get("doc_type"), filters.get("status"),
                                       filters.get("tag"), filters.get("since"), filters.get("until"))
            where = "content MATCH ?" + (" AND " + " AND ".join(clauses) if clauses else "")
            base = "FROM content JOIN meta ON meta.rowid = content.rowid WHERE " + where
            result: dict[str, object] = {"total": connection.execute("SELECT count(*) " + base, [expression, *params]).fetchone()[0]}
            for key, column in (("doc_type", "meta.doc_type"), ("status", "meta.status"), ("project", "meta.project")):
                rows = connection.execute(
                    f"SELECT {column}, count(*) {base} AND {column} IS NOT NULL GROUP BY {column} "
                    f"ORDER BY count(*) DESC, {column} LIMIT 10", [expression, *params])
                result[key] = {row[0]: row[1] for row in rows}
            return result
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        logging.getLogger(__name__).warning("content index facets failed: %s", exc)
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--store", type=Path)
    build_parser.add_argument("--full", action="store_true")
    build_parser.add_argument("--max-files", type=int)
    query_parser = commands.add_parser("query")
    query_parser.add_argument("text", nargs="?", default="")
    query_parser.add_argument("--dsl")
    query_parser.add_argument("--limit", type=int, default=20)
    query_parser.add_argument("--project")
    query_parser.add_argument("--type", dest="doc_type")
    query_parser.add_argument("--status")
    query_parser.add_argument("--tag")
    query_parser.add_argument("--since")
    query_parser.add_argument("--until")
    query_parser.add_argument("--all", action="store_true")
    query_parser.add_argument("--exclude", action="append")
    query_parser.add_argument("--json", action="store_true")
    facets_parser = commands.add_parser("facets")
    facets_parser.add_argument("text")
    for option, kwargs in (("--project", {}), ("--type", {"dest": "doc_type"}), ("--status", {}), ("--tag", {}),
                           ("--since", {}), ("--until", {})):
        facets_parser.add_argument(option, **kwargs)
    facets_parser.add_argument("--all", action="store_true")
    facets_parser.add_argument("--exclude", action="append")
    args = parser.parse_args(argv)
    if args.command == "build":
        print(json.dumps(build(memory_store_root() if args.store is None else args.store,
                               incremental=not args.full, max_files=args.max_files), sort_keys=True))
        return 0
    if args.command == "facets":
        print(json.dumps(facets(args.text, project=args.project, doc_type=args.doc_type, status=args.status,
                                tag=args.tag, since=args.since, until=args.until,
                                mode="all" if args.all else "any", exclude=args.exclude), sort_keys=True))
        return 0
    kwargs = parse_query(args.dsl) if args.dsl else {"q": args.text}
    kwargs["limit"] = args.limit
    for key in ("project", "doc_type", "status", "tag", "since", "until"):
        value = getattr(args, key)
        if value is not None:
            kwargs[key] = value
    if args.all:
        kwargs["mode"] = "all"
    if args.exclude:
        kwargs["exclude"] = [*kwargs.get("exclude", []), *args.exclude]
    rows = query(**kwargs)  # type: ignore[arg-type]
    if args.json:
        print(json.dumps(rows, sort_keys=True))
    else:
        for row in rows:
            print(f"{row['title']}\t{row['path']}\t{row['_relevance']:.6g}\t{row['snippet']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
