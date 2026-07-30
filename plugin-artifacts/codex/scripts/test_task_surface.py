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

HERE = Path(__file__).resolve().parent
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
        followup = self.workdir / ".build-loop" / "followup"
        issues.mkdir()
        followup.mkdir()
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
        self.assertEqual(payload["counts_by_surface"]["followup"], 1)
        self.assertEqual(payload["dry_run"]["mode"], "rank-only")
        self.assertEqual(payload["dry_run"]["next_item"]["id"], "T-1")
        self.assertEqual(payload["dry_run"]["next_item"]["dry_run_action"], "continue_in_flight")
        self.assertEqual(payload["dry_run"]["next_item"]["rank"], 1)
        self.assertEqual(payload["iteration_summary"]["T-1"]["attempts"], 1)
        self.assertEqual(payload["iteration_summary"]["T-1"]["stop_reason"], "validator-failed")

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
        titles = [row["title"] for row in payload["items"]]
        self.assertIn("Ship active backlog item", titles)
        self.assertNotIn("Completed backlog item", titles)
        self.assertNotIn("Dropped backlog item", titles)
        self.assertNotIn("Archived backlog item", titles)
        self.assertNotIn("Backlog", titles)

    def test_legacy_flat_backlog_item_without_frontmatter_still_surfaces(self) -> None:
        backlog = self.workdir / ".build-loop" / "backlog"
        backlog.mkdir(parents=True)
        (backlog / "legacy.md").write_text("# Legacy backlog item\n", encoding="utf-8")

        result = run_surface("--workdir", str(self.workdir), "--no-memory", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["counts_by_surface"]["backlog"], 1)
        self.assertEqual(payload["items"][0]["title"], "Legacy backlog item")


if __name__ == "__main__":
    unittest.main(verbosity=2)
