#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/memory_graph."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from memory_graph import GraphStore  # noqa: E402


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


class MemoryGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.indexes = self.root / "indexes"
        _write_jsonl(
            self.indexes / "graph-nodes.jsonl",
            [
                {
                    "id": "decision-demo",
                    "title": "Demo decision",
                    "path": "projects/demo/decisions/decision-demo.md",
                    "project": "demo",
                    "memory_type": "decision",
                    "status": "accepted",
                    "type": "memory-entry",
                }
            ],
        )
        _write_jsonl(
            self.indexes / "graph-edges.jsonl",
            [
                {
                    "id": "edge-project-demo-decision",
                    "source": "project:demo",
                    "target": "decision-demo",
                    "relation": "contains",
                },
                {
                    "id": "edge-demo-tag",
                    "source": "decision-demo",
                    "target": "tag:memory",
                    "relation": "tagged_with",
                },
            ],
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_related_traverses_jsonl_graph_via_sqlite(self) -> None:
        graph = GraphStore.open(root=self.root, db_path=self.indexes / "graph.sqlite")

        result = graph.related("project:demo", depth=1, limit=5)

        self.assertEqual(result["backend"], "sqlite_edges")
        self.assertEqual(result["stats"]["edges"], 2)
        ids = [node["id"] for node in result["nodes"]]
        self.assertIn("decision-demo", ids)
        decision = next(node for node in result["nodes"] if node["id"] == "decision-demo")
        self.assertEqual(decision["path"], "projects/demo/decisions/decision-demo.md")

    def test_unsupported_backend_falls_back_to_sqlite(self) -> None:
        graph = GraphStore.open(
            root=self.root,
            backend="unsupported",
            db_path=self.indexes / "graph.sqlite",
        )

        result = graph.related("project:demo", depth=1)

        self.assertEqual(result["backend"], "sqlite_edges")
        self.assertTrue(any("unsupported_backend" in reason for reason in result["reasons"]))

    def test_ladybug_request_falls_back_to_sqlite(self) -> None:
        graph = GraphStore.open(
            root=self.root,
            backend="ladybug",
            db_path=self.indexes / "graph.sqlite",
        )

        result = graph.related("project:demo", depth=1)

        self.assertEqual(result["backend"], "sqlite_edges")
        self.assertTrue(any(reason.startswith("ladybug_") for reason in result["reasons"]))


if __name__ == "__main__":
    unittest.main()
