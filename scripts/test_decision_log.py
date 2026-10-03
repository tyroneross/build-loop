#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for the private running decision log writer."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision_log import LOG_REL, RECEIPT_DIR, acknowledge_none, record  # noqa: E402


def test_record_preserves_history_and_is_idempotent(tmp_path: Path) -> None:
    log = tmp_path / LOG_REL
    log.parent.mkdir(parents=True)
    old = ("# Project decisions\n\n---\n\n## 2026-07-01 · Old choice\n\n"
           "**Decision (operator).** Keep this record.\n\n"
           "**Impacted files:** `app/search.tsx`.\n\n**Status:** DECIDED, not executed.\n")
    log.write_text(old)
    args = dict(
        title="Search has one entry point", decision="Use one results page.",
        rationale="Two search paths caused inconsistent behavior.",
        status="executed", evidence=["git:44b21ae79"],
        supersedes="2026-07-01 · Old choice", decision_date="2026-10-03",
        impacted_files=["app/search.tsx"],
    )

    first = record(tmp_path, **args)
    second = record(tmp_path, **args)

    assert first["created"] is True
    assert second == {**first, "created": False}
    content = log.read_text()
    assert content.count("Search has one entry point") == 1
    assert content.index("Search has one entry point") < content.index("Old choice")
    assert "**Supersedes:** 2026-07-01 · Old choice" in content
    assert "**Impacted files:** `app/search.tsx`" in content
    assert content.endswith(old.split("---\n\n", 1)[1])

    decided = record(tmp_path, **{**args, "status": "decided"})
    assert decided["created"] is True
    assert decided["id"] != first["id"]


def test_record_requires_rationale_and_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        record(tmp_path, title="Choice", decision="Do it", rationale="",
               status="decided", evidence=["git:abc"], decision_date="2026-10-03")
    with pytest.raises(ValueError):
        record(tmp_path, title="Choice", decision="Do it", rationale="Reason",
               status="decided", evidence=[], decision_date="2026-10-03")
    assert not (tmp_path / LOG_REL).exists()


def test_run_receipts_for_record_and_none(tmp_path: Path) -> None:
    import json

    result = record(tmp_path, title="Use shared search", decision="Share retrieval.",
                    rationale="Duplicate work was wasteful.", status="executed",
                    evidence=["git:abc"], run_id="run-1", decision_date="2026-10-03")
    receipt = json.loads((tmp_path / RECEIPT_DIR / "run-1.json").read_text())
    assert receipt["disposition"] == "recorded"
    assert receipt["decision_id"] == result["id"]
    with pytest.raises(ValueError):
        acknowledge_none(tmp_path, run_id="run-1", reason="No choice")
    none = acknowledge_none(tmp_path, run_id="run-2", reason="Copy change only")
    assert none["disposition"] == "none"
    assert json.loads((tmp_path / RECEIPT_DIR / "run-2.json").read_text())["reason"] == "Copy change only"
    with pytest.raises(ValueError):
        acknowledge_none(tmp_path, run_id="../bad", reason="No choice")
