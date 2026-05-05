"""Static doc-shape test for `references/memory-systems.md`.

Locks Priority 14 (run #4): the read protocol must mirror the write
protocol's structural shape — both surfaces have a heading and at least
one fenced executable block per call site. Behavior-preserving check;
prevents future doc edits from collapsing the symmetry that the
proposal `memory-read-protocol-symmetry.md` argued for.

Pure stdlib + pytest. Sub-second.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "references" / "memory-systems.md"


@pytest.fixture(scope="module")
def doc_text() -> str:
    assert DOC.is_file(), f"missing reference doc: {DOC}"
    return DOC.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Return the body of an H2 section by heading text. Empty if not found."""
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    return m.group(1) if m else ""


def test_read_protocol_section_present(doc_text: str) -> None:
    body = _section(doc_text, "Read protocol — Phase 1 Assess")
    assert body, "Missing §'Read protocol — Phase 1 Assess'"


def test_write_protocol_section_present(doc_text: str) -> None:
    body = _section(doc_text, "Write protocol — Phase 4 Review sub-step F")
    assert body, "Missing §'Write protocol — Phase 4 Review sub-step F'"


def test_read_protocol_has_fenced_blocks_per_call_site(doc_text: str) -> None:
    """Every numbered call site (1-5) under Read protocol gets ≥1 fenced block."""
    body = _section(doc_text, "Read protocol — Phase 1 Assess")
    # Split body into per-step sections by H3 markers (### 1., ### 2., ...).
    step_pattern = re.compile(
        r"^###\s+\d+\.\s+(.+?)$(.*?)(?=^###\s+\d+\.\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    steps = step_pattern.findall(body)
    assert len(steps) >= 5, (
        f"Read protocol should have ≥5 numbered call sites, got {len(steps)}"
    )
    for title, segment in steps:
        # Each step must contain at least one fenced block.
        assert "```" in segment, (
            f"Read protocol step '{title.strip()}' has no fenced executable block"
        )


def test_read_protocol_has_return_shape_table(doc_text: str) -> None:
    """The read protocol summary table mirrors the write protocol's exit-code style."""
    body = _section(doc_text, "Read protocol — Phase 1 Assess")
    # Look for a markdown table with at least Step / Surface / Return shape columns.
    assert "Return shape" in body or "return shape" in body.lower(), (
        "Read protocol missing return-shape contract (table or per-step note)"
    )
    # And a degradation matrix.
    assert "degradation" in body.lower() or "Degradation" in body, (
        "Read protocol missing graceful-degradation discussion"
    )


def test_orchestrator_retains_5_step_imperative() -> None:
    """Priority 12's 5-step Phase 1 memory imperative must remain in the orchestrator."""
    orch = REPO / "agents" / "build-orchestrator.md"
    text = orch.read_text(encoding="utf-8")
    # Must still contain each of the 4 numbered imperative steps.
    for needle in (
        "Read(\"~/.build-loop/memory/MEMORY.md\")",
        ".build-loop/state.json",
        "scripts/memory_facade.py recall",
        "build-loop:debugging-memory",
    ):
        assert needle in text, (
            f"Orchestrator Phase 1 memory imperative missing: {needle!r}"
        )


def test_orchestrator_cross_links_read_protocol() -> None:
    """The orchestrator includes a single cross-link to the read-protocol doc."""
    orch = REPO / "agents" / "build-orchestrator.md"
    text = orch.read_text(encoding="utf-8")
    assert "Read protocol — Phase 1 Assess" in text, (
        "Orchestrator should cross-link to references/memory-systems.md "
        "§'Read protocol — Phase 1 Assess' for return-shape contracts"
    )


def test_proposal_consumed() -> None:
    """The deferred proposal file should be deleted once consumed."""
    proposal = REPO / ".build-loop" / "proposals" / "memory-read-protocol-symmetry.md"
    assert not proposal.exists(), (
        f"Proposal {proposal} still exists; should be deleted after consumption"
    )
