#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for attestation_lint.py. Stdlib only. Run: python3 scripts/test_attestation_lint.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "attestation_lint.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS_PLACEMENT_DIFF = """\
--- a/app/components/Button.tsx
+++ b/app/components/Button.tsx
@@ -1,5 +1,8 @@
 import React from 'react';
+
+const PrimaryButton = () => <button className="btn-primary">Click</button>;
+
 export function Button() {
   return <button>click</button>;
 }
"""

FAIL_PLACEMENT_DIFF = """\
--- a/app/components/Button.tsx
+++ b/app/components/Button.tsx
@@ -10,6 +10,9 @@
 function Footer() {
   return <footer>Footer</footer>;
 }
+
+const UnrelatedThing = () => null;
+
"""

CTA_DIFF = """\
--- a/app/page.tsx
+++ b/app/page.tsx
@@ -1,4 +1,7 @@
 export default function Page() {
+  return <Button variant="primary">Submit</Button>;
 }
"""

VISUAL_WEIGHT_DIFF = """\
--- a/app/page.tsx
+++ b/app/page.tsx
@@ -1,4 +1,7 @@
 export default function Page() {
+  return <h2 className="font-bold">Section Title</h2>;
 }
"""

EMPTY_DIFF = """\
--- a/app/page.tsx
+++ b/app/page.tsx
@@ -1,3 +1,3 @@
 export default function Page() {
-  return null;
+  return null;
 }
