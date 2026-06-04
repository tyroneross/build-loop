#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""End-to-end integration test: session lifecycle (bootstrap → surface → preference → gate).

Proves that the pieces CONNECT:
  context_bootstrap.build_packet()  →  queues + lessons_progressive + session_prefs
  write_session_prefs()             →  persists into state.json
  should_continue_into_queues()     →  reads back correctly
  pending_queue_items()             →  counts issues + backlog

Zero external deps required — EMBED_BACKEND_UNAVAILABLE=1 forces FTS-only mode.
No Postgres, no MLX, no Ollama.

Run:
  uv run pytest scripts/test_session_lifecycle_integration.py -v
"""
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ISSUE_BODY = """\
---
title: Login button unresponsive on mobile Safari
classify: SAFE
effort: S
status: open
---

Login button tap has no effect on iOS 17.4 + Safari. Repros 100% in landscape.
Root cause suspected: touch event handler not bound on mount.
"""

BACKLOG_BODY = """\
---
title: Add dark-mode toggle to settings page
classify: SAFE
effort: M
status: open
---

Users have requested a dark-mode switch. Currently only system preference is
honoured. Implement an explicit toggle that overrides the system preference and
persists via localStorage.
"""

LESSON_BODY = """\
---
name: safari-touch-event-fix
description: Bind touch handlers after mount to fix iOS Safari tap regression
type: lesson
---

# Safari Touch Event Fix

