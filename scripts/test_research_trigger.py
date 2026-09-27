#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for research_trigger.py."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "research_trigger.py"


def run_trigger(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


class ResearchTriggerTests(unittest.TestCase):
    def test_request_replays_preserve_owner_and_authorization_boundary(self) -> None:
        cases = [
            ("Compare these worktrees and merge additive changes", False, "maintenance", "resume_requested_workflow"),
            ("Review latest local main and prune stale branches", False, "maintenance", "resume_requested_workflow"),
            ("Compare these worktrees and research the latest SDK before merging", True, "maintenance", "resume_requested_workflow"),
            ("Research how to build a terminal", True, "auto", "return_recommendation"),
            ("Research terminal approaches then implement the best option", True, "auto", "resume_requested_workflow"),
            ("Research terminal approaches and build it", True, "auto", "resume_requested_workflow"),
            ("Research terminal approaches, do not implement", True, "auto", "return_recommendation"),
            ("Research terminal approaches without implementing", True, "auto", "return_recommendation"),
            ("Research approaches and implement nothing until I approve", True, "auto", "return_recommendation"),
            ("Compare worktrees and merge later; read-only for now", False, "maintenance", "return_recommendation"),
            ("Compare API libraries for a worktree dashboard", True, "auto", "return_recommendation"),
            ("Compare our main competitors' onboarding", True, "auto", "return_recommendation"),
            ("Review the main approaches to caching", False, "auto", "return_recommendation"),
            ("Compare repository patterns for a compiler", True, "auto", "return_recommendation"),
            ("Research the SDK and build a recommendation", True, "auto", "return_recommendation"),
            ("Research why rebases and merge conflicts recur", True, "auto", "return_recommendation"),
            ("Evaluate the 'research then build' workflow", True, "auto", "return_recommendation"),
            ('Evaluate the "research then implement it" workflow', True, "auto", "return_recommendation"),
            ("Compare these worktrees", False, "maintenance", "return_recommendation"),
            ("Review the branches", False, "maintenance", "return_recommendation"),
            ("Review the main branch", False, "maintenance", "return_recommendation"),
            ("Review origin/main", False, "maintenance", "return_recommendation"),
            ("Merge latest main", False, "maintenance", "resume_requested_workflow"),
            ("Merge latest main into this branch", False, "maintenance", "resume_requested_workflow"),
            ("Compare with current main", False, "maintenance", "return_recommendation"),
            ("Compare these branches against latest main", False, "maintenance", "return_recommendation"),
            ("Reconcile this repo", False, "maintenance", "resume_requested_workflow"),
            ("Research SDK options and build it; follow 'do not implement' for now", True, "auto", "return_recommendation"),
        ]
        for task, required, context, continuation in cases:
            with self.subTest(task=task), tempfile.TemporaryDirectory() as td:
                result = run_trigger("--workdir", td, "--task", task, "--effort", "M", "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["research_required"], required)
                self.assertEqual(payload["request_context"], context)
                self.assertEqual(payload["continuation"], continuation)
                self.assertFalse(payload["continuation_is_authorization"])

    def test_maintenance_preserves_external_clauses_without_product_allowlist(self) -> None:
        for task in [
            "Reconcile branches and upgrade to the latest React",
            "merge the worktrees and bump Node to the newest LTS",
            "Review latest local main and compare the current browser compatibility",
            "Reconcile branches and research today's browser compatibility",
        ]:
            with self.subTest(task=task), tempfile.TemporaryDirectory() as td:
                result = run_trigger("--workdir", td, "--task", task, "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["request_context"], "maintenance")
                self.assertTrue(payload["research_required"])
                self.assertIn("current_external", payload["triggers"])
                self.assertTrue(payload["requires_citations_or_unavailable_note"])
                self.assertTrue(payload["blocks_final_claims"])

    def test_explicit_maintenance_context_preserves_review_only_stopping_point(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_trigger("--workdir", td, "--task", "Compare these worktrees",
                                 "--context", "maintenance", "--json")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["continuation"], "return_recommendation")
            self.assertFalse(payload["continuation_is_authorization"])

    def test_explicit_context_preserves_build_but_negation_takes_precedence(self) -> None:
        for task, continuation in [
            ("Research current SDK options", "resume_requested_workflow"),
            ("Research current SDK options; do not implement", "return_recommendation"),
        ]:
            with self.subTest(task=task), tempfile.TemporaryDirectory() as td:
                result = run_trigger("--workdir", td, "--task", task, "--context", "build", "--json")
                payload = json.loads(result.stdout)
                self.assertEqual(payload["continuation"], continuation)
                self.assertTrue(payload["requires_citations_or_unavailable_note"])

    def test_research_context_does_not_infer_authority_from_quoted_next_steps(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_trigger("--workdir", td, "--task", "Evaluate the 'research then build' workflow",
                                 "--context", "research", "--json")
            self.assertEqual(json.loads(result.stdout)["continuation"], "return_recommendation")

    def test_maintenance_keeps_citations_for_explicit_external_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_trigger("--workdir", td, "--task",
                                 "Reconcile branches; look up the latest browser compatibility before merging", "--json")
            payload = json.loads(result.stdout)
            self.assertTrue(payload["requires_citations_or_unavailable_note"])
            self.assertTrue(payload["blocks_final_claims"])
            self.assertIn("current_external", payload["triggers"])

    def test_novel_integration_records_research_packet_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_trigger(
                "--workdir", td,
                "--task", "Add Stripe API checkout integration",
                "--effort", "M",
                "--json",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["research_required"])
        self.assertEqual(payload["depth"], "standard")
        self.assertIn("new_dependency", payload["triggers"])
        self.assertIn(".build-loop/research/", payload["packet_path"])
        self.assertTrue(payload["requires_citations_or_unavailable_note"])

    def test_trivial_local_edit_does_not_trigger_research(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_trigger(
                "--workdir", td,
                "--task", "Rename local helper variable in one test",
                "--effort", "XS",
                "--json",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["research_required"])
        self.assertEqual(payload["depth"], "none")
        self.assertIsNone(payload["packet_path"])
        self.assertFalse(payload["blocks_final_claims"])

    def test_current_external_api_blocks_uncited_final_claims(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".build-loop").mkdir()
            result = run_trigger(
                "--workdir", td,
                "--task", "Use the latest OpenAI Responses API behavior",
                "--effort", "S",
                "--cache-into-state",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["research_required"])
            self.assertEqual(payload["depth"], "standard")
            self.assertIn("current_external", payload["triggers"])
            self.assertTrue(payload["blocks_final_claims"])
            self.assertTrue(payload["requires_citations_or_unavailable_note"])

            state = json.loads((root / ".build-loop" / "state.json").read_text())
            self.assertEqual(state["researchGate"]["packet_path"], payload["packet_path"])

    def test_large_research_architecture_task_escalates_to_deep(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_trigger(
                "--workdir", td,
                "--task", "Evaluate memory architecture options for future use",
                "--effort", "L",
                "--json",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["depth"], "deep")
        self.assertEqual(payload["memory_recall_depth"], "deep")


if __name__ == "__main__":
    unittest.main(verbosity=2)
