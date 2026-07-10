#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Portable graph adapter for build-loop-memory.

The canonical graph is the rebuildable JSONL pair in
``<memory-root>/indexes/graph-nodes.jsonl`` and ``graph-edges.jsonl``.
``sqlite_edges`` is the default runtime backend: it mirrors that JSONL into a
small SQLite index and answers bounded related-node traversals.  ``ladybug`` is
opt-in only; when unavailable or smoke-gate incompatible, callers fall back to
``sqlite_edges`` with a reason in the response.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import deque
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _paths import memory_indexes_dir, memory_store_root  # type: ignore  # noqa: E402

SCHEMA_VERSION = "1.0.0"
DEFAULT_BACKEND = "sqlite_edges"
VALID_DIRECTIONS = {"out", "in", "both"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _source_signature(nodes_path: Path, edges_path: Path) -> str:
    parts: list[dict[str, Any]] = []
    for path in (nodes_path, edges_path):
        try:
            stat = path.stat()
        except OSError:
            parts.append({"path": str(path), "exists": False})
            continue
        parts.append(
            {
                "path": str(path),
                "exists": True,
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
            }
        )
    return json.dumps(parts, sort_keys=True)


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _node_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    node_id = _text(row.get("id") or row.get("canonical_id") or row.get("path")).strip()
    if not node_id:
        return None
    return {
        "id": node_id,
        "title": _text(row.get("title") or row.get("name") or node_id, node_id),
        "path": _text(row.get("path") or row.get("canonical_path")),
        "project": _text(row.get("project")),
        "memory_type": _text(row.get("memory_type") or row.get("type")),
        "status": _text(row.get("status")),
        "data": row,
    }


def _edge_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    source = _text(row.get("source")).strip()
    target = _text(row.get("target")).strip()
    if not source or not target:
        return None
    edge_id = _text(row.get("id") or f"{source}->{target}:{row.get('relation', '')}")
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "relation": _text(row.get("relation") or row.get("type") or "related"),
        "data": row,
    }


