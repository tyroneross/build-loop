#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/context_bootstrap.py."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import context_bootstrap as cb  # noqa: E402


class EnvIsolationMixin:
    def setUp(self) -> None:  # type: ignore[override]
        super().setUp()  # type: ignore[misc]
        self._prev_env = {
            "AGENT_MEMORY_ROOT": os.environ.get("AGENT_MEMORY_ROOT"),
            "BUILD_LOOP_MEMORY_ROOT": os.environ.get("BUILD_LOOP_MEMORY_ROOT"),
            "BUILD_LOOP_MEMORY_STORE_ROOT": os.environ.get("BUILD_LOOP_MEMORY_STORE_ROOT"),
            "CODEX_MEMORY_ROOT": os.environ.get("CODEX_MEMORY_ROOT"),
        }
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.workdir = self.tmp_root / "repo"
        self.memroot = self.tmp_root / "build-loop-memory"
        self.codex_root = self.tmp_root / "codex-memory"
        self.workdir.mkdir()
        self.memroot.mkdir()
        self.codex_root.mkdir()
        os.environ["AGENT_MEMORY_ROOT"] = str(self.memroot)
        os.environ.pop("BUILD_LOOP_MEMORY_ROOT", None)
        os.environ.pop("BUILD_LOOP_MEMORY_STORE_ROOT", None)
        os.environ["CODEX_MEMORY_ROOT"] = str(self.codex_root)

    def tearDown(self) -> None:  # type: ignore[override]
        for key, val in self._prev_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._tmp.cleanup()
        super().tearDown()  # type: ignore[misc]


class ContextBootstrapTests(EnvIsolationMixin, unittest.TestCase):
    def write_repo_local(self) -> None:
        bl = self.workdir / ".build-loop"
        bl.mkdir()
        (bl / "feedback.md").write_text(
            "2026-05-27 | memory bootstrap missed Codex memory | load Codex registry in Phase 1\n",
            encoding="utf-8",
        )
        (bl / "intent.md").write_text("Keep context available before planning.\n", encoding="utf-8")
        (bl / "goal.md").write_text("Emit relevant memory context packet.\n", encoding="utf-8")
        (bl / "state.json").write_text(
            json.dumps(
                {
                    "execution": {"build_loop_id": "bl-test"},
                    "architecture": {
                        "backendHealth": {
                            "decisions": {"ok": False, "reason": "store_down"}
                        }
                    },
                    "runs": [
                        {"run_id": "old", "outcome": "irrelevant"},
                        {
                            "run_id": "recent",
                            "goal": "memory bootstrap",
                            "outcome": "context was lost",
                            "root_cause": "Codex memory was not loaded",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    def write_canonical_lesson(self) -> None:
        (self.memroot / "MEMORY.md").write_text(
            "Global build-loop memory says context bootstrap should include canonical files.\n",
            encoding="utf-8",
        )
        (self.memroot / "constitution.md").write_text(
            "C-MEM/context-bootstrap: Phase 1 memory bootstrap must be automatic.\n",
            encoding="utf-8",
        )
        lessons = self.memroot / "lessons"
        lessons.mkdir(parents=True)
        (lessons / "pattern_context_bootstrap.md").write_text(
            "---\nname: Context bootstrap\n---\nUse context bootstrap for memory bootstrap Phase 1 runs.\n",
            encoding="utf-8",
        )

    def write_codex_memory(self) -> None:
        rollout_dir = self.codex_root / "rollout_summaries"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "memory-bootstrap.md").write_text(
            "thread_id=019e6591-9fa6-79b2-aaa3-c8e3ffdff440\n"
            "The rollout showed memory bootstrap should read repo-local feedback and Codex memory.\n",
            encoding="utf-8",
        )
        (self.codex_root / "MEMORY.md").write_text(
            "# Task Group: /tmp/repo memory bootstrap gap\n"
            "scope: Relevant to memory bootstrap and lost context.\n"
            "- rollout_summaries/memory-bootstrap.md (thread_id=019e6591-9fa6-79b2-aaa3-c8e3ffdff440)\n"
            "## Reusable knowledge\n"
            "- Phase 1 should load Codex memory and repo-local feedback before planning.\n"
            "\n"
            "# Task Group: other topic\n"
            "scope: unrelated\n",
            encoding="utf-8",
        )

    def test_packet_includes_repo_local_canonical_and_codex_memory(self) -> None:
        self.write_repo_local()
        self.write_canonical_lesson()
        self.write_codex_memory()

        packet = cb.build_packet(
            workdir=self.workdir,
            query="memory bootstrap",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
            limit=5,
        )

        self.assertEqual(packet["project"], "_unscoped")
        canonical_hits = packet["sources"]["canonical_memory"]["merged"]
        self.assertTrue(any(hit.get("_kind") == "lessons" for hit in canonical_hits))
        canonical_files = packet["sources"]["canonical_memory"]["files"]
        self.assertTrue(any(item["path"].endswith("MEMORY.md") and item["exists"] for item in canonical_files))
        self.assertTrue(any(item["path"].endswith("constitution.md") and item["exists"] for item in canonical_files))

        repo_files = packet["sources"]["repo_local"]["files"]
        feedback = next(item for item in repo_files if item["path"] == ".build-loop/feedback.md")
        self.assertTrue(feedback["exists"])
        self.assertIn("Codex memory", feedback["excerpt"])

        state = next(item for item in repo_files if item["path"] == ".build-loop/state.json")
        self.assertEqual(state["summary"]["backendHealth"]["decisions"]["ok"], False)
        self.assertEqual(state["summary"]["runs_tail"][-1]["run_id"], "recent")

        codex_hits = packet["sources"]["codex_memory"]["registry_hits"]
        self.assertEqual(len(codex_hits), 1)
        self.assertEqual(codex_hits[0]["line_start"], 1)
        self.assertIn("rollout_summaries/memory-bootstrap.md", codex_hits[0]["rollout_refs"])
        self.assertEqual(len(packet["sources"]["codex_memory"]["rollout_hits"]), 1)
        self.assertIn("Top Codex Memory Hits", packet["agent_brief"])

    def test_missing_codex_memory_degrades_without_error(self) -> None:
        self.write_repo_local()
        missing_root = self.tmp_root / "missing-codex"
        packet = cb.build_packet(
            workdir=self.workdir,
            query="memory bootstrap",
            codex_memory_root=missing_root,
            include_postgres=False,
            include_rally=False,
        )
        self.assertEqual(packet["sources"]["codex_memory"]["registry_hits"], [])
        self.assertTrue(packet["sources"]["codex_memory"]["reasons"])
        self.assertIn("Relevant Memory Context", packet["agent_brief"])

    def test_write_packet_is_atomic_json(self) -> None:
        self.write_repo_local()
        self.write_codex_memory()
        packet = cb.build_packet(
            workdir=self.workdir,
            query="memory bootstrap",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        out = self.workdir / ".build-loop" / "context-bootstrap.json"
        cb.write_packet(packet, out)
        loaded = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(loaded["query"], "memory bootstrap")
        self.assertFalse((out.parent / ".context-bootstrap.json.tmp").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
