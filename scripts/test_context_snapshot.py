#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/context_snapshot.py."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import context_snapshot as cs  # noqa: E402


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
        self.write_repo_state()
        self.write_codex_memory()

    def tearDown(self) -> None:  # type: ignore[override]
        for key, val in self._prev_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._tmp.cleanup()
        super().tearDown()  # type: ignore[misc]

    def write_repo_state(self) -> None:
        bl = self.workdir / ".build-loop"
        bl.mkdir()
        (bl / "feedback.md").write_text("Snapshot feedback.\n", encoding="utf-8")
        (bl / "intent.md").write_text("Keep live context current.\n", encoding="utf-8")
        (bl / "goal.md").write_text("Write context snapshots.\n", encoding="utf-8")
        (bl / "plan.md").write_text("Plan: add context snapshot writer.\n", encoding="utf-8")
        (bl / "state.json").write_text(
            json.dumps(
                {
                    "phase": "execute",
                    "execution": {
                        "run_id": "run-test",
                        "build_loop_id": "bl-test",
                    },
                    "runs": [],
                }
            ),
            encoding="utf-8",
        )
        ws = bl / "working-state"
        ws.mkdir()
        (ws / "current.json").write_text(
            json.dumps(
                {
                    "agent": "orchestrator",
                    "status": "dispatching",
                    "chunk_id": "c1",
                    "current_task_summary": "Implement context snapshot writer",
                }
            ),
            encoding="utf-8",
        )

    def write_codex_memory(self) -> None:
        rollout_dir = self.codex_root / "rollout_summaries"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "context-snapshot.md").write_text(
            "thread_id=019e6591-9fa6-79b2-aaa3-c8e3ffdff440\n"
            "Context snapshot should preserve handoff state.\n",
            encoding="utf-8",
        )
        (self.codex_root / "MEMORY.md").write_text(
            "# Task Group: context snapshot\n"
            "scope: live context snapshot handoff.\n"
            "- rollout_summaries/context-snapshot.md (thread_id=019e6591-9fa6-79b2-aaa3-c8e3ffdff440)\n",
            encoding="utf-8",
        )

    def args(self, trigger: str = "manual", **overrides: object) -> argparse.Namespace:
        base = {
            "workdir": str(self.workdir),
            "trigger": trigger,
            "query": "context snapshot",
            "phase": "",
            "agent": "codex",
            "run_id": "",
            "chunk_id": "c1",
            "status": "dispatching",
            "message": "snapshot test",
            "next_action": "run validation",
            "files": ["scripts/context_snapshot.py"],
            "commit_sha": "",
            "validation_command": ["python3 scripts/test_context_snapshot.py"],
            "validation_result": "pending",
            "if_changed": False,
            "retention": 10,
            "limit": 4,
            "include_postgres": False,
            "include_debugger": False,
            "include_rally": False,
            "max_excerpt_chars": 800,
            "rollout_limit": 2,
            "json": True,
        }
        base.update(overrides)
        return argparse.Namespace(**base)


class ContextSnapshotTests(EnvIsolationMixin, unittest.TestCase):
    def test_manual_snapshot_writes_current_index_and_json(self) -> None:
        args = self.args("manual")
        snapshot = cs.build_snapshot(args)
        result = cs.write_snapshot(snapshot, self.workdir, if_changed=False, retention=10)

        self.assertTrue(result["ok"])
        current = self.workdir / ".build-loop" / "context" / "current.md"
        index = self.workdir / ".build-loop" / "context" / "index.json"
        self.assertTrue(current.is_file())
        self.assertTrue(index.is_file())
        self.assertIn("Build Loop Context Snapshot", current.read_text(encoding="utf-8"))
        self.assertIn("run validation", current.read_text(encoding="utf-8"))
        loaded_index = json.loads(index.read_text(encoding="utf-8"))
        snapshot_path = self.workdir / loaded_index["last_snapshot_path"]
        loaded_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded_snapshot["trigger"], "manual")
        self.assertEqual(loaded_snapshot["bootstrap_summary"]["codex_registry_hits"], 1)

    def test_if_changed_skips_identical_interval_snapshot(self) -> None:
        first = cs.build_snapshot(self.args("interval", if_changed=True))
        first_result = cs.write_snapshot(first, self.workdir, if_changed=True, retention=10)
        second = cs.build_snapshot(self.args("interval", if_changed=True))
        second_result = cs.write_snapshot(second, self.workdir, if_changed=True, retention=10)

        self.assertEqual(first_result["action"], "written")
        self.assertEqual(second_result["action"], "skipped")
        self.assertEqual(second_result["reason"], "unchanged")

    def test_agent_and_commit_triggers_append_sidecar_logs(self) -> None:
        for trigger in ("agent_dispatch", "agent_return", "pre_commit", "post_commit"):
            snapshot = cs.build_snapshot(self.args(trigger, commit_sha="abc123"))
            cs.write_snapshot(snapshot, self.workdir, if_changed=False, retention=10)

        context_dir = self.workdir / ".build-loop" / "context"
        briefs = (context_dir / "agent-briefs.jsonl").read_text(encoding="utf-8").splitlines()
        returns = (context_dir / "agent-returns.jsonl").read_text(encoding="utf-8").splitlines()
        commits = (context_dir / "commit-boundaries.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(briefs), 1)
        self.assertEqual(len(returns), 1)
        self.assertEqual(len(commits), 2)
        self.assertEqual(json.loads(commits[0])["commit_sha"], "abc123")


if __name__ == "__main__":
    unittest.main(verbosity=2)