class SQLiteEdgesStore:
    """SQLite-backed adapter over rebuildable graph JSONL files."""

    backend = DEFAULT_BACKEND

    def __init__(
        self,
        *,
        root: Path,
        db_path: Path | str | None = None,
        reasons: Iterable[str] = (),
    ) -> None:
        self.root = root
        self.index_dir = root / "indexes"
        self.nodes_path = self.index_dir / "graph-nodes.jsonl"
        self.edges_path = self.index_dir / "graph-edges.jsonl"
        # SQLite is a rebuildable runtime cache. Keep it out of the tracked
        # ``indexes/`` surface so expand-mode reads never dirty memory git.
        self.db_path = db_path or root / "db" / "runtime" / "graph.sqlite"
        self.reasons = list(reasons)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._sync_if_needed()

    def close(self) -> None:
        self._conn.close()

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS nodes (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL DEFAULT '',
              path TEXT NOT NULL DEFAULT '',
              project TEXT NOT NULL DEFAULT '',
              memory_type TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT '',
              data TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS edges (
              id TEXT PRIMARY KEY,
              source TEXT NOT NULL,
              target TEXT NOT NULL,
              relation TEXT NOT NULL DEFAULT 'related',
              data TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
            CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);
            """
        )

    def _sync_if_needed(self) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        signature = _source_signature(self.nodes_path, self.edges_path)
        current = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'source_signature'"
        ).fetchone()
        if current and current["value"] == signature:
            return

        node_rows = [_node_from_row(row) for row in _read_jsonl(self.nodes_path)]
        edge_rows = [_edge_from_row(row) for row in _read_jsonl(self.edges_path)]
        nodes = [row for row in node_rows if row is not None]
        edges = [row for row in edge_rows if row is not None]

        with self._conn:
            self._conn.execute("DELETE FROM edges")
            self._conn.execute("DELETE FROM nodes")
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO nodes
                (id, title, path, project, memory_type, status, data)
                VALUES (:id, :title, :path, :project, :memory_type, :status, :data)
                """,
                [
                    {
                        **row,
                        "data": json.dumps(row["data"], sort_keys=True, default=str),
                    }
                    for row in nodes
                ],
            )
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO edges (id, source, target, relation, data)
                VALUES (:id, :source, :target, :relation, :data)
                """,
                [
                    {
                        **row,
                        "data": json.dumps(row["data"], sort_keys=True, default=str),
                    }
                    for row in edges
                ],
            )
            for edge in edges:
                for endpoint in (edge["source"], edge["target"]):
                    self._conn.execute(
                        """
                        INSERT OR IGNORE INTO nodes
                        (id, title, path, project, memory_type, status, data)
                        VALUES (?, ?, '', '', '', '', '{}')
                        """,
                        (endpoint, endpoint),
                    )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('source_signature', ?)",
                (signature,),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (SCHEMA_VERSION,),
            )

    def stats(self) -> dict[str, int]:
        nodes = self._conn.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()["n"]
        edges = self._conn.execute("SELECT COUNT(*) AS n FROM edges").fetchone()["n"]
        return {"nodes": int(nodes), "edges": int(edges)}

    def _edges_for(
        self,
        node_id: str,
        *,
        direction: str,
        relation: str | None,
    ) -> list[sqlite3.Row]:
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f"invalid direction {direction!r}; expected one of {sorted(VALID_DIRECTIONS)}")
        rel_sql = " AND relation = ?" if relation else ""
        rel_args: tuple[str, ...] = (relation,) if relation else ()
        rows: list[sqlite3.Row] = []
        if direction in {"out", "both"}:
            rows.extend(
                self._conn.execute(
                    f"SELECT * FROM edges WHERE source = ?{rel_sql}",  # nosec: only ?-placeholders / constant fragments interpolated; values bound as params
                    (node_id, *rel_args),
                ).fetchall()
            )
        if direction in {"in", "both"}:
            rows.extend(
                self._conn.execute(
                    f"SELECT * FROM edges WHERE target = ?{rel_sql}",  # nosec: only ?-placeholders / constant fragments interpolated; values bound as params
                    (node_id, *rel_args),
                ).fetchall()
            )
        return rows

    def _node(self, node_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row["data"])
        except json.JSONDecodeError:
            data = {}
        return {
            "id": row["id"],
            "title": row["title"],
            "path": row["path"],
            "project": row["project"],
            "memory_type": row["memory_type"],
            "status": row["status"],
            "data": data,
        }

    def related(
        self,
        seeds: str | Iterable[str],
        *,
        depth: int = 1,
        limit: int = 10,
        direction: str = "both",
        relation: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Return nodes and edges reachable from ``seeds`` within ``depth``.

        The response shape follows the graph-query convention
        ``MATCH (seed)-[r*1..depth]-(related) RETURN related, r`` without
        binding callers to a non-portable graph database.
        """
        self._sync_if_needed()
        seed_list = [seeds] if isinstance(seeds, str) else [str(seed) for seed in seeds]
        seed_list = [seed for seed in seed_list if seed]
        max_depth = max(0, int(depth))
        max_limit = max(0, int(limit))

        queue: deque[tuple[str, int]] = deque((seed, 0) for seed in seed_list)
        seen = set(seed_list)
        node_hits: dict[str, dict[str, Any]] = {}
        edge_hits: dict[str, dict[str, Any]] = {}

        while queue:
            node_id, hop = queue.popleft()
            if hop >= max_depth:
                continue
            for edge in self._edges_for(node_id, direction=direction, relation=relation):
                source = edge["source"]
                target = edge["target"]
                neighbor = target if source == node_id else source
                next_hop = hop + 1
                edge_hits.setdefault(
                    edge["id"],
                    {
                        "id": edge["id"],
                        "source": source,
                        "target": target,
                        "relation": edge["relation"],
                        "hop": next_hop,
                    },
                )
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, next_hop))
                node = self._node(neighbor)
                if node is None:
                    continue
                if project and node.get("project") and node["project"] != project:
                    continue
                prior = node_hits.get(neighbor)
                score = 1.0 / (next_hop + 1)
                if prior is None or next_hop < prior["hop"]:
                    node_hits[neighbor] = {**node, "hop": next_hop, "score": score}
                elif prior is not None:
                    prior["score"] += score

        nodes = [
            row
            for row in node_hits.values()
            if row["id"] not in seed_list
        ]
        nodes.sort(key=lambda row: (-float(row.get("score", 0.0)), int(row.get("hop", 0)), row.get("title", "")))
        if max_limit:
            nodes = nodes[:max_limit]
        return {
            "schema_version": SCHEMA_VERSION,
            "backend": self.backend,
            "query_shape": f"MATCH (seed)-[r*1..{max_depth}]-(related) RETURN related, r",
            "seeds": seed_list,
            "depth": max_depth,
            "direction": direction,
            "relation": relation,
            "nodes": nodes,
            "edges": list(edge_hits.values()),
            "stats": self.stats(),
            "reasons": list(self.reasons),
        }


class GraphStore:
    """Backend-selecting graph adapter.

    ``sqlite_edges`` is the default. ``ladybug`` is accepted only as an opt-in
    request and falls back automatically unless its import + smoke gate succeed.
    """

    @classmethod
    def open(
        cls,
        *,
        root: Path | str | None = None,
        backend: str | None = None,
        db_path: Path | str | None = None,
    ) -> SQLiteEdgesStore:
        memory_root = Path(root).resolve() if root else memory_store_root().resolve()
        requested = backend or os.environ.get("BUILD_LOOP_MEMORY_GRAPH_BACKEND") or DEFAULT_BACKEND
        reasons: list[str] = []
        if requested in {"ladybug", "auto"}:
            reasons.extend(_ladybug_smoke_reasons())
        elif requested != DEFAULT_BACKEND:
            reasons.append(f"unsupported_backend:{requested}; using {DEFAULT_BACKEND}")
        return SQLiteEdgesStore(root=memory_root, db_path=db_path, reasons=reasons)


def _ladybug_smoke_reasons() -> list[str]:
    try:
        __import__("ladybug")
    except Exception as exc:  # noqa: BLE001
        return [f"ladybug_unavailable:{type(exc).__name__}"]
    return ["ladybug_smoke_unimplemented; using sqlite_edges"]


__all__ = ["GraphStore", "SQLiteEdgesStore"]
