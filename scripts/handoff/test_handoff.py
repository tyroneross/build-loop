#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for scripts/handoff/__main__.py — fixture-based, no network."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Add the scripts directory so we can import directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
from handoff.__main__ import compose, _queue_titles, _git_state, _read_state


@pytest.fixture()
def fake_bl(tmp_path: Path) -> Path:
    """Create a minimal .build-loop/ fixture."""
    bl = tmp_path / ".build-loop"
    bl.mkdir()
    (bl / "intent.md").write_text("# Intent\nBuild something great.", encoding="utf-8")
    (bl / "goal.md").write_text("# Goal\nF1: passes.\nF2: passes.", encoding="utf-8")
    state = {
        "phase": "execute",
        "execution": {"phase": "execute", "run_id": "test-run-001"},
        "runs": [
            {
                "run_id": "test-run-001",
                "date": "2026-06-06",
                "outcome": "pass",
                "goal": "Build something great.",
                "phases": {"assess": {"status": "done"}, "plan": {"status": "done"}},
                "judge_decisions": [],
            }
        ],
    }
    (bl / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (bl / "feedback.md").write_text("2026-06-06 | Lesson: always test.\n", encoding="utf-8")

    # Queues
    fu = bl / "followup"
    fu.mkdir()
    (fu / "001-auth.md").write_text("# Fix auth timeout\nDetails.", encoding="utf-8")

    backlog = bl / "backlog"
    backlog.mkdir()
    (backlog / "b1-refactor.md").write_text("# Refactor parser\n", encoding="utf-8")

    return tmp_path


class TestCompose:
    def test_all_sections_present(self, fake_bl: Path) -> None:
        result = compose(fake_bl)
        doc = result["document"]
        for heading in [
            "## 1. North Star",
            "## 2. Current Goal",
            "## 3. Phase",
            "## 4. Git State",
            "## 5. Queues",
            "## 6. Gotchas",
            "## 7. Last Run Summary",
            "## 8. Resume Instructions",
        ]:
            assert heading in doc, f"Missing section: {heading}"

    def test_sources_populated(self, fake_bl: Path) -> None:
        result = compose(fake_bl)
        assert "intent.md" in result["sources"]
        assert "goal.md" in result["sources"]
        assert "state.json" in result["sources"]
        assert "queues" in result["sources"]

    def test_no_errors_on_full_fixture(self, fake_bl: Path) -> None:
        result = compose(fake_bl)
        assert result["errors"] == [], f"Unexpected errors: {result['errors']}"

    def test_phase_rendered(self, fake_bl: Path) -> None:
        result = compose(fake_bl)
        assert "execute" in result["document"]

    def test_queue_item_listed(self, fake_bl: Path) -> None:
        result = compose(fake_bl)
        # followup item title should appear
        assert "Fix auth timeout" in result["document"]
        # backlog item
        assert "Refactor parser" in result["document"]

    def test_stable_section_order(self, fake_bl: Path) -> None:
        doc = compose(fake_bl)["document"]
        positions = [doc.index(f"## {i}.") for i in range(1, 9)]
        assert positions == sorted(positions), "Sections are not in order"


class TestEmptyRepo:
    """Compose should not crash when .build-loop/ is absent or empty."""

    def test_no_bl_directory(self, tmp_path: Path) -> None:
        result = compose(tmp_path)
        doc = result["document"]
        assert "n/a" in doc
        assert "## 1. North Star" in doc

    def test_partial_bl_directory(self, tmp_path: Path) -> None:
        bl = tmp_path / ".build-loop"
        bl.mkdir()
        # Only intent.md — everything else missing
        (bl / "intent.md").write_text("# Intent\nMinimal.", encoding="utf-8")
        result = compose(tmp_path)
        doc = result["document"]
        assert "Minimal" in doc
        assert "n/a" in doc  # goal is missing

    def test_json_output(self, fake_bl: Path) -> None:
        result = compose(fake_bl)
        # JSON round-trip
        dumped = json.dumps(result)
        loaded = json.loads(dumped)
        assert loaded["document"] == result["document"]


class TestQueueTitles:
    def test_returns_headings(self, tmp_path: Path) -> None:
        d = tmp_path / "followup"
        d.mkdir()
        (d / "001.md").write_text("# My Title\nbody", encoding="utf-8")
        (d / "002.md").write_text("no heading", encoding="utf-8")
        titles = _queue_titles(d)
        assert "My Title" in titles
        assert "002" in titles  # falls back to stem

    def test_empty_dir(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        assert _queue_titles(d) == []

    def test_missing_dir(self, tmp_path: Path) -> None:
        assert _queue_titles(tmp_path / "nonexistent") == []

    def test_limit_honored(self, tmp_path: Path) -> None:
        d = tmp_path / "q"
        d.mkdir()
        for i in range(10):
            (d / f"{i:03d}.md").write_text(f"# Item {i}\n", encoding="utf-8")
        titles = _queue_titles(d, limit=3)
        assert len(titles) == 3