On iOS 17+ / Safari, touch events must be bound after the component mounts.
Attach the handler in componentDidMount / useEffect, not during render.
"""


class SessionLifecycleIntegrationTest(unittest.TestCase):
    """Integration test: bootstrap -> surface payload -> preference -> gate."""

    def setUp(self) -> None:
        # Force FTS-only mode — no embedding backend needed.
        os.environ["EMBED_BACKEND_UNAVAILABLE"] = "1"

        # Capture env overrides so we can restore them in tearDown.
        self._prev_env = {
            k: os.environ.get(k)
            for k in (
                "AGENT_MEMORY_ROOT",
                "BUILD_LOOP_MEMORY_ROOT",
                "BUILD_LOOP_MEMORY_STORE_ROOT",
                "CODEX_MEMORY_ROOT",
                "EMBED_BACKEND_UNAVAILABLE",
            )
        }

        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)

        self.workdir = tmp / "repo"
        self.workdir.mkdir()

        self.memroot = tmp / "build-loop-memory"
        self.memroot.mkdir()

        self.codex_root = tmp / "codex-memory"
        self.codex_root.mkdir()

        # Point memory env vars at the tmp tree so no real memory is accessed.
        os.environ["AGENT_MEMORY_ROOT"] = str(self.memroot)
        os.environ.pop("BUILD_LOOP_MEMORY_ROOT", None)
        os.environ.pop("BUILD_LOOP_MEMORY_STORE_ROOT", None)
        os.environ["CODEX_MEMORY_ROOT"] = str(self.codex_root)

        self._plant_fixtures()

    def tearDown(self) -> None:
        for key, val in self._prev_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # Fixture writers
    # ------------------------------------------------------------------

    def _plant_fixtures(self) -> None:
        """Create .build-loop queue items + a lesson in the memory store."""
        bl = self.workdir / ".build-loop"
        bl.mkdir()

        # issues/ dir with one item
        issues_dir = bl / "issues"
        issues_dir.mkdir()
        (issues_dir / "2026-05-30-safari-tap.md").write_text(ISSUE_BODY, encoding="utf-8")

        # backlog/ dir with one item
        backlog_dir = bl / "backlog"
        backlog_dir.mkdir()
        (backlog_dir / "2026-05-30-dark-mode.md").write_text(BACKLOG_BODY, encoding="utf-8")

        # Plant a lesson in the memory store (top-level lessons lane).
        lessons_dir = self.memroot / "lessons"
        lessons_dir.mkdir(parents=True)
        (lessons_dir / "safari_touch_fix.md").write_text(LESSON_BODY, encoding="utf-8")

        # Minimal MEMORY.md so canonical_memory_files doesn't warn "no files present".
        (self.memroot / "MEMORY.md").write_text(
            "# Build-loop memory\nTest fixture.\n", encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_queue_counts_in_envelope(self) -> None:
        """bootstrap packet contains correct issues and backlog counts."""
        packet = cb.build_packet(
            workdir=self.workdir,
            query="safari touch mobile fix",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        queues = packet.get("queues", {})
        self.assertEqual(queues.get("issues", {}).get("count"), 1,
                         "issues.count should be 1 (one planted issue)")
        self.assertEqual(queues.get("backlog", {}).get("count"), 1,
                         "backlog.count should be 1 (one planted backlog item)")

    def test_queue_top_titles_in_envelope(self) -> None:
        """top[] lists contain the planted items' titles from frontmatter."""
        packet = cb.build_packet(
            workdir=self.workdir,
            query="safari touch",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        queues = packet.get("queues", {})

        issues_top = queues.get("issues", {}).get("top", [])
        self.assertTrue(
            any("Login button" in item.get("title", "") for item in issues_top),
            f"Expected 'Login button' in issues top titles, got: {issues_top}",
        )

        backlog_top = queues.get("backlog", {}).get("top", [])
        self.assertTrue(
            any("dark-mode" in item.get("title", "").lower() for item in backlog_top),
            f"Expected 'dark-mode' in backlog top titles, got: {backlog_top}",
        )

    def test_lessons_progressive_non_empty(self) -> None:
        """lessons_progressive list is non-empty and includes the planted lesson."""
        packet = cb.build_packet(
            workdir=self.workdir,
            query="safari touch event fix ios",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        lessons = packet.get("lessons_progressive", [])
        # POSITIVE-PATH PROOF (no escape hatch): the fixture plants a lesson in
        # the exact path top_level_lessons_dir() resolves to under AGENT_MEMORY_ROOT,
        # using SQLite FTS only (EMBED_BACKEND_UNAVAILABLE=1). If ingest+query is
        # broken, this MUST fail — an empty list is not acceptable here.
        reasons = packet.get("sources", {}).get("canonical_memory", {}).get("reasons", [])
        self.assertGreater(
            len(lessons), 0,
            f"lessons_progressive must be non-empty — planted lesson was not indexed "
            f"(reasons={reasons})",
        )
        names = [l.get("name", "") for l in lessons]
        self.assertTrue(
            any("safari" in n.lower() for n in names),
            f"Expected 'safari' lesson in progressive list, got names: {names}",
        )

    def test_agent_brief_contains_queue_counts(self) -> None:
        """agent_brief one-liner mentions the issue and backlog counts."""
        packet = cb.build_packet(
            workdir=self.workdir,
            query="mobile safari fix",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        brief = packet.get("agent_brief", "")
        self.assertIn("#issues=1", brief, f"agent_brief missing #issues=1: {brief[:400]}")
        self.assertIn("#backlog=1", brief, f"agent_brief missing #backlog=1: {brief[:400]}")

    def test_session_prefs_block_present_with_defaults(self) -> None:
        """session_prefs block is present and defaults to 'ask'."""
        packet = cb.build_packet(
            workdir=self.workdir,
            query="fix safari tap",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        prefs = packet.get("session_prefs", {})
        self.assertIsInstance(prefs, dict, "session_prefs must be a dict")
        self.assertIn(
            prefs.get("continue_from_queues"),
            ("ask", "always", "never"),
            f"continue_from_queues must be one of ask/always/never, got: {prefs}",
        )
        # No state.json.session_prefs written yet → default is "ask".
        self.assertEqual(prefs.get("continue_from_queues"), "ask")
        self.assertEqual(prefs.get("source"), "default")

    def test_continuation_gate_no_pref_returns_true_default_flip(self) -> None:
        """SHIPPED DEFAULT (2026-06-04): no preference set → True so the end-of-run
        backlog/issues drain runs automatically. Reversible per-repo via
        ``continue_from_queues: "never"`` in .build-loop/config.json."""
        self.assertTrue(
            cb.should_continue_into_queues(self.workdir),
            "Gate must return True when no preference has been set (default-flip)",
        )

    def test_continuation_gate_after_write_always_returns_true(self) -> None:
        """After write_session_prefs('always'), gate returns True."""
        cb.write_session_prefs(self.workdir, "always", source="asked")
        self.assertTrue(
            cb.should_continue_into_queues(self.workdir),
            "Gate must return True after writing 'always'",
        )

    def test_continuation_gate_never_returns_false(self) -> None:
        """After write_session_prefs('never'), gate returns False."""
        cb.write_session_prefs(self.workdir, "never", source="asked")
        self.assertFalse(
            cb.should_continue_into_queues(self.workdir),
            "Gate must return False after writing 'never'",
        )

    def test_pending_queue_items_after_write_always(self) -> None:
        """pending_queue_items returns correct counts for issues + backlog."""
        cb.write_session_prefs(self.workdir, "always", source="asked")
        # Gate must be True so the continuation would run.
        self.assertTrue(cb.should_continue_into_queues(self.workdir))
        pending = cb.pending_queue_items(self.workdir)
        self.assertEqual(pending.get("issues"), 1, f"Expected 1 issue, got: {pending}")
        self.assertEqual(pending.get("backlog"), 1, f"Expected 1 backlog item, got: {pending}")
        # Combined count > 0 means the continuation loop should enter.
        self.assertGreater(
            pending["issues"] + pending["backlog"], 0,
            "pending sum must be > 0 so the continuation loop would enter",
        )

    def test_write_session_prefs_persists_and_read_session_prefs_reads_back(self) -> None:
        """write_session_prefs persists; read_session_prefs reads back correctly."""
        cb.write_session_prefs(self.workdir, "always", source="asked")
        prefs = cb.read_session_prefs(self.workdir)
        self.assertEqual(prefs["continue_from_queues"], "always")
        self.assertEqual(prefs["source"], "asked")
        self.assertIsNotNone(prefs["set_at"])

    def test_full_end_to_end_bootstrap_to_gate(self) -> None:
        """Full pipeline: bootstrap → check gate False → write pref → gate True + pending."""
        # 1. Bootstrap returns expected envelope fields simultaneously.
        packet = cb.build_packet(
            workdir=self.workdir,
            query="mobile safari login fix",
            codex_memory_root=self.codex_root,
            include_postgres=False,
            include_rally=False,
        )
        self.assertEqual(packet["queues"]["issues"]["count"], 1)
        self.assertEqual(packet["queues"]["backlog"]["count"], 1)
        # Non-empty (not just a list): the planted lesson must be retrieved end-to-end.
        self.assertGreater(
            len(packet.get("lessons_progressive", [])), 0,
            "end-to-end lessons_progressive must be non-empty (planted lesson)",
        )
        self.assertIsInstance(packet.get("agent_brief"), str)
        self.assertIsInstance(packet.get("session_prefs"), dict)

        # agent_brief must surface queue counts.
        brief = packet["agent_brief"]
        self.assertIn("#issues=1", brief)
        self.assertIn("#backlog=1", brief)

        # 2. Gate is True before preference is written (SHIPPED DEFAULT 2026-06-04
        #    — source="default" → auto-drain). Reversible via continue_from_queues:"never".
        self.assertTrue(cb.should_continue_into_queues(self.workdir))

        # 3. Write "always" → gate stays True AND pending shows items.
        cb.write_session_prefs(self.workdir, "always", source="asked")
        self.assertTrue(cb.should_continue_into_queues(self.workdir))
        pending = cb.pending_queue_items(self.workdir)
        self.assertGreater(pending["issues"] + pending["backlog"], 0)

    def test_no_external_deps_required(self) -> None:
        """Entire pipeline runs without Postgres/MLX/Ollama — stdlib only.

        Verified by EMBED_BACKEND_UNAVAILABLE=1 set in setUp.
        If this test passes, the fresh-install path is confirmed.
        """
        # Confirm the env flag is set.
        self.assertEqual(os.environ.get("EMBED_BACKEND_UNAVAILABLE"), "1")
        # Full packet must succeed without raising.
        try:
            packet = cb.build_packet(
                workdir=self.workdir,
                query="safari",
                codex_memory_root=self.codex_root,
                include_postgres=False,
                include_rally=False,
            )
        except Exception as exc:  # noqa: BLE001
            self.fail(f"build_packet raised unexpectedly: {exc}")
        # Must still have the core envelope fields.
        for field in ("queues", "lessons_progressive", "session_prefs", "agent_brief"):
            self.assertIn(field, packet, f"envelope missing '{field}'")


if __name__ == "__main__":
    unittest.main()
