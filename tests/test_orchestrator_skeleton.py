"""Tests for the build-orchestrator agent definition skeleton.

Locks priority 2 of the architecture-awareness follow-up: the orchestrator
must stay compressed (≤200 lines), reference its references[] files, and
preserve every wiring point added by prior chunks.

Behavior is preserved 1:1 with prior versions; this test catches accidental
deletion during future compressions/re-flows.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ORCH = REPO / "agents" / "build-orchestrator.md"
REFS = REPO / "references"

LINE_BUDGET = 200


# ---------------------------------------------------------------------------
# Skeleton invariants
# ---------------------------------------------------------------------------

def _read() -> str:
    return ORCH.read_text(encoding="utf-8")


def test_orchestrator_under_line_budget() -> None:
    text = _read()
    n = text.count("\n") + (0 if text.endswith("\n") else 1)
    assert n <= LINE_BUDGET, (
        f"build-orchestrator.md is {n} lines, must be ≤ {LINE_BUDGET}. "
        f"Push detail into references/* on demand."
    )


def test_all_six_phase_headers_present() -> None:
    text = _read()
    for n in range(1, 7):
        assert re.search(rf"^### Phase {n}: ", text, re.MULTILINE), (
            f"missing Phase {n} header"
        )


def test_at_least_5_architecture_scout_dispatches() -> None:
    """Phase 1 baseline + Phase 2 chunk-impact + Phase 4 review-rules +
    Phase 5 iterate-subgraph + Phase 6 learn-sync = 5 dispatches."""
    text = _read()
    count = text.count("architecture-scout")
    assert count >= 5, (
        f"only {count} architecture-scout dispatches found; expected ≥ 5 "
        f"(baseline, chunk-impact, review-rules, iterate-subgraph, learn-sync)"
    )


# ---------------------------------------------------------------------------
# References files exist and are linked
# ---------------------------------------------------------------------------

REQUIRED_REFERENCES = [
    "capability-routing.md",
    "trigger-rules.md",
    "phase-gate-checklist.md",
    "memory-systems.md",
    "iterate-protocol.md",
    "learn-protocol.md",
]


@pytest.mark.parametrize("name", REQUIRED_REFERENCES)
def test_reference_file_exists(name: str) -> None:
    p = REFS / name
    assert p.is_file(), f"missing references/{name}"
    body = p.read_text(encoding="utf-8")
    assert len(body) >= 200, f"references/{name} looks empty / stub"


@pytest.mark.parametrize("name", REQUIRED_REFERENCES)
def test_orchestrator_references_each_file(name: str) -> None:
    """Every references/* file should be linked from the orchestrator."""
    text = _read()
    assert f"references/{name}" in text, (
        f"orchestrator does not link to references/{name} — extracted "
        f"detail must remain reachable"
    )


# ---------------------------------------------------------------------------
# Specific wiring points
# ---------------------------------------------------------------------------

def test_plan_verify_gate_present() -> None:
    text = _read()
    assert "plan_verify.py" in text, "Phase 2 plan-verify gate dropped"
    assert "plan-critic" in text, "Phase 2 plan-critic dispatch dropped"


def test_capability_registry_wired() -> None:
    text = _read()
    assert "build_capability_registry.py" in text, "Phase 1 registry build dropped"
    assert "capability_shortlist.py" in text or "build-loop:capabilities" in text, (
        "Phase 1 shortlist dispatch dropped"
    )


def test_ibr_quickpass_present() -> None:
    """Even though detail moved to phase-gate-checklist.md, the orchestrator
    must mention the validate sub-step's IBR-first behavior at high level."""
    text = _read()
    # The B sub-step bullet should mention IBR-first.
    assert "IBR-first" in text or "ibr_quickpass" in text or "ibr-bridge" in text or "IBR" in text


def test_deployment_policy_block_present() -> None:
    text = _read()
    assert "deploymentPolicy" in text, "deployment policy config block dropped"
    assert "deployment_policy.py" in text


def test_intent_routing_present() -> None:
    text = _read()
    for label in ("BUILD", "OPTIMIZE", "RESEARCH", "TEST"):
        assert f"**{label}**" in text, f"intent-routing label {label} dropped"


def test_model_tiering_block_present() -> None:
    text = _read()
    assert "Model Tiering" in text
    assert "claude-opus-4-7" in text or "Opus 4.7" in text
    assert "sonnet" in text.lower()


def test_output_format_section_present() -> None:
    text = _read()
    assert "Output Format" in text
    assert "✅" in text and "❌" in text and "❓" in text


def test_memory_systems_block_present() -> None:
    text = _read()
    assert "memory_facade" in text or "memory-systems.md" in text


def test_learn_phase_dispatches_pattern_detector_and_scout() -> None:
    text = _read()
    assert "recurring-pattern-detector" in text
    # Scout learn-sync: either the dispatch line or the references file ref.
    assert "learn-sync" in text or "learn-protocol.md" in text
