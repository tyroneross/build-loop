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
# index_paths runs on the write-through hot path (atomic_io.atomic_write_bytes,
# 29 importers) and must never sit on a memory write: this is a fail-fast
# busy timeout, not sqlite3's 5.0s default, so lock contention against a
# concurrent build()/index_paths call degrades to a skipped upsert instead of
# a multi-second stall. Measured on this platform: sqlite3's busy-retry
# schedule overshoots the requested `timeout` by roughly 3-6x (a 0.25s
# request measured ~1.5s wall-clock), so this is kept well below the 1.0s
# write-through budget rather than at the "~0.25s" figure that reads most
# naturally -- a few tens of milliseconds of grace for a same-process
# commit-boundary overlap, not a real wait for cross-process contention.
_WRITE_THROUGH_TIMEOUT_S = 0.05
_QUERY_TOKEN_RE = re.compile(r"\S+", re.UNICODE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DSL_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\S+', re.UNICODE)
_DSL_KEYS = {"type": "doc_type", "status": "status", "project": "project",
             "tag": "tag", "since": "since", "until": "until"}
# Both sentinels are globally visible per build-loop's memory routing rule
# ("would this apply to a different project? yes -> global"): "global" is
# _scope()'s fallback for anything not under projects/<slug>/, and
# "_unscoped" is the literal project slug of the global project lane
# (projects/_unscoped/...). Treating only "global" as global silently drops
# every _unscoped-routed document the instant a caller names a project — the
# same write-only-global-lane bug memory_facade/decisions.py already
# documents (17 of 47 rows affected there). Kept identical to
# memory_facade.decisions._GLOBAL_SCOPES so the two modules cannot drift.
_GLOBAL_SCOPES = frozenset({"_unscoped", "global"})


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


def _schema_version_readonly(db_path: Path, *, timeout: float = _WRITE_THROUGH_TIMEOUT_S) -> int | None:
    """Return the on-disk ``schema_version`` via a read-only connection.

    Returns ``None`` when the table is absent/empty, the file isn't a
    readable sqlite database, or the read itself times out under lock
    contention — every one of those is "can't confirm the schema is safe to
    write into", which callers must treat identically to a real mismatch.
    """
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=timeout)
    except sqlite3.OperationalError:
        return None
    try:
        row = connection.execute("SELECT schema_version FROM index_state").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def _connect(db_path: Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=timeout)
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
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(store.resolve())
    except (OSError, ValueError):
        return False
    return resolved.suffix.lower() == ".md" and not any(part in _SKIP_PARTS for part in relative.parts)


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


def _upsert_file(connection: sqlite3.Connection, path: Path, store: Path, *, incremental: bool = True) -> str:
    """Write (or skip) one file's row into content/meta/files. Returns "indexed" or "skipped".

    Shared by ``build()`` (walks the whole store) and ``index_paths()`` (targets
    specific files); the three-table INSERT is written exactly once here so a
    schema change never needs to be kept in sync across two call sites.
    """
    stat = path.stat()
    if stat.st_size > _MAX_FILE_BYTES:
        return "skipped"
    absolute = str(path.resolve())
    previous = connection.execute("SELECT rowid, mtime_ns, size FROM files WHERE path = ?", (absolute,)).fetchone()
    if incremental and previous is not None and previous[1:] == (stat.st_mtime_ns, stat.st_size):
        return "skipped"
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
    return "indexed"


def _delete_path_rows(connection: sqlite3.Connection, path_text: str) -> bool:
    """Delete *path_text*'s rows if present. Returns whether anything was deleted.

    Callers that report a ``deleted`` count (``index_paths``) must only count
    genuine removals — a path that was never indexed (outside the store, or
    not yet written) is a no-op, not a deletion, and reporting it as one
    claims work that did not happen.
    """
    row = connection.execute("SELECT rowid FROM files WHERE path = ?", (path_text,)).fetchone()
    if row is None:
        return False
    connection.execute("DELETE FROM content WHERE rowid = ?", (row[0],))
    connection.execute("DELETE FROM meta WHERE rowid = ?", (row[0],))
    connection.execute("DELETE FROM files WHERE path = ?", (path_text,))
    return True


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
                outcome = _upsert_file(connection, path, store, incremental=incremental)
                if outcome == "indexed":
                    indexed += 1
                    if indexed % _BATCH_SIZE == 0:
                        connection.commit()
                else:
                    skipped += 1
            except OSError:
                skipped += 1
        connection.commit()
        total_docs = connection.execute("SELECT count(*) FROM files").fetchone()[0]
    finally:
        connection.close()
    return {"indexed": indexed, "skipped": skipped, "deleted": deleted, "elapsed_s": time.monotonic() - started,
            "db_path": str(target), "total_docs": total_docs}


