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
from project_resolver import resolve_project as cb_resolve_project  # noqa: E402


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
    def test_ensure_root_constitution_seeds_template_when_missing(self) -> None:
        target = self.memroot / "constitution.md"
        self.assertFalse(target.exists())

        reasons = cb.ensure_root_constitution(self.memroot)

        self.assertTrue(target.exists())
        self.assertTrue(any(reason.startswith("constitution_seeded:") for reason in reasons))
        self.assertIn("Build-Loop Constitution", target.read_text(encoding="utf-8"))

    def test_ensure_root_constitution_never_overwrites_existing_file(self) -> None:
        target = self.memroot / "constitution.md"
        target.write_text("custom rules stay\n", encoding="utf-8")

        reasons = cb.ensure_root_constitution(self.memroot)

        self.assertEqual(reasons, [])
        self.assertEqual(target.read_text(encoding="utf-8"), "custom rules stay\n")

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


class ReferenceFreshnessTests(EnvIsolationMixin, unittest.TestCase):
    """Activation tests: a captured reference past its horizon surfaces as stale
    in the read path (context bootstrap), routed through the env-overridden store.
    """

    def _capture(self, *, topic: str, retrieved_at: str | None = None,
                 content_class: str | None = None,
                 refresh_after_days: int | None = None) -> None:
        from reference_capture import capture_reference  # local import (env-set)

        capture_reference(
            workdir=self.workdir,
            topic=topic,
            findings="extracted finding body",
            source_urls=["https://docs.example.com/x"],
            informed_decision="informed a build decision",
            run_id="activation_run",
            retrieved_at=retrieved_at,
            content_class=content_class,
            refresh_after_days=refresh_after_days,
        )

    def test_fresh_reference_not_flagged_stale(self) -> None:
        # A reference captured today is fresh — no stale flag.
        self._capture(topic="fresh thing today")
        packet = cb.build_packet(
            workdir=self.workdir, query="thing",
            codex_memory_root=self.codex_root,
            include_postgres=False, include_rally=False,
        )
        refresh = packet["reference_freshness"]
        self.assertTrue(refresh["exists"])
        self.assertEqual(refresh["stale_count"], 0)
        self.assertEqual(refresh["total"], 1)
        # Brief does NOT carry a stale line when nothing is stale.
        self.assertNotIn("stale-needs-refresh", packet["agent_brief"])

    def test_backdated_reference_flagged_stale_in_packet_and_brief(self) -> None:
        from datetime import date, timedelta

        old = (date.today() - timedelta(days=120)).isoformat()
        # 120 days old, api-docs 7-day horizon → unambiguously stale.
        self._capture(topic="stale api endpoint reference",
                      retrieved_at=old, content_class="api-docs")
        packet = cb.build_packet(
            workdir=self.workdir, query="endpoint",
            codex_memory_root=self.codex_root,
            include_postgres=False, include_rally=False,
        )
        refresh = packet["reference_freshness"]
        self.assertEqual(refresh["stale_count"], 1)
        self.assertEqual(refresh["stale"][0]["content_class"], "api-docs")
        self.assertGreater(refresh["stale"][0]["days_overdue"], 0)
        # The read-path brief surfaces it (advisory, not an AskUserQuestion).
        self.assertIn("stale-needs-refresh", packet["agent_brief"])
        self.assertIn("Reference corpus:", packet["agent_brief"])

    def test_no_reference_lane_degrades_quietly(self) -> None:
        packet = cb.build_packet(
            workdir=self.workdir, query="x",
            codex_memory_root=self.codex_root,
            include_postgres=False, include_rally=False,
        )
        refresh = packet["reference_freshness"]
        self.assertFalse(refresh["exists"])
        self.assertEqual(refresh["stale_count"], 0)
        self.assertNotIn("stale-needs-refresh", packet["agent_brief"])


