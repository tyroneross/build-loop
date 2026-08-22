#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the commit-boundary memory receipt.

The trigger is calibrated, not intuited: a broad substring rule fired on 59% of
112 real commits in this repo and would have trained the reader to ignore the
packet. These tests pin the calibration so a later widening has to argue with a
failing test rather than sail through review.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit_before_commit as audit  # type: ignore  # noqa: E402


def test_authored_knowledge_files_require_a_receipt() -> None:
    for path in (
        "AGENTS.md",
        "agents/build-orchestrator.md",
        "skills/build-loop/SKILL.md",
        "db/migrations/001_init.sql",
        "api/openapi.yaml",
    ):
        assert audit._memory_lane_hits([path]) == [path], path


def test_generated_artifacts_never_require_a_receipt() -> None:
    """The diagram hook rewrites these on EVERY commit.

    Counting them took the trigger from 24% to 51%+ and made it noise.
    """
    generated = [
        "architecture/ARCHITECTURE.md",
        "architecture/model.json",
        "docs/build-loop-flow-mockup.html",
        "plugin-artifacts/codex/agents/scope-auditor.md",
    ]
    assert audit._memory_lane_hits(generated) == []


def test_tests_and_plain_code_never_require_a_receipt() -> None:
    assert audit._memory_lane_hits(["scripts/test_memory_locator.py"]) == []
    assert audit._memory_lane_hits(["scripts/memory_locator.py"]) == []
    assert audit._memory_lane_hits(["src/agents_helper.py"]) == []


def test_blast_radius_alone_does_not_require_a_receipt() -> None:
    """A 57-file refactor that teaches nothing needs no memory entry."""
    receipt = audit._memory_receipt(Path("."), ["src/a.py"] * 57, {"level": "high"})
    assert receipt["required"] is False


def test_receipt_is_unsatisfied_when_no_read_or_write_is_recorded() -> None:
    receipt = audit._memory_receipt(Path("."), ["agents/x.md"], {"level": "low"})
    assert receipt["required"] is True
    assert receipt["satisfied"] is False
    assert "MISSING" in audit._memory_receipt_section(receipt)


def test_enforcement_is_off_by_default(tmp_path: Path) -> None:
    """Warn-only until the false-positive rate is measured in the wild."""
    assert audit._enforce_memory_receipt_enabled(tmp_path) is False