"""


def _write_envelope(attestation: dict) -> str:
    data = {"synthesis_attestation": attestation}
    f = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False
    )
    json.dump(data, f)
    f.flush()
    return f.name


def _write_diff(diff_text: str) -> str:
    f = tempfile.NamedTemporaryFile(
        "w", suffix=".diff", delete=False
    )
    f.write(diff_text)
    f.flush()
    return f.name


def run_script(diff_path: str, envelope_path: str, extra: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), "--diff", diff_path, "--envelope", envelope_path, "--json"]
    if extra:
        cmd.extend(extra)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


# ---------------------------------------------------------------------------
# F2: placement — pass case
# ---------------------------------------------------------------------------

class PlacementLintTests(unittest.TestCase):
    """F2 — placement is lintable."""

    def test_placement_pass(self) -> None:
        """Anchor exists; added lines appear near anchor — PASS."""
        diff_path = _write_diff(PASS_PLACEMENT_DIFF)
        # The anchor "import React from 'react'" is at line 1 of pre-image.
        # Added lines start at line 2 of post-image (within ±5).
        envelope_path = _write_envelope({
            "placement": "after import React from 'react' in app/components/Button.tsx",
        })
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 0, f"expected exit 0 (pass)\nstdout: {r.stdout}\nstderr: {r.stderr}")
            payload = json.loads(r.stdout)
            results = payload["results"]
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["dimension"], "placement")
            self.assertEqual(results[0]["status"], "pass", results[0]["evidence"])
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)

    def test_placement_fail_missing_file(self) -> None:
        """File not in diff — FAIL, exit 1."""
        diff_path = _write_diff(PASS_PLACEMENT_DIFF)
        envelope_path = _write_envelope({
            "placement": "after SomeAnchor in app/components/NonExistent.tsx",
        })
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 1, f"expected exit 1 (fail)\nstdout: {r.stdout}")
            payload = json.loads(r.stdout)
            results = payload["results"]
            self.assertEqual(results[0]["status"], "fail")
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)

    def test_placement_fail_no_added_lines(self) -> None:
        """File in diff but only modifications, not additions at anchor — FAIL."""
        diff_path = _write_diff(FAIL_PLACEMENT_DIFF)
        # The anchor "import React" does NOT appear in this diff at all
        envelope_path = _write_envelope({
            "placement": "after import React from 'react' in app/components/Button.tsx",
        })
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 1, f"expected exit 1 (fail)\nstdout: {r.stdout}")
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# F2: cta_tier — pass / fail
# ---------------------------------------------------------------------------

class CtaTierLintTests(unittest.TestCase):
    """F2 — cta_tier is lintable."""

    def test_cta_tier_pass(self) -> None:
        """primary variant found in diff — PASS."""
        diff_path = _write_diff(CTA_DIFF)
        envelope_path = _write_envelope({"cta_tier": "primary"})
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 0, f"stdout: {r.stdout}")
            payload = json.loads(r.stdout)
            self.assertEqual(payload["results"][0]["status"], "pass")
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)

    def test_cta_tier_fail(self) -> None:
        """secondary claimed but only primary in diff — FAIL."""
        diff_path = _write_diff(CTA_DIFF)
        envelope_path = _write_envelope({"cta_tier": "secondary"})
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 1, f"stdout: {r.stdout}")
            payload = json.loads(r.stdout)
            self.assertEqual(payload["results"][0]["status"], "fail")
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)

    def test_cta_tier_unknown_unverifiable(self) -> None:
        """unknown tier — unverifiable (not in known set), exit 2."""
        diff_path = _write_diff(CTA_DIFF)
        envelope_path = _write_envelope({"cta_tier": "quaternary"})
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 2, f"stdout: {r.stdout}")
            payload = json.loads(r.stdout)
            self.assertEqual(payload["results"][0]["status"], "unverifiable")
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# F2: visual_weight — pass / fail
# ---------------------------------------------------------------------------

class VisualWeightLintTests(unittest.TestCase):
    """F2 — visual_weight is lintable."""

    def test_visual_weight_pass(self) -> None:
        """h2 element found in diff — PASS."""
        diff_path = _write_diff(VISUAL_WEIGHT_DIFF)
        envelope_path = _write_envelope({"visual_weight": "h2 heading"})
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 0, f"stdout: {r.stdout}")
            payload = json.loads(r.stdout)
            self.assertEqual(payload["results"][0]["status"], "pass")
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)

    def test_visual_weight_fail(self) -> None:
        """heading claimed but diff has no heading elements — FAIL."""
        diff_path = _write_diff(EMPTY_DIFF)
        envelope_path = _write_envelope({"visual_weight": "h2 heading"})
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 1, f"stdout: {r.stdout}")
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# F3: subjective dimensions — always unverifiable
# ---------------------------------------------------------------------------

class SubjectiveDimensionsTests(unittest.TestCase):
    """F3 — copy_tone and empty_state always return unverifiable."""

    def test_copy_tone_unverifiable(self) -> None:
        diff_path = _write_diff(PASS_PLACEMENT_DIFF)
        envelope_path = _write_envelope({"copy_tone": "professional and concise"})
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 2, f"stdout: {r.stdout}")
            payload = json.loads(r.stdout)
            self.assertEqual(payload["results"][0]["status"], "unverifiable")
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)

    def test_empty_state_unverifiable(self) -> None:
        diff_path = _write_diff(PASS_PLACEMENT_DIFF)
        envelope_path = _write_envelope({"empty_state": "friendly illustration + CTA"})
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 2, f"stdout: {r.stdout}")
            payload = json.loads(r.stdout)
            self.assertEqual(payload["results"][0]["status"], "unverifiable")
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# F5: exit codes
# ---------------------------------------------------------------------------

class ExitCodeTests(unittest.TestCase):
    """F5 — exit codes distinguish pass / fail / unverifiable."""

    def test_exit_0_on_pass(self) -> None:
        diff_path = _write_diff(CTA_DIFF)
        envelope_path = _write_envelope({"cta_tier": "primary"})
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 0)
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)

    def test_exit_1_on_fail(self) -> None:
        diff_path = _write_diff(CTA_DIFF)
        envelope_path = _write_envelope({"cta_tier": "secondary"})
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 1)
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)

    def test_exit_2_on_all_unverifiable(self) -> None:
        diff_path = _write_diff(PASS_PLACEMENT_DIFF)
        envelope_path = _write_envelope({"copy_tone": "friendly"})
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 2)
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)

    def test_exit_2_on_missing_envelope(self) -> None:
        """Missing envelope file — exit 2."""
        diff_path = _write_diff(PASS_PLACEMENT_DIFF)
        try:
            r = run_script(diff_path, "/nonexistent/envelope.json")
            self.assertEqual(r.returncode, 2)
        finally:
            Path(diff_path).unlink(missing_ok=True)

    def test_exit_2_on_malformed_envelope(self) -> None:
        """Missing synthesis_attestation field — exit 2 with warning (malformed_envelope_handling=warn)."""
        diff_path = _write_diff(PASS_PLACEMENT_DIFF)
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"status": "completed", "files_changed": []}, f)
        f.flush()
        envelope_path = f.name
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 2, f"stdout: {r.stdout}")
            payload = json.loads(r.stdout)
            self.assertIn("warning", payload)
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Mixed results: pass + fail → exit 1 (fail takes precedence)
# ---------------------------------------------------------------------------

class MixedResultTests(unittest.TestCase):
    """When dimensions include both pass and fail, exit 1."""

    def test_fail_dominates_pass(self) -> None:
        """cta_tier=primary passes, cta_tier=secondary fails → exit 1."""
        diff_path = _write_diff(CTA_DIFF)
        # Two dimensions: one will pass, one will fail
        envelope_path = _write_envelope({
            "cta_tier": "secondary",    # FAIL — not in diff
            "copy_tone": "professional",  # unverifiable
        })
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 1)
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)

    def test_pass_and_unverifiable_exit_0(self) -> None:
        """pass + unverifiable with no fails → exit 0."""
        diff_path = _write_diff(CTA_DIFF)
        envelope_path = _write_envelope({
            "cta_tier": "primary",      # PASS
            "copy_tone": "professional",  # unverifiable
        })
        try:
            r = run_script(diff_path, envelope_path)
            self.assertEqual(r.returncode, 0)
        finally:
            Path(diff_path).unlink(missing_ok=True)
            Path(envelope_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
