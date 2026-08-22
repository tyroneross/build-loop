from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_policy_covers_update_compare_and_audit() -> None:
    text = (ROOT / "references/ibr-ui-verification-policy.md").read_text(encoding="utf-8").lower()
    assert "updates, compares, or audits" in text
    assert "primary visual verifier" in text
    assert "interactive viewers" in text


def test_active_guidance_does_not_restore_explicit_only_verification() -> None:
    active_files = [
        "skills/ui-design/SKILL.md",
        "skills/build-loop/fallbacks.md",
        "skills/build-loop/references/phase-1-assess.md",
        "skills/build-loop/references/phase-5-iterate.md",
        "references/iterate-protocol.md",
        "references/ui-spotcheck-protocol.md",
        "references/phase-gate-checklist.md",
        "agents/build-orchestrator.md",
        "agents/design-contract-specialist.md",
        "skills/plugin-tests/SKILL.md",
    ]
    forbidden = (
        "do not route to ibr unless",
        "ibr is not invoked unless",
        "ibr may run only when the user explicitly asks",
        "dispatch ibr only when the user explicitly asks",
    )
    for relative in active_files:
        text = (ROOT / relative).read_text(encoding="utf-8").lower()
        for phrase in forbidden:
            assert phrase not in text, f"{relative} restored stale policy: {phrase}"