class QueueContextTests(EnvIsolationMixin, unittest.TestCase):
    def _make_md(self, path: Path, title: str, body: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntitle: {title}\n---\n{body}\n", encoding="utf-8")

    def test_queue_count_and_top_titles(self) -> None:
        bl = self.workdir / ".build-loop"
        issues_dir = bl / "issues"
        backlog_dir = bl / "backlog"
        self._make_md(issues_dir / "iss1.md", "Fix login bug")
        self._make_md(issues_dir / "iss2.md", "Fix nav crash")
        self._make_md(backlog_dir / "bk1.md", "Add dark mode")

        result = cb.queue_context(self.workdir)

        self.assertEqual(result["issues"]["count"], 2)
        self.assertEqual(result["backlog"]["count"], 1)
        self.assertEqual(result["ux-queue"]["count"], 0)
        self.assertEqual(result["followup"]["count"], 0)
        self.assertEqual(result["proposals"]["count"], 0)

        issue_titles = [item["title"] for item in result["issues"]["top"]]
        self.assertIn("Fix login bug", issue_titles)
        backlog_titles = [item["title"] for item in result["backlog"]["top"]]
        self.assertIn("Add dark mode", backlog_titles)

    def test_missing_queue_dir_returns_zero(self) -> None:
        result = cb.queue_context(self.workdir)
        for qname in cb.QUEUE_NAMES:
            self.assertEqual(result[qname]["count"], 0)
            self.assertEqual(result[qname]["top"], [])

    def test_frontmatter_title_uses_name_fallback(self) -> None:
        """Files using 'name:' key in frontmatter also work."""
        md = self.workdir / ".build-loop" / "backlog" / "item.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text("---\nname: My backlog item\n---\nBody.\n", encoding="utf-8")
        result = cb.queue_context(self.workdir)
        self.assertEqual(result["backlog"]["top"][0]["title"], "My backlog item")

    def test_top_capped_at_three(self) -> None:
        issues_dir = self.workdir / ".build-loop" / "issues"
        for i in range(5):
            self._make_md(issues_dir / f"iss{i}.md", f"Issue {i}")
        result = cb.queue_context(self.workdir)
        self.assertEqual(result["issues"]["count"], 5)
        self.assertEqual(len(result["issues"]["top"]), 3)

    def test_queues_in_build_packet(self) -> None:
        bl = self.workdir / ".build-loop"
        (bl / "issues").mkdir(parents=True, exist_ok=True)
        (bl / "issues" / "iss1.md").write_text(
            "---\ntitle: Test issue\n---\nBody.\n", encoding="utf-8"
        )
        packet = cb.build_packet(
            workdir=self.workdir,
            query="test",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        self.assertIn("queues", packet)
        self.assertEqual(packet["queues"]["issues"]["count"], 1)
        self.assertEqual(packet["queues"]["issues"]["top"][0]["title"], "Test issue")
        # Queue summary in agent_brief
        self.assertIn("#issues=1", packet["agent_brief"])


class LessonsProgressiveTests(EnvIsolationMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        os.environ["EMBED_BACKEND_UNAVAILABLE"] = "1"

    def tearDown(self) -> None:
        os.environ.pop("EMBED_BACKEND_UNAVAILABLE", None)
        super().tearDown()

    def _make_lesson(self, name: str, description: str, body: str) -> None:
        lessons_dir = self.memroot / "lessons"
        lessons_dir.mkdir(parents=True, exist_ok=True)
        md = lessons_dir / f"{name}.md"
        md.write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
            encoding="utf-8",
        )

    def test_lessons_progressive_returns_results(self) -> None:
        # Positive-path proof (FTS-only, no embedding backend): a top-level
        # lesson MUST be retrieved for a matching query. This is the headline
        # progressive-disclosure contract, not just graceful degradation.
        self._make_lesson(
            "context_bootstrap_lesson",
            "Always run context bootstrap in Phase 1",
            "When Phase 1 starts, run context_bootstrap.py to load memory.",
        )
        results, reasons = cb.lessons_progressive_context(
            query="context bootstrap phase 1",
            project="_unscoped",
            workdir=self.workdir,
            limit=5,
        )
        names = [r["name"] for r in results]
        self.assertIn(
            "context_bootstrap_lesson", names,
            f"positive retrieval failed: expected the lesson, got {names} ({reasons})",
        )
        for r in results:
            for key in ("name", "description", "snippet", "score", "source_path"):
                self.assertIn(key, r)

    def test_lessons_progressive_covers_project_and_top_level(self) -> None:
        # Regression guard for the ingest-coverage bug: a named-project session
        # must retrieve BOTH the project's own lesson AND a cross-project
        # top-level lesson in one call (query scopes to project OR _unscoped,
        # so ingest must populate both lanes).
        proj = cb_resolve_project(self.workdir)
        # top-level (cross-project) lesson
        self._make_lesson(
            "global_rollback",
            "Keep a rollback path on every deploy",
            "Always retain a rollback target before deploying.",
        )
        # project-scoped lesson under projects/<proj>/lessons
        proj_lessons = self.memroot / "projects" / proj / "lessons"
        proj_lessons.mkdir(parents=True, exist_ok=True)
        (proj_lessons / "proj_caching.md").write_text(
            "---\nname: proj_caching\ndescription: Cache the rollback manifest per deploy\n---\n"
            "Persist the rollback manifest in the project cache.\n",
            encoding="utf-8",
        )
        results, reasons = cb.lessons_progressive_context(
            query="rollback deploy cache", project=proj, workdir=self.workdir, limit=10,
        )
        names = [r["name"] for r in results]
        self.assertIn("global_rollback", names,
                      f"top-level lesson missing: {names} ({reasons})")
        self.assertIn("proj_caching", names,
                      f"project lesson missing: {names} ({reasons})")

    def test_lessons_progressive_in_packet(self) -> None:
        self._make_lesson(
            "memory_lesson",
            "Load memory before planning",
            "Memory context is crucial for correct planning.",
        )
        packet = cb.build_packet(
            workdir=self.workdir,
            query="memory load planning",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        self.assertIn("lessons_progressive", packet)
        self.assertIsInstance(packet["lessons_progressive"], list)

    def test_lessons_degrade_when_import_fails(self) -> None:
        """If lessons_index is not importable, results must be [] with a reason."""
        import importlib
        import unittest.mock as mock
        # Patch the import inside the function.
        with mock.patch.dict("sys.modules", {"lessons_index": None}):
            results, reasons = cb.lessons_progressive_context(
                query="test",
                project="_unscoped",
                workdir=self.workdir,
                limit=5,
            )
        self.assertEqual(results, [])
        self.assertTrue(any("lessons_index" in r for r in reasons))

    def test_lessons_degrade_gracefully_on_empty_index(self) -> None:
        """When no memory files exist, results should be [] without crashing."""
        results, reasons = cb.lessons_progressive_context(
            query="anything",
            project="_unscoped",
            workdir=self.workdir,
            limit=5,
        )
        self.assertIsInstance(results, list)
        self.assertIsInstance(reasons, list)


class SessionPrefsTests(EnvIsolationMixin, unittest.TestCase):
    def test_default_when_absent(self) -> None:
        prefs = cb.read_session_prefs(self.workdir)
        self.assertEqual(prefs["continue_from_queues"], "ask")
        self.assertEqual(prefs["source"], "default")

    def test_write_then_read_roundtrip(self) -> None:
        (self.workdir / ".build-loop").mkdir(parents=True, exist_ok=True)
        cb.write_session_prefs(self.workdir, "always", source="asked")
        prefs = cb.read_session_prefs(self.workdir)
        self.assertEqual(prefs["continue_from_queues"], "always")
        self.assertEqual(prefs["source"], "asked")
        self.assertIsNotNone(prefs["set_at"])

    def test_write_never_roundtrip(self) -> None:
        (self.workdir / ".build-loop").mkdir(parents=True, exist_ok=True)
        cb.write_session_prefs(self.workdir, "never")
        prefs = cb.read_session_prefs(self.workdir)
        self.assertEqual(prefs["continue_from_queues"], "never")

    def test_config_override_wins_over_state(self) -> None:
        bl = self.workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        # Write state with "never"
        cb.write_session_prefs(self.workdir, "never", source="asked")
        # Write config with "always"
        config = bl / "config.json"
        config.write_text(
            json.dumps({"sessionPrefs": {"continueFromQueues": "always"}}),
            encoding="utf-8",
        )
        prefs = cb.read_session_prefs(self.workdir)
        self.assertEqual(prefs["continue_from_queues"], "always")
        self.assertEqual(prefs["source"], "config")

    def test_invalid_value_ignored_falls_to_default(self) -> None:
        bl = self.workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        # Bad value in state.json
        (bl / "state.json").write_text(
            json.dumps({"session_prefs": {"continue_from_queues": "badvalue"}}),
            encoding="utf-8",
        )
        prefs = cb.read_session_prefs(self.workdir)
        self.assertEqual(prefs["continue_from_queues"], "ask")
        self.assertEqual(prefs["source"], "default")

    def test_write_invalid_value_is_noop(self) -> None:
        """write_session_prefs with invalid value must not corrupt state.json."""
        bl = self.workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        existing = {"runs": [], "schema_version": "1.0.0"}
        (bl / "state.json").write_text(json.dumps(existing), encoding="utf-8")
        cb.write_session_prefs(self.workdir, "invalid_option")
        # State should be unchanged (no session_prefs key written).
        state = json.loads((bl / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("session_prefs", state)

    def test_session_prefs_in_packet(self) -> None:
        packet = cb.build_packet(
            workdir=self.workdir,
            query="test",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        self.assertIn("session_prefs", packet)
        self.assertIn(packet["session_prefs"]["continue_from_queues"], cb.SESSION_PREFS_VALID)

    def test_write_creates_state_json_if_absent(self) -> None:
        bl = self.workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        self.assertFalse((bl / "state.json").exists())
        cb.write_session_prefs(self.workdir, "always")
        self.assertTrue((bl / "state.json").exists())
        state = json.loads((bl / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["session_prefs"]["continue_from_queues"], "always")

    def test_config_invalid_json_falls_to_state(self) -> None:
        bl = self.workdir / ".build-loop"
        bl.mkdir(parents=True, exist_ok=True)
        cb.write_session_prefs(self.workdir, "never", source="asked")
        (bl / "config.json").write_text("{ not valid json", encoding="utf-8")
        prefs = cb.read_session_prefs(self.workdir)
        # Config failed to parse → fall through to state.json value
        self.assertEqual(prefs["continue_from_queues"], "never")
        self.assertEqual(prefs["source"], "asked")


class ContinuationGateTests(EnvIsolationMixin, unittest.TestCase):
    """Tests for should_continue_into_queues and pending_queue_items."""

    def _write_prefs(self, value: str) -> None:
        (self.workdir / ".build-loop").mkdir(parents=True, exist_ok=True)
        cb.write_session_prefs(self.workdir, value, source="asked")

    # --- should_continue_into_queues ---

    def test_always_returns_true(self) -> None:
        self._write_prefs("always")
        self.assertTrue(cb.should_continue_into_queues(self.workdir))

    def test_ask_returns_false(self) -> None:
        self._write_prefs("ask")
        self.assertFalse(cb.should_continue_into_queues(self.workdir))

    def test_never_returns_false(self) -> None:
        self._write_prefs("never")
        self.assertFalse(cb.should_continue_into_queues(self.workdir))

    def test_unset_returns_true_default_flip(self) -> None:
        """SHIPPED DEFAULT (2026-06-04): unset → True so backlog drain runs automatically.
        Reversible per-repo via ``continue_from_queues: "never"``."""
        # No .build-loop dir at all → source=="default" → auto-drain
        self.assertTrue(cb.should_continue_into_queues(self.workdir))

    # --- pending_queue_items ---

    def _make_issue(self, name: str) -> None:
        d = self.workdir / ".build-loop" / "issues"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(f"---\ntitle: {name}\n---\n", encoding="utf-8")

    def _make_backlog(self, name: str) -> None:
        d = self.workdir / ".build-loop" / "backlog"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(f"---\ntitle: {name}\n---\n", encoding="utf-8")

    def test_pending_counts_issues_and_backlog(self) -> None:
        self._make_issue("iss1.md")
        self._make_issue("iss2.md")
        self._make_backlog("bk1.md")
        counts = cb.pending_queue_items(self.workdir)
        self.assertEqual(counts["issues"], 2)
        self.assertEqual(counts["backlog"], 1)

    def test_pending_zero_when_dirs_absent(self) -> None:
        counts = cb.pending_queue_items(self.workdir)
        self.assertEqual(counts["issues"], 0)
        self.assertEqual(counts["backlog"], 0)

    def test_pending_keys_are_exactly_issues_and_backlog(self) -> None:
        counts = cb.pending_queue_items(self.workdir)
        self.assertEqual(set(counts.keys()), {"issues", "backlog"})

    # --- integration: gate + counts together ---

    def test_gate_true_and_pending_work_detected(self) -> None:
        """Simulates the end-of-run continuation check: always + items present."""
        self._write_prefs("always")
        self._make_issue("critical.md")
        self._make_backlog("nice-to-have.md")
        self.assertTrue(cb.should_continue_into_queues(self.workdir))
        counts = cb.pending_queue_items(self.workdir)
        self.assertGreater(counts["issues"] + counts["backlog"], 0)

    def test_gate_false_with_never_even_when_items_present(self) -> None:
        self._write_prefs("never")
        self._make_issue("critical.md")
        # Gate should still be False — items don't matter
        self.assertFalse(cb.should_continue_into_queues(self.workdir))


class DecisionQualityDoctrineTests(unittest.TestCase):
    """WP-C: phase-gated decision-quality doctrine injection."""

    def test_doctrine_loads_from_shipped_reference(self) -> None:
        dq = cb.decision_quality_doctrine()
        self.assertTrue(dq["exists"], dq.get("reason"))
        self.assertIn("Decision-Quality Doctrine", dq["text"])
        self.assertIn("Ground-truth before accepting any suggested fix", dq["text"])

    def test_doctrine_missing_reference_is_absence_tolerant(self) -> None:
        import context_bootstrap as _cb
        saved = _cb.DECISION_QUALITY_REF
        try:
            _cb.DECISION_QUALITY_REF = saved.parent / "does-not-exist.md"
            dq = _cb.decision_quality_doctrine()
            self.assertFalse(dq["exists"])
            self.assertEqual(dq["text"], "")
            self.assertIsNotNone(dq["reason"])
        finally:
            _cb.DECISION_QUALITY_REF = saved

    def test_agent_brief_marks_doctrine_compactly_when_present(self) -> None:
        # A present doctrine surfaces as a COMPACT one-line presence marker in
        # the brief — not the full 12-rule text (that rides in the packet at
        # decision_quality.text; inlining the full text blew the brief budget,
        # prior-art regression 2026-06-09).
        dq = cb.decision_quality_doctrine()
        if not dq["exists"]:
            self.skipTest("shipped doctrine reference absent")
        packet = {
            "project": "build-loop", "workdir": "/tmp", "query": "x",
            "decision_quality": dq, "queues": {},
            "sources": {
                "canonical_memory": {}, "repo_local": {}, "codex_memory": {}, "rally": {},
            },
        }
        brief = cb.agent_brief(packet)
        # The compact presence marker is in the brief...
        self.assertIn("Decision-quality doctrine: 12 rules loaded", brief)
        # ...but the full rule text is NOT inlined (the brief stays a digest).
        self.assertNotIn("Ground-truth before accepting any suggested fix", brief)
        # The full text remains available to the orchestrator via the packet.
        self.assertIn("Ground-truth before accepting any suggested fix", packet["decision_quality"]["text"])


class TestBacklogDiscoverability(unittest.TestCase):
    """Change 4 — Phase-1 backlog discoverability nudge (non-fatal, cheap).

    The production logic lives in context_bootstrap.backlog_discoverability and
    is consumed by agent_brief. Pure filesystem — no memory/env rigging needed.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        (self.repo / ".build-loop").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, kind: str, name: str = "x.md") -> None:
        d = self.repo / ".build-loop" / kind
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text("# item\n", encoding="utf-8")

    def _backlog_with_index(self) -> None:
        bdir = self.repo / ".build-loop" / "backlog"
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / "INDEX.md").write_text("# Backlog\n", encoding="utf-8")

    def test_adoptable_when_scattered_work_and_no_index(self):
        self._seed("followup")
        res = cb.backlog_discoverability(self.repo)
        self.assertTrue(res["adoptable"])
        self.assertIsNotNone(res["notice"])
        self.assertIn("adopt --dry-run", res["notice"])

    def test_not_adoptable_when_index_exists(self):
        self._seed("issues")
        self._backlog_with_index()
        res = cb.backlog_discoverability(self.repo)
        self.assertFalse(res["adoptable"])
        if res["notice"]:
            self.assertNotIn("adopt --dry-run", res["notice"])

    def test_gitignored_backlog_flagged_wont_travel(self):
        self._backlog_with_index()
        (self.repo / ".gitignore").write_text(".build-loop/\n", encoding="utf-8")
        res = cb.backlog_discoverability(self.repo)
        self.assertTrue(res["gitignored"])
        self.assertIn("won't travel", res["notice"])

    def test_gitignored_false_when_unignore_present(self):
        self._backlog_with_index()
        (self.repo / ".gitignore").write_text(
            ".build-loop/\n!/.build-loop/backlog/\n", encoding="utf-8")
        res = cb.backlog_discoverability(self.repo)
        self.assertFalse(res["gitignored"])

    def test_legacy_unignore_is_flagged_for_rooted_migration(self):
        self._backlog_with_index()
        (self.repo / ".gitignore").write_text(
            ".build-loop/\n!.build-loop/backlog/\n", encoding="utf-8")
        res = cb.backlog_discoverability(self.repo)
        self.assertTrue(res["gitignored"])
        self.assertIn("adopt --apply", res["notice"])

    def test_mixed_rooted_and_legacy_rules_still_require_migration(self):
        self._backlog_with_index()
        (self.repo / ".gitignore").write_text(
            ".build-loop/\n"
            "!/.build-loop/\n"
            "/.build-loop/*\n"
            "!/.build-loop/backlog/\n"
            "!/.build-loop/backlog/**\n"
            "!/BACKLOG.md\n"
            "!.build-loop/backlog/**\n",
            encoding="utf-8",
        )
        res = cb.backlog_discoverability(self.repo)
        self.assertTrue(res["gitignored"])
        self.assertIn("adopt --apply", res["notice"])

    def test_silent_when_nothing_to_surface(self):
        res = cb.backlog_discoverability(self.repo)
        self.assertIsNone(res["notice"])
        self.assertFalse(res["adoptable"])
        self.assertFalse(res["gitignored"])

    def test_never_raises_on_missing_workdir(self):
        ghost = Path(self._tmp.name) / "does-not-exist"
        res = cb.backlog_discoverability(ghost)  # must not raise
        self.assertIsNone(res["notice"])

    def test_notice_surfaces_in_agent_brief(self):
        self._seed("followup")
        packet = {
            "project": "repo", "workdir": str(self.repo), "query": "",
            "backlog_discoverability": cb.backlog_discoverability(self.repo),
            "sources": {"canonical_memory": {}, "repo_local": {},
                        "codex_memory": {}, "rally": {}},
        }
        brief = cb.agent_brief(packet)
        self.assertIn("adopt --dry-run", brief)


if __name__ == "__main__":
    unittest.main(verbosity=2)
