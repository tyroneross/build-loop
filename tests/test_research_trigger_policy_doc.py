"""Static checks for the build-loop Research trigger policy."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_phase_docs_call_research_trigger_gate() -> None:
    for rel in (
        "agents/build-orchestrator.md",
        "references/phase-gate-checklist.md",
        "skills/build-loop/references/phase-1-assess.md",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "scripts/research_trigger.py" in text, f"{rel} missing research gate"
        assert "researchGate" in text, f"{rel} missing researchGate state contract"


def test_phase_2_requires_research_context_when_gate_fires() -> None:
    for rel in (
        "references/phase-gate-checklist.md",
        "skills/build-loop/references/phase-2-plan.md",
        "agents/build-orchestrator.md",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "Research Context" in text, f"{rel} missing Research Context plan gate"


def test_policy_documents_tshirt_depth_and_final_claim_gate() -> None:
    text = (REPO / "references" / "research-trigger-policy.md").read_text(
        encoding="utf-8"
    )
    for needle in (
        "T-shirt",
        "blocks_final_claims",
        "requires_citations_or_unavailable_note",
        "large T-shirt size alone",
        "Memory recall depth",
    ):
        assert needle in text, f"policy missing {needle!r}"
