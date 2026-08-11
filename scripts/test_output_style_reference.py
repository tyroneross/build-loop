"""Contract tests for outcome-first user-facing note guidance."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_STYLE = ROOT / "skills" / "build-loop" / "references" / "output-style.md"


def _guidance() -> str:
    return OUTPUT_STYLE.read_text(encoding="utf-8")


def test_release_notes_lead_with_actor_action_and_outcome() -> None:
    guidance = _guidance()

    assert "### Release notes lead with one useful claim" in guidance
    assert "`[Actor] [strong verb] [specific outcome].`" in guidance
    assert "The next sentence explains why the outcome matters." in guidance


def test_lists_support_the_release_note_claim() -> None:
    guidance = _guidance()

    assert "Treat a list as evidence, not insight." in guidance
    assert "Do not lead with a comma-separated inventory" in guidance
    assert "supporting detail last" in guidance


def test_example_names_the_actor_before_the_lifecycle_inventory() -> None:
    guidance = _guidance()
    strong_example = guidance.split("**Strong —", 1)[1].split("### Good", 1)[0]

    direct_claim = "Rally enforces ownership at the write boundary"
    supporting_inventory = "lead changes, claim closure, expired-claim takeover"
    assert direct_claim in strong_example
    assert supporting_inventory in strong_example
    assert strong_example.index(direct_claim) < strong_example.index(supporting_inventory)
