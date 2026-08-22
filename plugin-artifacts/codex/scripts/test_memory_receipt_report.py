#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the live memory-receipt measurement report."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import memory_receipt_report as rpt  # type: ignore  # noqa: E402


def _ledger(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _row(required=True, satisfied=False, read=False, write=False, hits=("agents/a.md",)):
    return {"ts": "2026-08-22T12:00:00Z", "repo": "demo", "commit": "abc",
            "required": required, "satisfied": satisfied, "read": read,
            "write": write, "lane_hits": list(hits), "enforced": False, "blocked": False}


def test_thin_sample_is_never_safe_to_enforce(tmp_path: Path) -> None:
    rows = [_row(required=True, satisfied=True, read=True, write=True)] * 5
    s = rpt.summarize(rpt.load(_ledger(tmp_path / "l.jsonl", rows), None))
    assert s["safe_to_enforce"] is False
    assert any("commits observed" in b for b in s["blockers"])


def test_zero_satisfaction_blocks_enforcement_even_at_a_low_fire_rate(tmp_path: Path) -> None:
    """The failure this guards: enforcing turns a warning into a wall.

    Nobody wrote memory before the gate existed, so satisfaction starts near
    zero by construction. Fire rate alone must not authorize enforcement.
    """
    rows = [_row(required=False)] * 24 + [_row(required=True, satisfied=False)] * 6
    s = rpt.summarize(rpt.load(_ledger(tmp_path / "l.jsonl", rows), None))
    assert s["fire_rate"] <= rpt.MAX_HEALTHY_FIRE_RATE
    assert s["safe_to_enforce"] is False
    assert any("satisfied" in b for b in s["blockers"])


def test_noisy_trigger_blocks_enforcement(tmp_path: Path) -> None:
    rows = [_row(required=True, satisfied=True, read=True, write=True)] * 25
    s = rpt.summarize(rpt.load(_ledger(tmp_path / "l.jsonl", rows), None))
    assert s["fire_rate"] == 1.0
    assert s["safe_to_enforce"] is False
    assert any("fires on" in b for b in s["blockers"])


def test_healthy_ledger_is_safe_to_enforce(tmp_path: Path) -> None:
    rows = ([_row(required=False)] * 22
            + [_row(required=True, satisfied=True, read=True, write=True)] * 8)
    s = rpt.summarize(rpt.load(_ledger(tmp_path / "l.jsonl", rows), None))
    assert s["safe_to_enforce"] is True
    assert s["blockers"] == []


def test_read_without_write_is_counted_separately(tmp_path: Path) -> None:
    """Consulting memory but never recording is the expected partial adoption."""
    rows = [_row(required=True, satisfied=False, read=True, write=False)] * 3
    s = rpt.summarize(rpt.load(_ledger(tmp_path / "l.jsonl", rows), None))
    assert s["read_but_no_write"] == 3


def test_missing_ledger_reports_instead_of_raising(tmp_path: Path) -> None:
    s = rpt.summarize(rpt.load(tmp_path / "nope.jsonl", None))
    assert s["commits_observed"] == 0
    assert s["safe_to_enforce"] is False