def index_paths(paths: list[Path | str], *, store: Path | None = None, db_path: Path | None = None) -> dict:
    """Upsert or delete SPECIFIC markdown files in the FTS index, without walking the store.

    For the write-through hook: a full incremental ``build()`` measured 33s over
    10,545 docs, three orders of magnitude too slow to sit on a single memory
    write. This touches only the given paths.

    A path that no longer exists on disk, or fails ``_indexable`` (outside the
    store, non-``.md``, in a skipped directory), is DELETED from the index —
    covers renames and deletes. Never raises; failures on one path count as
    "skipped" and processing continues with the rest.

    Never CREATES the database. ``sqlite3.connect`` creates the file it's
    given, so connecting unconditionally would make the first durable write to
    a store with no index silently produce a one-document database — recall's
    ``Path(db).is_file()`` check would then pass and the loud
    ``content_index_absent: ... build it with ...`` diagnostic would never
    fire again, hiding near-total under-coverage behind a database that merely
    looks present. ``build()`` stays the only creator: it is the explicit
    command that diagnostic tells the caller to run.

    Never MIGRATES a foreign-schema database either. ``_connect`` drops and
    recreates ``content``/``meta``/``files``/``index_state`` whenever
    ``schema_version`` doesn't match — correct for ``build()``, which refills
    every row right after, but silent total data loss here: this call only
    ever re-adds the handful of paths it was given, so routing it through
    ``_connect``'s migrating path would collapse a 40-document index down to
    1. The read-only precheck below refuses that path instead, reporting the
    given paths as skipped and leaving the database untouched for an explicit
    ``build()`` to migrate.
    """
    resolved_store = (memory_store_root() if store is None else Path(store)).resolve()
    target = default_db_path(resolved_store) if db_path is None else Path(db_path)
    if not target.is_file():
        return {"indexed": 0, "deleted": 0, "skipped": len(paths)}
    if _schema_version_readonly(target) != _SCHEMA_VERSION:
        return {"indexed": 0, "deleted": 0, "skipped": len(paths)}
    indexed = deleted = skipped = 0
    try:
        connection = _connect(target, timeout=_WRITE_THROUGH_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — never raise from the write-through hot path
        return {"indexed": 0, "deleted": 0, "skipped": len(paths)}
    try:
        for raw_path in paths:
            path = Path(raw_path)
            try:
                if not _indexable(path, resolved_store):
                    # path.resolve() (default strict=False) matches the key _upsert_file
                    # stored even when the file no longer exists — it normalizes as far
                    # as the existing parent directories allow, same as when the row
                    # was written.
                    if _delete_path_rows(connection, str(path.resolve())):
                        deleted += 1
                    else:
                        # Never indexed in the first place (outside the store, or not
                        # yet written) — a genuine no-op, not a deletion.
                        skipped += 1
                    continue
                # Resolve now that _indexable confirmed path.resolve(strict=True) succeeds
                # and lands under resolved_store — build()'s callers get this for free via
                # rglob on an already-resolved store; index_paths' caller-supplied paths
                # need the same normalization so _scope()'s relative_to(store) can't fail
                # on a symlinked tmp prefix (macOS /var -> /private/var).
                outcome = _upsert_file(connection, path.resolve(), resolved_store, incremental=True)
                if outcome == "indexed":
                    indexed += 1
                else:
                    skipped += 1
            except Exception:  # noqa: BLE001 — one bad path must not stop the batch
                skipped += 1
        connection.commit()
    except Exception:  # noqa: BLE001
        pass
    finally:
        connection.close()
    return {"indexed": indexed, "deleted": deleted, "skipped": skipped}


def _values(value: str | list[str] | None) -> list[str]:
    return [str(item) for item in (value if isinstance(value, list) else [value]) if item is not None and str(item)]


def _filters(project: str | list[str] | None, doc_type: str | list[str] | None,
             status: str | list[str] | None, tag: str | list[str] | None,
             since: str | None, until: str | None,
             scope: str | list[str] | None = None) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    # project keeps the original path-scope contract for existing recall callers.
    projects = _values(project)
    if projects:
        global_scopes = sorted(_GLOBAL_SCOPES)
        clauses.append("(content.scope IN (%s) OR content.scope IN (%s))" % (
            ",".join("?" * len(projects)), ",".join("?" * len(global_scopes))))
        params.extend(projects)
        params.extend(global_scopes)
    # scope is the EXACT-match counterpart of project: no implicit global OR.
    # A caller that must guarantee a project sees its own documents needs to
    # ask for exactly those, because `project` widens the set to include every
    # global document and the SQL LIMIT is then spent on whichever rows rank
    # highest across the whole widened set.
    scopes = _values(scope)
    if scopes:
        clauses.append("content.scope IN (%s)" % ",".join("?" * len(scopes)))
        params.extend(scopes)
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


_ROW_COLUMNS = (
    "SELECT content.path, content.scope, content.file_id, content.name, content.title, "
    "files.mtime_ns, {relevance} AS relevance, {snippet}, meta.doc_type, meta.status, meta.created "
    "FROM content JOIN files ON files.rowid = content.rowid JOIN meta ON meta.rowid = content.rowid "
)


def query(q: str, *, limit: int = 20, project: str | list[str] | None = None,
          db_path: Path | None = None, doc_type: str | list[str] | None = None,
          status: str | list[str] | None = None, tag: str | list[str] | None = None,
          since: str | None = None, until: str | None = None, mode: str = "any",
          exclude: list[str] | None = None, browse_on_empty: bool = False,
          scope: str | list[str] | None = None) -> list[dict]:
    """Return search matches; positive structured filters exclude NULL field values.

    Failures return ``[]`` because this is on the recall hot path. ``project`` retains
    its original path-scope behavior: the selected scope plus global documents.

    ``browse_on_empty`` makes an empty or token-less query BROWSE the filtered
    set, newest first, instead of returning nothing. Callers that replaced a
    whole-file reader with this index need it: the readers being replaced treat
    an empty query as "match everything" (``memory_facade.common._q_match``
    returns True), so without it a bare "show me recent decisions" recall
    silently returns zero rows. It is opt-in rather than the default because
    every existing caller was written against "no query means no results", and
    flipping that for all of them would turn a typo into a full-index scan.
    """
    if limit <= 0 or mode not in {"any", "all"}:
        return []
    expression = _match_expression(q, mode=mode, exclude=exclude) if q else ""
    if not expression and not browse_on_empty:
        return []
    target = default_db_path() if db_path is None else Path(db_path)
    if not target.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            clauses, params = _filters(project, doc_type, status, tag, since, until, scope)
            if expression:
                # bm25() and snippet() are only defined against a MATCH.
                select = _ROW_COLUMNS.format(
                    relevance="-bm25(content, 0, 0, 0, 0, 10.0, 4.0, 1.0)",
                    snippet="snippet(content, 6, '[', ']', '…', 24)",
                )
                where = "content MATCH ?" + (" AND " + " AND ".join(clauses) if clauses else "")
                args: list[object] = [expression, *params, limit]
                order = "relevance DESC"
            else:
                select = _ROW_COLUMNS.format(relevance="0.0", snippet="substr(content.body, 1, 160)")
                where = " AND ".join(clauses) if clauses else "1"
                args = [*params, limit]
                order = "COALESCE(meta.created, meta.updated) DESC, files.mtime_ns DESC"
            rows = connection.execute(f"{select}WHERE {where} ORDER BY {order} LIMIT ?", args)
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
    index_paths_parser = commands.add_parser("index-paths")
    index_paths_parser.add_argument("paths", nargs="+", type=Path)
    index_paths_parser.add_argument("--store", type=Path)
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
    if args.command == "index-paths":
        print(json.dumps(index_paths(args.paths, store=args.store), sort_keys=True))
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
