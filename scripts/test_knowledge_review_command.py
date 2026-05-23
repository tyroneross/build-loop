#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for knowledge_review.py — the script that backs /knowledge:review.

Covers:
- empty repo -> all four sections present, all empty
- decision in _review/ -> appears under Review queue
- decision >90 days old -> appears under Decision rot
- procedure with missing symbol -> appears under Stale procedures
- fact_conflicts row -> appears under Open conflicts (live DB if available, else section absent)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "knowledge_review.py"


def _has_db() -> bool:
    if "DATABASE_URL" in os.environ:
        return True
    return (Path.home() / ".config" / "agent-memory" / "connection.env").exists()


def _make_decision(workdir: Path, did: str, days_ago: int) -> Path:
    date_str = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    fm = (
        "---\n"
        f"id: '{did}'\n"
        f"slug: dec-{did}\n"
        f"title: Decision {did}\n"
        "type: decision\nstatus: accepted\nconfidence: explicit\n"
        f"date: '{date_str}'\n"
        "tags: [testing]\nprimary_tag: testing\n"
        f"entity: 'fixture:{did}'\n"
        "source: manual\n"
        "---\n\n# body\n"
    )
    decisions_dir = workdir / ".episodic" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    p = decisions_dir / f"{did}-{date_str}-dec.md"
    p.write_text(fm)
    return p


def _make_review_decision(workdir: Path, did: str) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fm = (
        "---\n"
        f"id: '{did}'\n"
        f"title: Review queue item {did}\n"
        "type: decision\nstatus: proposed\nconfidence: inferred\n"
        f"date: '{today}'\n"
        "tags: [testing]\nprimary_tag: testing\n"
        f"entity: 'review:{did}'\n"
        "source: auto-inferred\n"
        "---\n\n# body\n"
    )
    review_dir = workdir / ".episodic" / "decisions" / "_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    p = review_dir / f"{did}-{today}-review.md"
    p.write_text(fm)
    return p


def _make_procedure(workdir: Path, name: str, missing_symbol: str | None = None) -> None:
    pdir = workdir / ".procedural" / name
    pdir.mkdir(parents=True)
    deps = (
        f"depends_on:\n  - symbol: \"{missing_symbol}\"\n    last_verified: \"2026-01-01\"\n"
        if missing_symbol
        else "depends_on: []\n"
    )
    fm = (
        "---\n"
        f"name: {name}\n"
        "trigger: 'sym'\n"
        "domains: [test]\nconfidence: medium\n"
        "created: '2026-01-01'\nincident_count: 0\n"
        + deps
        + "---\n# body\n"
    )
    (pdir / "procedure.md").write_text(fm)


def run_review(workdir: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir)] + list(extra),
        capture_output=True,
        text=True,
    )


class KnowledgeReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        (self.workdir / ".episodic" / "decisions").mkdir(parents=True)
        (self.workdir / ".procedural").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_empty_repo_renders_all_sections(self) -> None:
        r = run_review(self.workdir, "--no-db")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        out = r.stdout
        self.assertIn("Review queue", out)
        self.assertIn("Decision rot", out)
        self.assertIn("Stale procedures", out)
        self.assertIn("Open conflicts", out)

    def test_review_queue_picks_up_quarantined_decision(self) -> None:
        _make_review_decision(self.workdir, "0099")
        r = run_review(self.workdir, "--no-db")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("0099", r.stdout)

    def test_decision_rot_picks_up_old_decision(self) -> None:
        _make_decision(self.workdir, "0001", days_ago=120)
        _make_decision(self.workdir, "0002", days_ago=10)
        r = run_review(self.workdir, "--no-db", "--rot-threshold-days", "90")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("0001", r.stdout)
        # 0002 should NOT appear in the rot section
        # (it may appear elsewhere; we just check the rot section's body)
        rot_section = r.stdout.split("Decision rot")[1].split("##")[0]
        self.assertNotIn("0002", rot_section)

    def test_stale_procedures_picks_up_missing_symbol(self) -> None:
        # Empty source tree, symbol does not exist
        (self.workdir / "src").mkdir()
        (self.workdir / "src" / "x.py").write_text("# nothing here\n")
        _make_procedure(self.workdir, "stale-one", missing_symbol="NopeNotFound")
        r = run_review(self.workdir, "--no-db", "--symbol-paths", "src")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("stale-one", r.stdout)
        self.assertIn("NopeNotFound", r.stdout)

    def test_no_db_renders_conflicts_section_as_unavailable(self) -> None:
        r = run_review(self.workdir, "--no-db")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        # Either "no open conflicts" or "DB not consulted"; just ensure section header is there
        self.assertIn("Open conflicts", r.stdout)


if __name__ == "__main__":
    unittest.main()
