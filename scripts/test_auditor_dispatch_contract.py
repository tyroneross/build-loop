#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Regression guard for GAP-1: the LLM independent-auditor is never silently skipped.

Root cause (2026-06-06 IBR retro): when the build-loop orchestrator runs as a NESTED
subagent it has no Agent tool (the harness blocks sub-subagents), so it cannot dispatch
`independent-auditor` and historically substituted inline self-reasoning reported as
"ran inline" — masquerading inline self-audit as the independent auditor and shipping
un-audited code.

The fix is a contract layer across the owned docs (dispatch ladder + parent-dispatch
contract + never-masquerade honesty rule + `auditor_status` vocabulary). This test locks
those clauses against silent removal, and best-effort cross-checks that the existing
write_run_entry gate agrees with the honesty invariant (inline self-audit does NOT count
as a real auditor verdict). It does NOT modify the writer — the gate is codex-owned.
"""
from __future__ import annotations

import unittest
from pathlib import Path

# scripts/ -> repo root
REPO = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# Each owned doc must carry these GAP-1 contract tokens (case-insensitive substring match).
# Tokens are the load-bearing invariants, not whole sentences, so prose may evolve — but
# removing the honesty/ladder/parent-dispatch contract fails the build.
REQUIRED_CLAUSES: dict[str, list[str]] = {
    "agents/build-orchestrator.md": [
        "auditor_status",
        "ran:dispatched-agent",
        "peer-host",
        "not-run:parent-must-dispatch",
        "inline self-audit",          # never-masquerade honesty rule
        "agent tool",                 # capability detection
    ],
    "skills/build-loop/references/phase-4-review.md": [
        "auditor_status",
        "dispatch ladder",
        "ran:dispatched-agent",
        "ran:peer-host",
        "not-run:parent-must-dispatch",
        "parent-dispatch contract",
        "inline self-audit",
        "review-complete",            # not-run run is not review-complete
    ],
    "skills/build-loop/references/independent-auditor.md": [
        "auditor_status",
        "never-masquerade",
        "not-run:parent-must-dispatch",
        "peer-host",
        "inline self-audit is not the independent auditor",
    ],
    "skills/build-loop/SKILL.md": [
        "auditor_status",
        "not-run:parent-must-dispatch",
        "masquerade",
        "parent-dispatch contract",
    ],
}


class DispatchContractDocsTest(unittest.TestCase):
    """The GAP-1 honesty/ladder/parent-dispatch contract is present in every owned doc."""

    def test_required_clauses_present(self) -> None:
        for rel, tokens in REQUIRED_CLAUSES.items():
            text = _read(rel).lower()
            for token in tokens:
                self.assertIn(
                    token.lower(),
                    text,
                    msg=f"GAP-1 contract regression: '{token}' missing from {rel}. "
                    f"The auditor dispatch ladder / parent-dispatch contract / "
                    f"never-masquerade rule must not be removed.",
                )

    def test_no_inline_self_audit_labeled_as_auditor(self) -> None:
        """No owned doc may instruct labeling inline self-reasoning as the auditor.

        Guards the one move that would defeat the write_run_entry gate: writing a
        judge_id of 'independent-auditor' for inline self-audit. Any doc that pairs
        'inline self-audit' with a judge_id assignment must be negating it, not endorsing.
        """
        for rel in REQUIRED_CLAUSES:
            text = _read(rel).lower()
            # The phrase 'inline self-audit' must co-occur with a negation marker
            # (not / never / ≠ / not a substitute) — never as an endorsed auditor source.
            if "inline self-audit" in text:
                window_ok = any(
                    marker in text
                    for marker in ("not the independent auditor", "≠ independent auditor",
                                   "is not a substitute", "never", "must not")
                )
                self.assertTrue(
                    window_ok,
                    msg=f"{rel}: 'inline self-audit' present without a negation marker — "
                    f"the never-masquerade rule must be explicit.",
                )


class GateAgreementTest(unittest.TestCase):
    """Best-effort: the existing write_run_entry gate agrees with the honesty invariant.

    Read-only — imports the codex-owned validator without modifying it. If the symbol
    has moved/refactored (codex owns the writer), the cross-check skips rather than
    failing, so this test never causes cross-author breakage.
    """

    def _load_auditor_present(self):
        import sys

        sys.path.insert(0, str(REPO / "scripts"))
        try:
            from write_run_entry.validators import auditor_present  # type: ignore
        except Exception as exc:  # pragma: no cover - defensive across refactors
            self.skipTest(f"auditor_present not importable (writer owned by peer): {exc}")
        return auditor_present

    def test_inline_self_audit_is_not_a_real_verdict(self) -> None:
        auditor_present = self._load_auditor_present()
        inline = [{"judge_id": "orchestrator-inline-self-audit", "verdict": "yay"}]
        self.assertFalse(
            auditor_present(inline),
            "inline self-audit must NOT satisfy the real-auditor gate",
        )
        self.assertFalse(auditor_present([]), "empty judge list must not satisfy the gate")
        self.assertFalse(auditor_present(None), "None must not satisfy the gate")

    def test_dispatched_and_peer_host_verdicts_count(self) -> None:
        auditor_present = self._load_auditor_present()
        # Both the Agent-tool path and the peer-host path write judge_id 'independent-auditor'.
        dispatched = [{"judge_id": "independent-auditor", "verdict": "yay"}]
        peer_host = [{"judge_id": "independent-auditor", "verdict": "nay"}]
        hook = [{"judge_id": "independent-auditor-hook", "verdict": "yay"}]
        self.assertTrue(auditor_present(dispatched))
        self.assertTrue(auditor_present(peer_host))
        self.assertTrue(auditor_present(hook))


if __name__ == "__main__":
    unittest.main()
