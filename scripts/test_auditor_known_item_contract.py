"""Regression guard for the independent auditor's known-item closure gate."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SURFACES = (
    ROOT / "agents" / "independent-auditor.md",
    ROOT / "plugin-artifacts" / "codex" / "agents" / "independent-auditor.md",
)


def test_known_item_gate_is_binding_and_packaged() -> None:
    required = (
        "known_open_items",
        "Known-item closure gate (MANDATORY on every audit)",
        "A report, diagnostic, backlog entry, or plan records the issue; it does not close it.",
        "A bounded spot-check does not close an exhaustive acceptance criterion.",
        'forces `verdict: "nay"` with a `high` finding',
        "known_item_closure",
        "binding loop-control result",
    )
    bodies = [path.read_text(encoding="utf-8") for path in SURFACES]
    assert bodies[0] == bodies[1], "Codex packaged auditor must match the source prompt"
    for phrase in required:
        assert phrase in bodies[0], phrase
