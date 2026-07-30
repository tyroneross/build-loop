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

    def test_non_trivial_plan_does_not_trigger_independent_review(self) -> None:
        profile = build_profile({"non_trivial": True})
        self.assertFalse(profile["independent_review_required"])
        self.assertFalse(profile["cross_vendor_required"])

    def test_auth_change_requires_independent_cross_vendor_review(self) -> None:
        profile = build_profile({}, ["app/auth/session.ts"])
        self.assertTrue(profile["independent_review_required"])
        self.assertTrue(profile["cross_vendor_required"])
        self.assertIn("auth_change", profile["reasons"])

    def test_ambiguous_risk_fails_conservative(self) -> None:
        profile = build_profile({"riskSurfaceChange": "unknown"})
        self.assertTrue(profile["independent_review_required"])
        self.assertIn("ambiguous_risk_surface_change", profile["reasons"])

    def test_large_architecture_diff_flags_reasons_and_review(self) -> None:
        profile = build_profile({"architectureBoundaryCrossed": True, "lines_changed": 250})
        self.assertTrue(profile["independent_review_required"])
        self.assertIn("large_diff", profile["reasons"])

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
