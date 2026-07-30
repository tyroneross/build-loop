# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/backlog/triage.classify (F4 of the retro+backlog spec)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/ on path so `import backlog.triage` works.

from backlog.triage import classify  # noqa: E402


class ClassifyTests(unittest.TestCase):
    # ----- True (product-impacting) -----

    def test_broken_save_button(self) -> None:
        r = classify("fix the broken Save button on the dashboard", {})
        self.assertTrue(r["product_impacting"])
        self.assertIsNotNone(r["impact"])
        self.assertIn("matched", r["rationale"])

    def test_login_error_message(self) -> None:
        r = classify("login fails with a confusing error message")
        self.assertTrue(r["product_impacting"])
        self.assertIsNotNone(r["impact"])

    def test_slow_dashboard_load(self) -> None:
        r = classify("dashboard is slow to load on first paint")
        self.assertTrue(r["product_impacting"])

    def test_accessibility_keyboard_nav(self) -> None:
        r = classify("keyboard nav broken on the settings page")
        self.assertTrue(r["product_impacting"])

    def test_data_integrity_wrong_amount(self) -> None:
        r = classify("checkout shows wrong amount when coupon applied")
        self.assertTrue(r["product_impacting"])

    def test_render_failure(self) -> None:
        r = classify("chart loads incorrectly when there are zero data points")
        self.assertTrue(r["product_impacting"])

    # ----- False (NOT product-impacting) -----

    def test_internal_rename(self) -> None:
        r = classify("rename internal function helper_x to helper_y", {})
        self.assertFalse(r["product_impacting"])
        self.assertIsNone(r["impact"])

    def test_doc_typo(self) -> None:
        r = classify("doc typo in README — 'occured' → 'occurred'")
        self.assertFalse(r["product_impacting"])

    def test_test_coverage_only(self) -> None:
        r = classify("add a test for the parse_args helper")
        self.assertFalse(r["product_impacting"])

    def test_lint_cleanup(self) -> None:
        r = classify("lint cleanup — remove unused imports across scripts/")
        self.assertFalse(r["product_impacting"])

    def test_empty_text(self) -> None:
        r = classify("", {})
        self.assertFalse(r["product_impacting"])
        self.assertIn("empty", r["rationale"])

    def test_no_surface_keyword(self) -> None:
        r = classify("bump the python version in pyproject")
        self.assertFalse(r["product_impacting"])

    # ----- Override: internal framing + broken-behavior wins True -----

    def test_internal_framing_but_broken_user_behavior_wins_true(self) -> None:
        """A 'refactor internal' deferral that mentions user-broken behavior is
        still product-impacting (broken-behavior overrides internal-only)."""
        r = classify("refactor internal navigation handler — currently broken when user signs in")
        self.assertTrue(r["product_impacting"])

    # ----- Defect-2: bare 'user' mention does NOT impersonate auth/sign-in -----

    def test_miner_issue_does_not_mislabel_as_signin(self) -> None:
        """The miner-issue string ('count isMeta hook-injections as USER
        corrections in the miner') is about self-improvement pipeline
        correctness, not user account / sign-in flow. It MUST still be
        product-impacting (system correctness is product correctness for a
        build orchestrator), but the impact label MUST be internal-correctness,
        not the auth/sign-in flow label that the old bare-'user' regex emitted.
        """
        r = classify("count isMeta hook-injections as USER corrections in the miner")
        self.assertTrue(r["product_impacting"])
        self.assertIsNotNone(r["impact"])
        self.assertNotEqual(r["impact"], "user account / sign-in flow")
        self.assertIn("internal", (r["impact"] or "").lower())

    def test_genuine_auth_string_still_labels_as_signin(self) -> None:
        """A real auth/sign-in deferral still gets the user-flow label. Uses a
        sentence whose first surface match is the auth lane (no upstream lane
        words like 'broken' / 'page' / 'button')."""
        r = classify("users cannot log in after password reset")
        self.assertTrue(r["product_impacting"])
        self.assertEqual(r["impact"], "user account / sign-in flow")

    def test_bare_user_word_without_auth_signal_no_longer_triggers_user_flow(self) -> None:
        """Pre-fix regression: the bare token 'user' matched user-flow and
        emitted the sign-in label for arbitrary internal text. Now the auth
        lane requires a real identity-flow signal."""
        r = classify("when the user types a long string the parser is slow")
        # 'slow' triggers performance lane — still product-impacting True, but
        # the impact MUST NOT be the auth/sign-in label.
        self.assertTrue(r["product_impacting"])
        self.assertNotEqual(r["impact"], "user account / sign-in flow")

    # ----- Shape -----

    def test_return_shape(self) -> None:
        r = classify("anything")
        self.assertEqual(set(r.keys()), {"product_impacting", "impact", "rationale"})
        self.assertIsInstance(r["product_impacting"], bool)
        self.assertIsInstance(r["rationale"], str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
