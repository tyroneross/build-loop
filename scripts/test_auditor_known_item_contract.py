"""Regression guard for the independent auditor's known-item closure gate."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDITOR = ROOT / "agents" / "independent-auditor.md"


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
    body = AUDITOR.read_text(encoding="utf-8")
    for phrase in required:
        assert phrase in body, phrase
