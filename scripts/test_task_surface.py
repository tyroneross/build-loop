#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for task_surface.py."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import task_surface as surface

SCRIPT = HERE / "task_surface.py"


def run_surface(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


class TaskSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workdir = self.root / "sample-repo"
        subprocess.run(["git", "init", "-q", str(self.workdir)], check=True)
        (self.workdir / ".build-loop").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_collects_active_state_and_local_queues(self) -> None:
        (self.workdir / ".build-loop" / "state.json").write_text(
            json.dumps(
                {
                    "execution": {
                        "queued_chunks": ["T-2"],
                        "in_flight_chunks": ["T-1"],
                        "item_iterations": {
                            "T-1": [
                                {
                                    "attempt": 1,
                                    "status": "failed",
                                    "phase": "iterate",
                                    "criterion": "tests",
                                    "stop_reason": "validator-failed",
                                    "recorded_at": "2026-06-12T12:00:00Z",
                                }
                            ]
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        issues = self.workdir / ".build-loop" / "issues"
        queue = self.workdir / ".build-loop" / "queue"
        followup = self.workdir / ".build-loop" / "followup"
        issues.mkdir()
        queue.mkdir()
        followup.mkdir()
        (queue / "outcome.md").write_text("# Complete known outcome\n", encoding="utf-8")
        (issues / "bug.md").write_text("# Fix stale watcher\n", encoding="utf-8")
        (followup / "later.md").write_text(
            "# Later\n\n- [ ] Add package privacy test\n",
            encoding="utf-8",
        )

        result = run_surface("--workdir", str(self.workdir), "--no-memory", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "derived-active-view-no-new-ledger")
        self.assertEqual(payload["counts_by_surface"]["state.in_flight_chunks"], 1)
        self.assertEqual(payload["counts_by_surface"]["state.queued_chunks"], 1)
        self.assertEqual(payload["counts_by_surface"]["issues"], 1)
        self.assertEqual(payload["counts_by_surface"]["queue"], 1)
        self.assertEqual(payload["counts_by_surface"]["followup"], 1)
        self.assertEqual(payload["dry_run"]["mode"], "rank-only")
        self.assertEqual(payload["dry_run"]["next_item"]["id"], "T-1")
        self.assertEqual(payload["dry_run"]["next_item"]["dry_run_action"], "continue_in_flight")
        self.assertEqual(payload["dry_run"]["next_item"]["rank"], 1)
        self.assertEqual(payload["iteration_summary"]["T-1"]["attempts"], 1)
        self.assertEqual(payload["iteration_summary"]["T-1"]["stop_reason"], "validator-failed")

    def test_structured_execution_items_preserve_title_owner_and_source(self) -> None:
        (self.workdir / ".build-loop" / "state.json").write_text(
            json.dumps({"execution": {"queued_chunks": [{
                "chunk_id": "T-2",
                "title": "Render open work",
                "owner": "dashboard-agent",
            }]}}),
            encoding="utf-8",
        )

        payload = surface.collect_task_surface(
            workdir=self.workdir,
            include_memory=False,
        )

        row = payload["items"][0]
        self.assertEqual(row["id"], "T-2")
        self.assertEqual(row["title"], "Render open work")
        self.assertEqual(row["owner"], "dashboard-agent")
        self.assertEqual(row["created_by"], "Build Loop")

    def test_memory_backlog_is_project_scoped(self) -> None:
        memory = self.root / "memory"
        build_loop = memory / "projects" / "sample-repo"
        sibling = memory / "projects" / "other-repo"
        build_loop.mkdir(parents=True)
        sibling.mkdir(parents=True)
        (build_loop / "backlog.md").write_text(
            "# Backlog\n\n- [ ] Ship guided memory install\n",
            encoding="utf-8",
        )
        (sibling / "backlog.md").write_text(
            "# Other\n\n- [ ] Do not include me\n",
            encoding="utf-8",
        )

        result = run_surface(
            "--workdir", str(self.workdir),
            "--memory-root", str(memory),
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        titles = [row["title"] for row in payload["items"]]
        self.assertIn("Ship guided memory install", titles)
        self.assertNotIn("Do not include me", titles)

    def test_status_current_is_surfaced_from_canonical_status(self) -> None:
        memory = self.root / "memory"
        status_dir = memory / "projects" / "sample-repo" / "status"
        status_dir.mkdir(parents=True)
        (status_dir / "CURRENT.md").write_text(
            "# Status\n\n## Current open work (ranked)\n"
            "1. **Async recordComposite** (P2)\n"
            "2. Reconcile docs\n\n## Links\n- x\n",
            encoding="utf-8",
        )

        result = run_surface(
            "--workdir", str(self.workdir),
            "--memory-root", str(memory),
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["counts_by_surface"]["status_current"], 2)
        titles = [row["title"] for row in payload["items"]]
        self.assertIn("Async recordComposite (P2)", titles)
        status_rows = [r for r in payload["items"] if r["surface"] == "status_current"]
        self.assertEqual(status_rows[0]["dry_run_action"], "address_status_item")

    def test_proposals_are_opt_in(self) -> None:
        proposals = self.workdir / ".build-loop" / "proposals"
        proposals.mkdir()
        (proposals / "candidate.md").write_text("# Candidate task\n", encoding="utf-8")

        default = run_surface("--workdir", str(self.workdir), "--no-memory", "--json")
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertNotIn("proposals", json.loads(default.stdout)["counts_by_surface"])

        opt_in = run_surface(
            "--workdir", str(self.workdir),
            "--no-memory",
            "--include-proposals",
            "--json",
        )
        self.assertEqual(opt_in.returncode, 0, opt_in.stderr)
        self.assertEqual(json.loads(opt_in.stdout)["counts_by_surface"]["proposals"], 1)

    def test_backlog_surface_excludes_done_archive_and_index(self) -> None:
        backlog = self.workdir / ".build-loop" / "backlog"
        items = backlog / "items"
        archive = backlog / "archive"
        items.mkdir(parents=True)
        archive.mkdir()
        (backlog / "INDEX.md").write_text("# Backlog\n\n- Active items: 0\n", encoding="utf-8")
        (items / "open.md").write_text(
            "---\nstatus: open\n---\n# Ship active backlog item\n",
            encoding="utf-8",
        )
        (items / "done.md").write_text(
            "---\nstatus: done\n---\n# Completed backlog item\n",
            encoding="utf-8",
        )
        (items / "dropped.md").write_text(
            "---\nstatus: dropped\n---\n# Dropped backlog item\n",
            encoding="utf-8",
        )
        (archive / "old.md").write_text("# Archived backlog item\n", encoding="utf-8")

        result = run_surface("--workdir", str(self.workdir), "--no-memory", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["counts_by_surface"]["backlog"], 1)
        backlog_row = next(row for row in payload["items"] if row["surface"] == "backlog")
        self.assertFalse(backlog_row["execution_eligible"])
        self.assertEqual(backlog_row["pickup_policy"], "promote-at-planning-boundary")
        self.assertEqual(payload["execution_queue_count"], 0)
        self.assertIsNone(payload["dry_run"]["next_item"])
        titles = [row["title"] for row in payload["items"]]
        self.assertIn("Ship active backlog item", titles)
        self.assertNotIn("Completed backlog item", titles)
        self.assertNotIn("Dropped backlog item", titles)
        self.assertNotIn("Archived backlog item", titles)
        self.assertNotIn("Backlog", titles)

    def test_terminal_status_is_excluded_from_active_surfaces(self) -> None:
        issues = self.workdir / ".build-loop" / "issues"
        issues.mkdir()
        (issues / "open.md").write_text(
            "---\nstatus: open\nowner: reviewer\nsource: Agent Rally\n---\n# Open issue\n",
            encoding="utf-8",
        )
        (issues / "done.md").write_text(
            "---\nstatus: done\n---\n# Completed issue\n",
            encoding="utf-8",
        )

        payload = surface.collect_task_surface(workdir=self.workdir, include_memory=False)

        self.assertEqual(payload["counts_by_surface"]["issues"], 1)
        row = next(item for item in payload["items"] if item["surface"] == "issues")
        self.assertEqual(row["title"], "Open issue")
        self.assertEqual(row["owner"], "reviewer")
        self.assertEqual(row["created_by"], "Agent Rally")

    def test_operations_center_is_repo_scoped_and_fail_soft(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps([
            {
                "id": "oc-1",
                "title": "Review dashboard queue",
                "target_repo": "sample-repo",
                "status": "review",
                "priority": 1,
                "agent_code": "pool-1",
                "handler": "build-loop",
                "logged_by": "claude-code",
            },
            {
                "id": "oc-2",
                "title": "Wait for direction",
                "target_repo": str(self.workdir),
                "status": "needs_input",
                "origin": "codex",
            },
            {
                "id": "oc-done",
                "title": "Already done",
                "target_repo": "sample-repo",
                "status": "done",
            },
            {
                "id": "other",
                "title": "Different repository",
                "target_repo": "other-repo",
                "status": "todo",
            },
        ]).encode()

        with patch.object(surface, "urlopen", return_value=response):
            payload = surface.collect_task_surface(
                workdir=self.workdir,
                include_memory=False,
                include_operations_center=True,
            )

        rows = [row for row in payload["items"] if row["surface"] == "operations_center"]
        self.assertEqual([row["id"] for row in rows], ["oc-1", "oc-2"])
        self.assertEqual(rows[0]["created_by"], "claude-code")
        self.assertEqual(rows[0]["owner"], "")
        self.assertTrue(rows[0]["execution_eligible"])
        self.assertFalse(rows[1]["execution_eligible"])
        self.assertEqual(payload["operations_center"], {"status": "available", "matched_count": 2})

        with patch.object(surface, "urlopen", side_effect=TimeoutError("offline")):
            unavailable = surface.collect_task_surface(
                workdir=self.workdir,
                include_memory=False,
                include_operations_center=True,
            )
        self.assertEqual(unavailable["operations_center"]["status"], "unavailable")
        self.assertEqual(unavailable["open_count"], 0)

    def test_legacy_flat_backlog_item_without_frontmatter_still_surfaces(self) -> None:
        backlog = self.workdir / ".build-loop" / "backlog"
        backlog.mkdir(parents=True)
        (backlog / "legacy.md").write_text("# Legacy backlog item\n", encoding="utf-8")

        result = run_surface("--workdir", str(self.workdir), "--no-memory", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["counts_by_surface"]["backlog"], 1)
        self.assertEqual(payload["items"][0]["title"], "Legacy backlog item")
        self.assertFalse(payload["items"][0]["execution_eligible"])

    def test_initiative_and_decision_backlog_policies_are_non_executable(self) -> None:
        items = self.workdir / ".build-loop" / "backlog" / "items"
        items.mkdir(parents=True)
        (items / "initiative.md").write_text(
            "---\nstatus: open\nbucket: initiative\nworkstream: dashboard-b\n---\n# Redesign UI\n",
            encoding="utf-8",
        )
        (items / "decision.md").write_text(
            "---\nstatus: open\ntype: decision\nbucket: decision\nworkstream: dashboard-b\n---\n# Pick density\n",
            encoding="utf-8",
        )
        payload = json.loads(run_surface(
            "--workdir", str(self.workdir), "--no-memory", "--json"
        ).stdout)
        policies = {row["bucket"]: row["pickup_policy"] for row in payload["items"]}
        self.assertEqual(policies["initiative"], "user-approval-plus-isolated-worktree")
        self.assertEqual(policies["decision"], "surface-only-for-matching-workstream")
        self.assertEqual(payload["execution_queue_count"], 0)

    def test_symlinked_external_backlog_is_not_read(self) -> None:
        outside = self.root / "outside-backlog"
        outside.mkdir()
        (outside / "external.md").write_text("# External task\n", encoding="utf-8")
        build_loop = self.workdir / ".build-loop"
        build_loop.mkdir(exist_ok=True)
        (build_loop / "backlog").symlink_to(outside, target_is_directory=True)

        result = run_surface("--workdir", str(self.workdir), "--no-memory", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertNotIn("External task", [row["title"] for row in payload["items"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
