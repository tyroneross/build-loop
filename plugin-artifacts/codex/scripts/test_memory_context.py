#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/memory_context."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class MemoryContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.memroot = self.root / "build-loop-memory"
        self.workdir = self.root / "repo"
        self.memroot.mkdir()
        self.workdir.mkdir()
        self._prev_env = {
            "BUILD_LOOP_MEMORY_STORE_ROOT": os.environ.get("BUILD_LOOP_MEMORY_STORE_ROOT"),
            "BUILD_LOOP_MEMORY_ROOT": os.environ.get("BUILD_LOOP_MEMORY_ROOT"),
            "AGENT_MEMORY_ROOT": os.environ.get("AGENT_MEMORY_ROOT"),
            "EMBED_BACKEND_UNAVAILABLE": os.environ.get("EMBED_BACKEND_UNAVAILABLE"),
        }
        os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = str(self.memroot)
        os.environ.pop("BUILD_LOOP_MEMORY_ROOT", None)
        os.environ.pop("AGENT_MEMORY_ROOT", None)
        os.environ["EMBED_BACKEND_UNAVAILABLE"] = "1"
        self.project = "demo"
        self.project_dir = self.memroot / "projects" / self.project
        (self.project_dir / "context").mkdir(parents=True)
        (self.project_dir / "decisions").mkdir()
        (self.project_dir / "lessons").mkdir()
        (self.memroot / "lessons").mkdir()
        (self.memroot / "indexes").mkdir()
        (self.project_dir / "context" / "CONTEXT.md").write_text(
            "---\nname: demo-context\n---\n# Context\n\n## Governing Summary\nUse build-loop-memory as fast context.\n\n## Gaps\nNone.\n",
            encoding="utf-8",
        )
        (self.project_dir / "decisions" / "decision-demo.md").write_text(
            "---\ntitle: Use hot capsule\ncanonical_id: decision-demo\n---\nUse CURRENT.json for fast start.\n",
            encoding="utf-8",
        )
        (self.project_dir / "lessons" / "lesson-demo.md").write_text(
            "---\nname: hot-context-lesson\ndescription: Hot context starts agents faster\n---\nFast context should avoid raw evidence.\n",
            encoding="utf-8",
        )
        (self.memroot / "indexes" / "graph-nodes.jsonl").write_text(
            json.dumps(
                {
                    "id": "decision-demo",
                    "title": "Use hot capsule",
                    "path": "projects/demo/decisions/decision-demo.md",
                    "project": "demo",
                    "memory_type": "decision",
                    "status": "accepted",
                    "type": "memory-entry",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.memroot / "indexes" / "graph-edges.jsonl").write_text(
            json.dumps(
                {
                    "id": "edge-project-demo-decision",
                    "source": "project:demo",
                    "target": "decision-demo",
                    "relation": "contains",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        for mod in list(sys.modules):
            if mod == "memory_context" or mod.startswith("memory_context."):
                del sys.modules[mod]

    def tearDown(self) -> None:
        for key, value in self._prev_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def test_build_context_writes_current_files(self) -> None:
        import memory_context as mc  # noqa: PLC0415

        envelope = mc.build_context(self.workdir, query="hot context", project=self.project, write=True)

        current = envelope["current"]
        self.assertEqual(envelope["mode"], "fast")
        self.assertEqual(current["kind"], "build-loop-memory-current")
        self.assertEqual(current["project"], self.project)
        self.assertIn("Use build-loop-memory as fast context", current["context"]["summary"])
        self.assertEqual(current["decisions"][0]["id"], "decision-demo")
        self.assertTrue(any(item["id"] == "context:CONTEXT" for item in current["evidence"]))

        paths = mc.current_paths(self.project)
        self.assertTrue(paths["json"].is_file())
        self.assertTrue(paths["markdown"].is_file())
        self.assertTrue(paths["freshness"].is_file())
        loaded = json.loads(paths["json"].read_text(encoding="utf-8"))
        self.assertEqual(loaded["project"], self.project)

    def test_expand_mode_uses_lessons_index(self) -> None:
        import memory_context as mc  # noqa: PLC0415

        envelope = mc.build_context(
            self.workdir,
            query="hot context agents",
            mode="expand",
            project=self.project,
            write=False,
        )

        self.assertEqual(envelope["mode"], "expand")
        self.assertIn("expansion", envelope)
        names = [item["name"] for item in envelope["expansion"]["lessons"]]
        self.assertIn("hot-context-lesson", names)
        graph = envelope["expansion"]["graph"]
        self.assertEqual(graph["backend"], "sqlite_edges")
        related_ids = [item["id"] for item in graph["related"]]
        self.assertIn("decision-demo", related_ids)

    def test_research_and_reference_lanes_are_retrievable(self) -> None:
        import memory_context as mc  # noqa: PLC0415

        research_dir = self.project_dir / "research"
        research_dir.mkdir()
        (research_dir / "deep-research.md").write_text(
            "# Deep Research\n\nEvidence register and claim matrix for retrieval.\n",
            encoding="utf-8",
        )
        envelope = mc.build_context(
            self.workdir,
            query="evidence register claim matrix",
            mode="expand",
            project=self.project,
            write=False,
        )
        self.assertEqual(envelope["current"]["research"][0]["title"], "Deep Research")
        names = [item["name"] for item in envelope["expansion"]["lessons"]]
        self.assertIn("deep-research", names)

    def test_open_artifact_reads_evidence_id(self) -> None:
        import memory_context as mc  # noqa: PLC0415

        result = mc.open_artifact("context:CONTEXT", workdir=self.workdir, project=self.project)

        self.assertTrue(result["exists"])
        self.assertIn("Governing Summary", result["text"])

    def test_validate_current_rejects_missing_fields(self) -> None:
        import memory_context as mc  # noqa: PLC0415

        with self.assertRaises(ValueError):
            mc.validate_current({"schema_version": "1.0.0", "kind": "build-loop-memory-current"})


if __name__ == "__main__":
    unittest.main()
