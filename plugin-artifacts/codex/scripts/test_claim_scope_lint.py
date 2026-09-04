#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for claim_scope_lint.

The fixtures are the LITERAL sentences from the 2026-09-04 incident that
motivated the rule, plus their corrected forms. A test that only asserts the
clean path certifies the hole, so every defect fixture is a sentence that was
actually shipped and actually wrong.
"""
from __future__ import annotations

import unittest

from claim_scope_lint import (
    L1_TREE, L2_REPO, L3_DEPLOYED, L4_LIVE,
    claim_layer, instrument_reach, lint,
)


class TestClaimLayer(unittest.TestCase):
    def test_absence_of_a_dependency_is_a_repository_claim(self):
        layer, _ = claim_layer("`@vercel/blob` is not a dependency in package.json at all.")
        self.assertEqual(layer, L2_REPO)

    def test_zero_consumers_is_a_repository_claim(self):
        layer, _ = claim_layer("siteSubTitle has ZERO consumers in the codebase.")
        self.assertEqual(layer, L2_REPO)

    def test_lives_only_in_is_a_repository_claim(self):
        layer, _ = claim_layer("The audio lives only in Postgres.")
        self.assertEqual(layer, L2_REPO)

    def test_deployed_is_a_deployed_claim(self):
        layer, _ = claim_layer("The blob code is deployed.")
        self.assertEqual(layer, L3_DEPLOYED)

    def test_response_code_is_a_live_claim(self):
        layer, _ = claim_layer("The endpoint returns a 200.")
        self.assertEqual(layer, L4_LIVE)

    def test_plain_prose_is_not_a_claim(self):
        layer, _ = claim_layer("This function parses the range header.")
        self.assertIsNone(layer)


class TestInstrumentReach(unittest.TestCase):
    def test_grep_reaches_only_the_working_tree(self):
        reach, _ = instrument_reach("a named grep across the entire repo returns three hits")
        self.assertEqual(reach, L1_TREE)

    def test_git_log_against_origin_reaches_the_repository(self):
        reach, _ = instrument_reach("git log HEAD..origin/main shows the merge")
        self.assertEqual(reach, L2_REPO)

    def test_git_log_alone_does_not_reach_the_repository(self):
        reach, _ = instrument_reach("git log shows the last eight commits")
        self.assertEqual(reach, L1_TREE)

    def test_vercel_ls_reaches_deployed(self):
        reach, _ = instrument_reach("vercel ls reports the deployment is Ready")
        self.assertEqual(reach, L3_DEPLOYED)

    def test_response_headers_reach_live(self):
        reach, _ = instrument_reach("accept-ranges: none came back on the request")
        self.assertEqual(reach, L4_LIVE)


class TestLintCatchesTheIncident(unittest.TestCase):
    """Every string here was shipped to the user on 2026-09-04 and was wrong."""

    SHIPPED_AND_WRONG = [
        "`@vercel/blob` is not a dependency in the app's package.json at all.",
        "No code in any of the sibling repos writes podcast audio to blob.",
        "There is no blob copy of the podcast audio to switch to.",
        "siteSubTitle has ZERO consumers in the codebase — a named grep across "
        "the entire repo returns three hits.",
        "The audio lives only in Postgres, so switching the source is not possible.",
    ]

    def test_every_shipped_defect_is_caught(self):
        for sentence in self.SHIPPED_AND_WRONG:
            with self.subTest(sentence=sentence[:50]):
                self.assertTrue(lint(sentence), f"missed: {sentence}")

    def test_grep_sentence_names_the_reach_gap(self):
        """The flagship case: instrument named, and still insufficient."""
        findings = lint(self.SHIPPED_AND_WRONG[3])
        self.assertEqual(findings[0]["rule_id"], "claim-scope-exceeds-instrument")
        self.assertIn("blind to", findings[0]["message"])


class TestLintAcceptsCorrectedForms(unittest.TestCase):
    CORRECTED = [
        "No blob dependency in the working tree of branch fix/embedding at commit 3fe5298.",
        "Verified live at 07:47Z against production: Range returns HTTP/2 200 with "
        "accept-ranges: none.",
        "vercel ls reports production deployment dpl-7f3a91c is Ready, so the "
        "blob code is deployed.",
        "No blob dependency [L1@3fe5298].",
    ]

    def test_no_false_positives_on_corrected_forms(self):
        for sentence in self.CORRECTED:
            with self.subTest(sentence=sentence[:50]):
                self.assertEqual(lint(sentence), [], f"false positive: {sentence}")


class TestPrecisionSuppressors(unittest.TestCase):
    """Measured against real retrospectives: these shapes were 5/5 noise."""

    QUIET = [
        "f6 is the only fix that ends the recurring closeout pattern.",
        "Two permanent-FAIL latches would have broken every future run.",
        "The latches would have made this a permanent blocker in any repo with history.",
        "If there is no run history, the gate should skip.",
        "We plan to deploy this to production next week.",
    ]

    def test_judgments_and_counterfactuals_stay_quiet(self):
        for sentence in self.QUIET:
            with self.subTest(sentence=sentence[:50]):
                self.assertEqual(lint(sentence), [], f"noise: {sentence}")

    def test_code_artifact_exclusivity_still_fires(self):
        """Tightening 'is the only' must not blind it to real absence claims."""
        self.assertTrue(lint("route.ts is the only caller of getEpisodeAudioBytes."))


class TestFencedCodeIsSkipped(unittest.TestCase):
    def test_code_blocks_do_not_produce_findings(self):
        text = "```\nassert x is not a dependency\n```\n"
        self.assertEqual(lint(text), [])


if __name__ == "__main__":
    unittest.main()
