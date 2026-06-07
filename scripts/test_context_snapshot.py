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
        # P0: current.md now uses the v2 "Working Context" title to advertise
        # the pointer-dense + memory-backlinks shape to load_current.py.
        self.assertIn("Build Loop Working Context", current.read_text(encoding="utf-8"))
        # next_action is mandatory: it's the single most important resume signal.
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


# ===========================================================================
# Pillar 0 — short-term working-context layer guarantees
# ===========================================================================


class ContextSnapshotPillar0Tests(EnvIsolationMixin, unittest.TestCase):
    """Verify the P0 contract on the current.md rewrite."""

    # ---- current.md is now pointer-dense (no forbidden ## Context Quality dump) ----
    def test_current_md_drops_legacy_context_quality_section(self) -> None:
        args = self.args("manual")
        snapshot = cs.build_snapshot(args)
        text = cs.current_markdown(snapshot)
        self.assertNotIn("## Context Quality", text)
        # The new v2 title is required by the loader's marker.
        self.assertTrue(text.startswith("# Build Loop Working Context"))

    # ---- pointer-density lint: a hand-crafted bloated doc trips findings ----
    def test_pointer_density_lint_flags_inlined_validation_commands(self) -> None:
        bloated = (
            "# Build Loop Working Context\n\n"
            "- Updated: now\n\n"
            "## Validation\n\n"
            "- Command: `do thing 1`\n"
            "- Command: `do thing 2`\n"
        )
        findings = cs.pointer_density_findings(bloated)
        self.assertTrue(any("inlined_validation_commands" in f for f in findings))

    def test_pointer_density_lint_flags_overlong_doc(self) -> None:
        too_many_lines = "\n".join(["# Build Loop Working Context"] + [f"- line {i}" for i in range(120)])
        findings = cs.pointer_density_findings(too_many_lines)
        self.assertTrue(any("too_many_lines" in f for f in findings))

    def test_pointer_density_lint_accepts_real_current_md(self) -> None:
        args = self.args("manual")
        snapshot = cs.build_snapshot(args)
        text = cs.current_markdown(snapshot)
        findings = cs.pointer_density_findings(text)
        # A fresh, well-formed write must lint clean.
        self.assertEqual(findings, [], f"density findings on canonical write: {findings}")

    # ---- ## Memory Backlinks section is always present (graceful when empty) ----
    def test_current_md_always_contains_memory_backlinks_section(self) -> None:
        args = self.args("manual")
        snapshot = cs.build_snapshot(args)
        text = cs.current_markdown(snapshot)
        self.assertIn("## Memory Backlinks", text)
        # The empty-state pointer must be the explicit graceful line.
        if not snapshot.get("memory_backlinks"):
            self.assertIn("prior_art empty", text)

    # ---- memory_backlinks_from_packet projects P4 surfaces correctly ----
    def test_memory_backlinks_projects_prior_art_decisions(self) -> None:
        packet = {
            "project": "build-loop",
            "prior_art": {
                "decisions": [
                    {
                        "title": "Pick MLX for embeddings",
                        "path": "projects/build-loop/decisions/2026-06-07-mlx.md",
                        "project": "build-loop",
                        "snippet": "Mac-only OK; perf > Ollama",
                    },
                ],
                "implementations": [
                    {
                        "source": "scripts/semantic_index/hybrid.py",
                        "project": "atomize-news",
                        "snippet": "hybrid vector+sparse w/ RRF",
                    },
                ],
            },
            "lessons_progressive": [
                {
                    "name": "memory closeout mandatory",
                    "source_path": "lessons/closeout.md",
                    "description": "write at end of every run",
                }
            ],
        }
        links = cs.memory_backlinks_from_packet(packet, max_links=8)
        self.assertEqual(links[0]["kind"], "decision")
        self.assertEqual(links[0]["title"], "Pick MLX for embeddings")
        kinds = {l["kind"] for l in links}
        self.assertIn("implementation", kinds)
        self.assertIn("lesson", kinds)

    def test_memory_backlinks_empty_packet_is_safe(self) -> None:
        self.assertEqual(cs.memory_backlinks_from_packet({}), [])
        self.assertEqual(cs.memory_backlinks_from_packet(None), [])  # type: ignore[arg-type]

    # ---- warm read latency is recorded on every write (non-blocking) ----
    def test_write_records_warm_read_latency_ms(self) -> None:
        snapshot = cs.build_snapshot(self.args("manual"))
        result = cs.write_snapshot(snapshot, self.workdir, if_changed=False, retention=10)
        self.assertIn("warm_read_latency_ms", result)
        # Local FS read of a small file must succeed and produce a real number.
        self.assertIsNotNone(result["warm_read_latency_ms"])
        self.assertGreaterEqual(result["warm_read_latency_ms"], 0.0)

        index = json.loads((self.workdir / ".build-loop" / "context" / "index.json").read_text())
        self.assertIn("warm_read_latency_ms", index)
        self.assertIn("memory_backlinks_count", index)
        self.assertIn("pointer_density_findings", index)
        self.assertIn("current_md_lines", index)

    # ---- changed files cap (10) — pointer-dense rule, was 20 in v1 ----
    def test_changed_files_capped_at_10_inline(self) -> None:
        # Fake a snapshot with many changed files.
        snapshot = {
            "schema_version": 1,
            "snapshot_id": "ctx-test",
            "generated_at": "2026-06-07T00:00:00+00:00",
            "trigger": "manual",
            "project": "build-loop",
            "phase": "execute",
            "run_id": "r",
            "build_loop_id": "bl",
            "agent": None,
            "chunk_id": None,
            "status": None,
            "message": "x",
            "next_action": "y",
            "git": {
                "branch": "main",
                "head": "abc",
                "dirty_count": 25,
                "changed_files": [f"file_{i}.py" for i in range(25)],
            },
            "validation": {"commands": [], "result": None},
            "bootstrap_summary": {},
            "memory_backlinks": [],
        }
        text = cs.current_markdown(snapshot)
        # 10 listed + 1 "+15 more" line; never 25 raw paths.
        self.assertIn("(+15 more — see snapshot JSON)", text)
        for n in range(11, 25):
            self.assertNotIn(f"file_{n}.py", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
