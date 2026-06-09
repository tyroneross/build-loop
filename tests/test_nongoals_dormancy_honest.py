# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Guard against the dormant-determinism claim recurring (bl-enforced-nongoals-dormant).

The intent-capability-pack USED to claim that a risk-naming ``non_goals`` entry at
``stakes: high`` "graduates from advisory to an ENFORCED invariant (the single
deterministic carve-out)". Nothing in ``scripts/`` ever read ``non_goals`` to enforce
anything (``grep non_goals scripts/`` is empty), so the determinism claim was dormant
by declaration — exactly the dormancy lesson class.

The honest framing (option a, 2026-06-09): a high-stakes ``non_goals`` graduates ONLY
when promoted into the project constitution, where the LLM enforces it. This test locks
that honesty in two directions:

1. If a future doc edit re-introduces a "deterministic"/"ENFORCED invariant" claim about
   ``non_goals``, this test fails unless a real enforcement script also exists.
2. The "promoted into the project constitution" framing must stay present.

Either the doc stays honest, or someone wires the determinism for real — both close the
dormancy gap. The test cannot be satisfied by a dormant claim.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "skills" / "build-loop" / "references" / "intent-capability-pack.md"
SCRIPTS = REPO / "scripts"


def _nongoals_enforcement_exists_in_scripts() -> bool:
    """True iff any script actually reads ``non_goals`` (a real determinism wiring)."""
    if not SCRIPTS.exists():
        return False
    for path in SCRIPTS.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "non_goals" in text:
            return True
    return False


def test_pack_exists() -> None:
    assert PACK.exists(), f"intent-capability-pack.md missing at {PACK}"


def test_no_dormant_determinism_claim() -> None:
    """The pack must not claim deterministic non_goals enforcement without code to back it."""
    text = PACK.read_text(encoding="utf-8")
    # Find the non_goals discussion region (the bullet + the tiered-charter mentions).
    # A claim of deterministic / ENFORCED-invariant enforcement is only honest if a
    # script actually reads non_goals. We allow the words to appear ONLY when paired
    # with the constitution-promotion framing OR a real enforcement script exists.
    has_enforcement = _nongoals_enforcement_exists_in_scripts()

    # Hard-dormant phrasings that asserted a standalone deterministic carve-out.
    dormant_patterns = [
        r"single\s+deterministic\s+carve-out",
        r"graduates?\s+from\s+advisory\s+to\s+an?\s+ENFORCED\s+invariant",
    ]
    for pat in dormant_patterns:
        if re.search(pat, text, re.IGNORECASE) and not has_enforcement:
            raise AssertionError(
                f"intent-capability-pack.md still carries the dormant determinism claim "
                f"matching /{pat}/ but no scripts/ file reads non_goals to enforce it. "
                f"Either soften the doc to constitution-promotion framing or wire the "
                f"enforcement (bl-enforced-nongoals-dormant)."
            )


def test_constitution_promotion_framing_present() -> None:
    """The honest framing — graduation rides constitution promotion — must stay."""
    text = PACK.read_text(encoding="utf-8").lower()
    assert "constitution" in text, (
        "The honest non_goals framing routes enforcement through constitution promotion; "
        "that framing is missing from intent-capability-pack.md."
    )
    # The pack must explicitly tie non_goals graduation to the constitution.
    assert re.search(r"non_goals.{0,400}constitution", text, re.DOTALL) or re.search(
        r"constitution.{0,400}non_goals", text, re.DOTALL
    ), "intent-capability-pack.md must tie non_goals graduation to constitution promotion."
