#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for original-intent recall and judge return routing."""
from __future__ import annotations

import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SOURCE_MARKERS = {
    "skills/build-loop/references/intent-capability-pack.md": (
        "## Request contract",
        "## Original-intent closure contract",
        "coverage: under-captured",
    ),
    "agents/build-orchestrator.md": (
        "stable `RC-*` IDs",
        "completion_routing.action: return_to_orchestrator",
        "review_finding_gate.py",
    ),
    "agents/independent-auditor.md": (
        "## Original-intent and known-gap closure (MANDATORY)",
        '"action": "proceed | return_to_orchestrator | report_blocked | gather_context"',
        "13 sources were spot-checked",
        '"decision_authority": "user | null"',
        '"waiver_expiry": "for waived: expiry date; null otherwise"',
    ),
    "skills/build-loop/SKILL.md": (
        "original `RC-*` request contract",
        "`review_finding_gate.py` original-intent closure check",
        "same-intent open item returns to Execute/Iterate regardless of severity",
    ),
    "skills/build-loop/references/phase-4-review.md": (
        "Finding and original-intent exit gate",
        "top-level `completion_routing`",
        "Only `orchestrator_route.action: proceed` may enter Report",
    ),
    "scripts/audit_before_commit.py": (
        'out("### Active plan\\n")',
        'out("### Current diagnostics\\n")',
        'out("### Open issue / follow-up / backlog queues\\n")',
    ),
    "AGENTS.md": (
        "## Request contract",
        "`return_to_orchestrator` takes precedence",
        "Top-level `completion_routing` is binding",
    ),
}


class OriginalIntentClosureContractTests(unittest.TestCase):
    def test_source_runtime_prompts_recall_the_contract(self) -> None:
        for relative, markers in SOURCE_MARKERS.items():
            text = (REPO / relative).read_text(encoding="utf-8")
            for marker in markers:
                self.assertIn(marker, text, f"{relative} missing {marker!r}")

    def test_generated_codex_runtime_matches_source_contract(self) -> None:
        for relative, markers in SOURCE_MARKERS.items():
            generated = REPO / "plugin-artifacts" / "codex" / relative
            text = generated.read_text(encoding="utf-8")
            for marker in markers:
                self.assertIn(marker, text, f"{generated} missing {marker!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
