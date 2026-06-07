"""Static checks for structured task capture policy."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_phase_docs_reference_task_surface() -> None:
    for rel in (
        "agents/build-orchestrator.md",
        "references/phase-gate-checklist.md",
        "skills/build-loop/references/phase-1-assess.md",
        "skills/build-loop/SKILL.md",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "scripts/task_surface.py" in text, f"{rel} missing task surface"
        assert "task-capture-policy.md" in text, f"{rel} missing policy link"


def test_policy_rejects_new_ledger_by_default() -> None:
    text = (REPO / "references" / "task-capture-policy.md").read_text(
        encoding="utf-8"
    )
    for needle in (
        "does not add a new durable task ledger by default",
        "derived-active-view-no-new-ledger",
        "Do not add `.build-loop/tasks.jsonl`",
        "Do not scan sibling project backlogs",
    ):
        assert needle in text, f"policy missing {needle!r}"
