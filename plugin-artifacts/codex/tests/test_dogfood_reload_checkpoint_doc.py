"""Static checks for dogfood reload checkpoint documentation."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_phase_and_orchestrator_docs_reference_reload_helper() -> None:
    for rel in (
        "agents/build-orchestrator.md",
        "references/phase-3-execute.md",
        "skills/build-loop/SKILL.md",
        "skills/build-loop/references/self-recursive-dev.md",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "dogfood_reload_checkpoint.py" in text, f"{rel} missing helper"
        assert "dogfood-reload-checkpoint.md" in text, f"{rel} missing protocol"


def test_coordination_rules_say_handoff_is_not_reload_proof() -> None:
    text = (REPO / "references" / "coordination-rules.md").read_text(
        encoding="utf-8"
    )
    assert "A Rally handoff or inject is not reload proof" in text
    assert "runtime root + commit" in text
    assert "continue_solo" in text


def test_protocol_names_ack_and_fallback_requirements() -> None:
    text = (REPO / "references" / "dogfood-reload-checkpoint.md").read_text(
        encoding="utf-8"
    )
    for needle in (
        "ack",
        "fallback",
        "claude --plugin-dir",
        "runtime-commit",
        "reload_checkpoint: ready",
    ):
        assert needle in text, f"protocol missing {needle!r}"
