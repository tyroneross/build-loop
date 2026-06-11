#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Contract + anti-dormancy guard for the Advisor dispatch ladder (trio v1).

Mirrors `test_auditor_dispatch_contract.py` (the proven GAP-1 pattern). Two jobs:

1. **Contract** — every owned doc carries the load-bearing ladder/honesty tokens,
   so the 4-rung ladder, the `advisor_status` taxonomy, and the stakes-gating can't
   be silently removed.

2. **Anti-dormancy** — this codebase's known failure mode is shipping a feature that
   exists in code but never fires. These tests prove the ladder + ledger are actually
   WIRED, not orphaned:
     - the orchestrator doc references the `advisor` agent AND the ladder reference
       AND the ledger writer (a wiring path, not just a definition);
     - the `advisor` agent's frontmatter declares every tool its body names (internal
       consistency — an agent referencing a tool it lacks is dormant-by-construction);
     - the ledger accepts a representative `action: author` advisor row end-to-end
       (the instrument actually records an Advisor action).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# scripts/ -> repo root
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import agent_ledger  # noqa: E402


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# Load-bearing invariants per owned doc (case-insensitive substring match). Tokens are
# the contract, not whole sentences — prose may evolve, the contract must not vanish.
REQUIRED_CLAUSES: dict[str, list[str]] = {
    "skills/build-loop/references/advisor-dispatch-ladder.md": [
        "advisor_status",
        "inline-frontier",          # Rung 0
        "ran:dispatched-agent",     # Rung 1
        "ran:peer-host",            # Rung 2
        "fallback:inline-opus",     # Rung 3
        "non-breaking",             # the floor == today's behavior
        "stakes-gating",
        "synthesisdensity > 5",
        "risksurfacechange",
        "never self-reported confidence",  # objective-signal rule
        "agent_ledger.py",          # the instrument is wired in
        "separate agent",           # not the executor self-reflecting
    ],
    "agents/build-orchestrator.md": [
        "advisor_status",
        "advisor dispatch ladder",
        "inline-frontier",
        "ran:dispatched-agent",
        "fallback:inline-opus",
        "agent_ledger.py",
        "build-loop:advisor",       # the orchestrator dispatches the agent
    ],
    "agents/advisor.md": [
        "generating is harder than evaluating",  # the core principle
        "planning miss",
        "execution miss",
        "corrected instructions",
        "never self-certif",        # always-verified rule
        "separate* agent",          # not the executor self-reflecting (markdown emphasis tolerated)
        "out of v1 scope",          # take-over is v2
    ],
    "skills/spec-writing/SKILL.md": [
        "frontier",                 # the enum gained frontier
        "advisor dispatch ladder",
    ],
}


class DispatchContractDocsTest(unittest.TestCase):
    """The ladder / honesty / stakes-gating contract is present in every owned doc."""

    def test_required_clauses_present(self) -> None:
        for rel, tokens in REQUIRED_CLAUSES.items():
            text = _read(rel).lower()
            for token in tokens:
                self.assertIn(
                    token.lower(),
                    text,
                    msg=f"Advisor-trio contract regression: '{token}' missing from {rel}. "
                    f"The 4-rung ladder / advisor_status taxonomy / stakes-gating / "
                    f"objective-signal rule must not be removed.",
                )

    def test_rung_3_equals_today_no_regression_clause(self) -> None:
        """The non-breaking guarantee (Rung 3 == today's inline behavior) must be explicit."""
        text = _read("skills/build-loop/references/advisor-dispatch-ladder.md").lower()
        self.assertTrue(
            "floor" in text and ("today" in text or "current state" in text),
            "the non-breaking guarantee (Rung 3 floor == current behavior) must be stated",
        )

    def test_objective_signal_not_self_reported_confidence(self) -> None:
        """Escalation/advance triggers must be objective signals, never self-reported confidence."""
        for rel in ("skills/build-loop/references/advisor-dispatch-ladder.md", "agents/advisor.md"):
            text = _read(rel).lower()
            self.assertIn(
                "objective",
                text,
                msg=f"{rel}: the objective-verifier-signal rule must be explicit (research: "
                f"models are overconfident; self-reported confidence is never a trigger).",
            )


class AdvisorAgentConsistencyTest(unittest.TestCase):
    """The advisor agent's frontmatter declares every tool its body actually names.

    An agent that references a tool it doesn't have in `tools:` is dormant-by-
    construction (the brief-discipline guardrail in Phase 3). This locks internal
    consistency for the new agent def.
    """

    def _frontmatter_tools(self, text: str) -> set[str]:
        import re
        m = re.search(r'^tools:\s*\[(.*?)\]', text, re.MULTILINE)
        if not m:
            return set()
        return {t.strip().strip('"').strip("'") for t in m.group(1).split(",") if t.strip()}

    def test_advisor_declares_write_and_read_tools(self) -> None:
        text = _read("agents/advisor.md")
        tools = self._frontmatter_tools(text)
        # The body uses Read/Grep/Glob (read intent+goal), Skill (load spec-writing),
        # and Write (plan artifacts). All must be declared.
        for required in ("Read", "Grep", "Glob", "Skill", "Write"):
            self.assertIn(
                required,
                tools,
                msg=f"advisor.md body references {required} but it's missing from tools: {sorted(tools)}",
            )

    def test_advisor_is_fable_tier(self) -> None:
        import re
        text = _read("agents/advisor.md")
        self.assertTrue(
            re.search(r"^model:\s*fable\s*$", text, re.MULTILINE),
            "advisor must be model: fable (Frontier tier)",
        )


class LedgerWiringTest(unittest.TestCase):
    """The ledger actually records an Advisor action end-to-end (instrument not dormant)."""

    def test_ledger_accepts_advisor_author_row(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".build-loop" / "agent-ledger.jsonl"
            # The exact shape the orchestrator appends per Advisor action.
            row = agent_ledger.build_row(
                run_id="trio-r1",
                agent="advisor",
                action="author",
                phase="2",
                tier="frontier",
                model="fable",
                rung=1,
                status="pass",
                trigger="riskSurfaceChange",
                refs={"output": "docs/plans/x.md"},
            )
            env = agent_ledger.append(path, row)
            self.assertTrue(env["ok"], env)
            summary = agent_ledger.summarize(agent_ledger.read(path))
            self.assertEqual(summary["advisor_invocations"], 1)
            self.assertEqual(summary["by_action"]["author"], 1)
            self.assertEqual(summary["by_agent_model"]["advisor:fable"], 1)
            self.assertEqual(summary["by_rung"]["1"], 1)

    def test_ledger_records_replan_action(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.jsonl"
            agent_ledger.append(path, agent_ledger.build_row(
                run_id="trio-r1", agent="advisor", action="re-plan",
                tier="frontier", model="fable", rung=2, status="pass",
                trigger="planning-miss", note="chunk-4 scope breach; retry justified",
            ))
            rows = agent_ledger.read(path)
            self.assertEqual(rows[0]["action"], "re-plan")
            self.assertEqual(rows[0]["trigger"], "planning-miss")


if __name__ == "__main__":
    unittest.main(verbosity=2)
