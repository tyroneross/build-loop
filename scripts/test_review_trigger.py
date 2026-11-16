#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "review_trigger.py"
sys.path.insert(0, str(HERE))
from review_trigger import build_profile  # noqa: E402


class ReviewTriggerTests(unittest.TestCase):
    def test_empty_context_has_no_trigger(self) -> None:
        profile = build_profile({})
        self.assertFalse(profile["independent_review_required"])
        self.assertEqual(profile["execution_profile"], "skip")
        self.assertEqual(profile["review_steps"], ["deterministic_validation"])

    def test_non_trivial_plan_uses_standard_single_audit_profile(self) -> None:
        profile = build_profile({"non_trivial": True})
        self.assertTrue(profile["independent_review_required"])
        self.assertFalse(profile["cross_vendor_required"])
        self.assertEqual(profile["execution_profile"], "standard")
        self.assertIn("independent_auditor", profile["review_steps"])
        self.assertNotIn("fact_check", profile["review_steps"])

    def test_auth_change_requires_independent_cross_vendor_review(self) -> None:
        profile = build_profile({}, ["app/auth/session.ts"])
        self.assertTrue(profile["independent_review_required"])
        self.assertTrue(profile["cross_vendor_required"])
        self.assertEqual(profile["execution_profile"], "high")
        self.assertIn("auth_change", profile["reasons"])

    def test_ambiguous_risk_fails_conservative(self) -> None:
        profile = build_profile({"riskSurfaceChange": "unknown"})
        self.assertTrue(profile["independent_review_required"])
        self.assertIn("ambiguous_risk_surface_change", profile["reasons"])

    def test_large_architecture_diff_flags_reasons_and_review(self) -> None:
        profile = build_profile({"architectureBoundaryCrossed": True, "lines_changed": 250})
        self.assertTrue(profile["independent_review_required"])
        self.assertIn("large_diff", profile["reasons"])
        self.assertEqual(profile["execution_profile"], "high")

    def test_multi_file_change_uses_standard_profile(self) -> None:
        profile = build_profile({}, ["src/a.py", "src/b.py"])
        self.assertEqual(profile["execution_profile"], "standard")
        self.assertTrue(profile["independent_review_required"])
        self.assertFalse(profile["cross_vendor_required"])

    def test_small_single_file_change_skips_full_loop(self) -> None:
        profile = build_profile({"lines_changed": 8}, ["src/formatting.py"])
        self.assertEqual(profile["execution_profile"], "skip")
        self.assertFalse(profile["independent_review_required"])

    def test_standard_profile_has_signal_triggered_heavy_steps(self) -> None:
        profile = build_profile({"lines_changed": 30}, ["src/formatting.py"])
        self.assertEqual(profile["execution_profile"], "standard")
        self.assertIn("fact_check_on_changed_claims", profile["conditional_review_steps"])
        self.assertIn("simplify_on_complexity_signal", profile["conditional_review_steps"])

    def test_cli_emits_profile_json(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"newDependency": True}, f)
            tmp = f.name
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--context", tmp, "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            Path(tmp).unlink()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["independent_review_required"])
        self.assertIn("new_dependency", payload["reasons"])

    def test_cli_accepts_planned_line_delta(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--lines-changed", "30", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["execution_profile"], "standard")
        self.assertIn("loc_delta", payload["reasons"])



class CompatibilityFocusTests(unittest.TestCase):

    def test_compatibility_focus_reuses_review_and_preserves_high_risk(self):
        profile = build_profile({'compatibility_preservation': True})
        assert profile['execution_profile'] == 'standard'
        assert profile['review_focus'][0]['id'] == 'existing_behavior_compatibility'
        high = build_profile({'compatibility_preservation': True, 'persistence_change': True})
        assert high['cross_vendor_required'] is True
        assert high['execution_profile'] == 'high'
        assert high['review_focus'] == profile['review_focus']
        assert build_profile({})['review_focus'] == []


    def test_unknown_compatibility_keeps_review_focus(self):
        profile = build_profile({'compatibility_preservation': 'unknown'})
        assert profile['independent_review_required']
        assert profile['review_focus']


    def test_existing_ambiguous_nontrivial_behavior_unchanged(self):
        assert build_profile({'nonTrivial': 'unknown'})['execution_profile'] == 'skip'

class InvalidNumericSignalTests(unittest.TestCase):
    def test_invalid_confidence_is_unknown_not_no_risk(self):
        for value in (None, 'unavailable', float('nan'), float('inf'), 10**1000):
            profile = build_profile({'confidence': value})
            self.assertIn('ambiguous_confidence', profile['reasons'])
            self.assertTrue(profile['independent_review_required'])

    def test_invalid_numeric_alias_does_not_hide_valid_later_alias(self):
        profile = build_profile({'criticConfidence': 'unknown', 'confidence': .4})
        self.assertIn('low_confidence', profile['reasons'])
        profile = build_profile({'lines_changed': 'unknown'})
        self.assertIn('ambiguous_loc_delta', profile['reasons'])

class SignalPrecedenceTests(unittest.TestCase):
    def test_top_level_signal_and_true_alias_cannot_be_masked(self):
        self.assertIn('risk_surface_change', build_profile({'meta': {'risk': False}, 'risk': True})['reasons'])
        self.assertIn('new_dependency', build_profile({'newDependency': False, 'dependency_delta': True})['reasons'])

    def test_normalized_collisions_preserve_true_and_conservative_numbers(self):
        for context in ({'riskSurfaceChange': False, 'risk_surface_change': True},
                        {'risk_surface_change': True, 'riskSurfaceChange': False}):
            self.assertIn('risk_surface_change', build_profile(context)['reasons'])
        profile = build_profile({'criticConfidence': .9, 'critic_confidence': .2})
        self.assertIn('low_confidence', profile['reasons'])
        profile = build_profile({'locDelta': 1, 'loc_delta': -250})
        self.assertIn('large_diff', profile['reasons'])

    def test_cli_line_count_overrides_stale_aliases(self):
        with tempfile.TemporaryDirectory() as td:
            context = Path(td) / 'context.json'
            context.write_text(json.dumps({'meta': {'lines_changed': 1}, 'loc_delta': 1, 'locDelta': 1}))
            proc = subprocess.run([sys.executable, str(SCRIPT), '--context', str(context),
                                   '--lines-changed', '250', '--json'], text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('large_diff', json.loads(proc.stdout)['reasons'])

if __name__ == "__main__":
    unittest.main(verbosity=2)
