#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""The output contract must REACH the surfaces that emit user-facing text.

`test_output_style_reference.py` asserts what `output-style.md` SAYS. Nothing
asserted that anything READS it, and for a long time nothing did: the contract was
reachable only from Phase 4 Review-G, while `skills/build-loop/SKILL.md` — the body
loaded into every interactive session — never named it. Sessions with the skill
loaded therefore had no output rule at all, and produced non-conforming findings
for a whole session without any check firing.

A doc that is correct and unreferenced is indistinguishable from a doc that does
not exist. These tests grade reach, not content.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "build-loop" / "SKILL.md"
REFS = ROOT / "skills" / "build-loop" / "references"
OUTPUT_STYLE = REFS / "output-style.md"
STATUS_FORMAT = REFS / "status-output-format.md"


class TestContractFilesExist(unittest.TestCase):
    def test_both_references_are_present(self) -> None:
        for path in (OUTPUT_STYLE, STATUS_FORMAT):
            self.assertTrue(path.is_file(), f"missing style reference: {path}")


class TestSkillBodyCarriesTheContract(unittest.TestCase):
    """SKILL.md is what a host model actually reads. If the contract is not here,
    it does not govern an interactive session no matter what Review-G does."""

    def setUp(self) -> None:
        self.body = SKILL.read_text(encoding="utf-8")

    def test_skill_names_both_references(self) -> None:
        for name in ("references/output-style.md", "references/status-output-format.md"):
            self.assertIn(name, self.body, f"SKILL.md never points at {name}")

    def test_contract_is_stated_in_the_body_not_only_in_the_reference_list(self) -> None:
        """A bare bullet in the References list is not a directive. The body must
        carry the rule itself, or a host that never opens the list never sees it."""
        head = self.body.split("## References")[0]
        self.assertIn("references/output-style.md", head,
                      "the style contract appears only in the References list")
        self.assertIn("references/status-output-format.md", head,
                      "the status format appears only in the References list")

    def test_contract_scope_is_not_limited_to_the_final_report(self) -> None:
        """The original defect: the contract read as a Review-G concern only."""
        head = self.body.split("## References")[0]
        self.assertIn("EVERY user-facing message", head)
        self.assertIn("not Review-G-only", head.replace("**", ""))

    def test_body_names_the_self_check_command(self) -> None:
        head = self.body.split("## References")[0]
        self.assertIn("scripts/report_lint.py", head)

    def test_body_states_the_lint_is_not_proof_of_compliance(self) -> None:
        """Three mechanical rules cannot certify a judgment contract. Saying so
        prevents 'lint clean' being read as 'contract met'."""
        head = self.body.split("## References")[0]
        self.assertIn("clean lint is not evidence", head)


class TestLintImplementsWhatTheDocsAdvertise(unittest.TestCase):
    """AGENTS.md advertises three enforced rules. Grade them by BEHAVIOUR, not by
    grepping the source: an earlier draft of this test searched report_lint.py for
    the literal "It's worth noting that" and failed, because the source spells it
    as the regex `It'?s worth noting`. Asserting on implementation text tests the
    spelling of the rule, not whether it fires."""

    def _rules_for(self, text: str) -> set[str]:
        import report_lint
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(text + "\n")
            path = Path(fh.name)
        try:
            result = report_lint.run_lint(path)
        finally:
            path.unlink(missing_ok=True)
        return {f["rule_id"] for f in result["findings"]}

    def test_weak_verb_fires(self) -> None:
        self.assertIn("weak-verb", self._rules_for("The change was responsible for the outage."))

    def test_hedge_fires(self) -> None:
        self.assertIn("hedge", self._rules_for("I think the parser fails."))

    def test_hedge_is_exempt_when_the_line_is_calibrated(self) -> None:
        """A status marker IS calibration, so hedging beside one is required, not padding."""
        self.assertNotIn("hedge", self._rules_for("⚠️ I think the parser fails — untested."))

    def test_filler_openers_named_in_guidance_all_fire(self) -> None:
        for opener in ("Now, the parser fails.",
                       "It's worth noting that the parser fails.",
                       "Basically, the parser fails.",
                       "I'll now fix the parser."):
            with self.subTest(opener=opener):
                self.assertIn("filler-opener", self._rules_for(opener),
                              f"guidance names {opener!r} as filler; the lint let it through")

    def test_clean_prose_raises_no_style_findings(self) -> None:
        """Mutation check in the other direction: if conforming prose still trips a
        rule, the lint would train writers to ignore it."""
        clean = "The migration cut append cost from 10.32 to 8.05 ms/item."
        self.assertEqual(
            self._rules_for(clean) & {"weak-verb", "filler-opener", "hedge"}, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
