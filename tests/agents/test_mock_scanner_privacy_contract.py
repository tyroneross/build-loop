# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for mock-scanner's public-surface privacy scan."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MOCK_SCANNER = REPO / "agents" / "mock-scanner.md"
ORCHESTRATOR = REPO / "agents" / "build-orchestrator.md"
PHASE_REVIEW = REPO / "skills" / "build-loop" / "references" / "phase-4-review.md"
FACT_CHECK = REPO / "skills" / "build-loop" / "phases" / "fact-check.md"
PUSH_READY = REPO / "references" / "push-readiness-checklist.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_mock_scanner_covers_public_surface_privacy_categories() -> None:
    body = _read(MOCK_SCANNER).lower()
    for term in (
        "api keys",
        "secrets",
        "absolute local paths",
        "persona/profile",
        "personal data",
        "rally",
        "public package",
    ):
        assert term in body


def test_review_docs_route_privacy_scan_through_mock_scanner() -> None:
    for path in (ORCHESTRATOR, PHASE_REVIEW, FACT_CHECK):
        body = _read(path).lower()
        assert "mock-scanner" in body or "mock and privacy data scanner" in body
        assert "privacy" in body
        assert "parallel" in body


def test_push_readiness_uses_mock_scanner_not_new_git_hook() -> None:
    body = _read(PUSH_READY).lower()
    assert "mock-scanner" in body
    assert "privacy scan" in body
    assert "do not add a separate git hook" in body


def test_privacy_findings_route_to_orchestrator_work_not_halt() -> None:
    combined = "\n".join(
        _read(path).lower()
        for path in (MOCK_SCANNER, PHASE_REVIEW, FACT_CHECK, PUSH_READY)
    )
    for term in (
        "do not halt",
        "orchestrator",
        "iterate",
        ".gitignore",
        "untracking",
        "archive",
        "over deletion",
    ):
        assert term in combined
