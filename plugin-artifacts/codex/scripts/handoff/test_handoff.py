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
from handoff.__main__ import compose, _queue_items, _landmines, _git_state, _read_state


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
            "## 8. Landmines",
            "## 9. Resume Instructions",
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
        positions = [doc.index(f"## {i}.") for i in range(1, 10)]
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


class TestQueueItems:
    def test_returns_headings(self, tmp_path: Path) -> None:
        d = tmp_path / "followup"
        d.mkdir()
        (d / "001.md").write_text("# My Title\nbody", encoding="utf-8")
        (d / "002.md").write_text("no heading", encoding="utf-8")
        titles = _queue_items(d)
        assert "My Title" in titles
        assert "002" in titles  # falls back to stem

    def test_empty_dir(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        assert _queue_items(d) == []

    def test_missing_dir(self, tmp_path: Path) -> None:
        assert _queue_items(tmp_path / "nonexistent") == []

    def test_no_silent_cap(self, tmp_path: Path) -> None:
        """Regression: a limit=5 default under-reported followup/ as 5-of-8,
        dropping three judgment-owed audit-debt items from the handoff."""
        d = tmp_path / "q"
        d.mkdir()
        for i in range(12):
            (d / f"{i:03d}.md").write_text(f"# Item {i}\n", encoding="utf-8")
        assert len(_queue_items(d)) == 12

    def test_prefers_canonical_items_subdir_over_derived_index(self, tmp_path: Path) -> None:
        """Regression: backlog/ reported '1 item' (its derived INDEX.md) while
        backlog/items/ held 7, including release blockers."""
        d = tmp_path / "backlog"
        (d / "items").mkdir(parents=True)
        (d / "INDEX.md").write_text("# Backlog — proj\nderived view\n", encoding="utf-8")
        for i in range(7):
            (d / "items" / f"IT-{i}.md").write_text(f"# Real item {i}\n", encoding="utf-8")
        titles = _queue_items(d)
        assert len(titles) == 7
        assert not any("Backlog —" in t for t in titles)

    def test_skips_closed_subdirs(self, tmp_path: Path) -> None:
        d = tmp_path / "followup"
        (d / "resolved").mkdir(parents=True)
        (d / "open.md").write_text("# Open one\n", encoding="utf-8")
        (d / "resolved" / "old.md").write_text("# Closed one\n", encoding="utf-8")
        assert _queue_items(d) == ["Open one"]

    def test_reads_frontmatter_title(self, tmp_path: Path) -> None:
        d = tmp_path / "backlog"
        (d / "items").mkdir(parents=True)
        (d / "items" / "a.md").write_text(
            'id: X\ntitle: "Pin the runtime flag"\nstatus: open\n', encoding="utf-8"
        )
        assert _queue_items(d) == ["Pin the runtime flag  _(status=open)_"]

    def test_surfaces_pickup_safety_fields(self, tmp_path: Path) -> None:
        """A bare title cannot tell a resumer whether an item is safe to take.
        All three cold-read reviewers (2026-07-26) named this their top gap."""
        d = tmp_path / "followup"
        d.mkdir()
        (d / "owed.md").write_text(
            "# Judgment owed — bl-123\n"
            "judgment_verdict: warn\n"
            "owed_layers: [independent-auditor]\n"
            "classify: DECISION\n",
            encoding="utf-8",
        )
        [item] = _queue_items(d)
        assert "judgment_verdict=warn" in item
        assert "owed_layers=" in item
        assert "classify=DECISION" in item


class TestLandmines:
    def test_flags_stale_current_run_id(self, tmp_path: Path) -> None:
        bl = tmp_path / ".build-loop"
        bl.mkdir()
        (bl / ".current-run-id").write_text("bl-OLD\n", encoding="utf-8")
        found = _landmines(bl, {"runs": [{"run_id": "bl-NEW"}]})
        assert any("bl-OLD" in f and "bl-NEW" in f for f in found)

    def test_flags_push_hold(self, tmp_path: Path) -> None:
        bl = tmp_path / ".build-loop"
        bl.mkdir()
        (bl / ".push-hold").write_text(
            '{"reason":"do-not-push","run_id":"bl-OTHER","set_at":"2026-07-21"}',
            encoding="utf-8",
        )
        assert any("push-hold" in f for f in _landmines(bl, {}))

    def test_flags_reconstructed_run(self, tmp_path: Path) -> None:
        bl = tmp_path / ".build-loop"
        bl.mkdir()
        found = _landmines(bl, {"runs": [{"run_id": "r", "reconstructed": True, "notes": "why"}]})
        assert any("RECONSTRUCTED" in f for f in found)

    def test_clean_state_has_no_landmines(self, tmp_path: Path) -> None:
        bl = tmp_path / ".build-loop"
        bl.mkdir()
        assert _landmines(bl, {"runs": [{"run_id": "r"}]}) == []


class TestNoTruncation:
    def test_long_intent_is_inlined_whole(self, tmp_path: Path) -> None:
        """Regression: intent.md was cut at 30 lines, landing immediately
        before `non_goals` -- so the handoff omitted the constraints."""
        bl = tmp_path / ".build-loop"
        bl.mkdir()
        body = "\n".join(f"line {i}" for i in range(60)) + "\n## non_goals\n- do not ship"
        (bl / "intent.md").write_text(f"# Intent\n{body}\n", encoding="utf-8")
        doc = compose(tmp_path)["document"]
        assert "non_goals" in doc
        assert "do not ship" in doc
        assert "truncated" not in doc
