#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for methodology_drift_lint.py — the single-source methodology guard.

Stdlib only. Run: python3 -m pytest scripts/test_methodology_drift_lint.py
"""
from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import methodology_drift_lint as mdl  # noqa: E402


CANON_SAMPLE = textwrap.dedent(
    """
    # Methodology Core

    ### INV-REVIEW-SUBSTEPS  (ENFORCED)
    Some prose.

    Canonical phrase (must appear verbatim):

    > Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Auto-Resolve → Report

    ### INV-OTHER
    No canonical phrase here, so it is not enforced.
    """
)


class ParseCanonicalTests(unittest.TestCase):
    def test_parses_enforced_invariant_with_suffix(self) -> None:
        invs = mdl.parse_canonical(CANON_SAMPLE)
        ids = [i["id"] for i in invs]
        self.assertIn("INV-REVIEW-SUBSTEPS", ids)

    def test_skips_invariant_without_canonical_phrase(self) -> None:
        invs = mdl.parse_canonical(CANON_SAMPLE)
        ids = [i["id"] for i in invs]
        self.assertNotIn("INV-OTHER", ids)

    def test_extracts_the_blockquote_phrase(self) -> None:
        invs = mdl.parse_canonical(CANON_SAMPLE)
        phrase = invs[0]["phrases"][0]
        self.assertEqual(
            phrase,
            "Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Auto-Resolve → Report",
        )


class RealRepoIsCleanTests(unittest.TestCase):
    """The shipped repo must have zero methodology drift — this is the guard."""

    def test_no_drift_in_repo(self) -> None:
        result = mdl.check(REPO_ROOT)
        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(
            result["findings"], [],
            f"methodology drift in repo: {result['findings']}",
        )

    def test_at_least_one_invariant_enforced(self) -> None:
        result = mdl.check(REPO_ROOT)
        self.assertGreaterEqual(len(result["invariants_checked"]), 1)


class DriftDetectionTests(unittest.TestCase):
    """A satellite missing the canonical phrase is reported as drift."""

    def test_missing_phrase_is_flagged(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "references").mkdir()
            (root / "references" / "methodology-core.md").write_text(CANON_SAMPLE, encoding="utf-8")
            # Create all four satellite paths; three carry the phrase, one drifts.
            for sat in mdl.SATELLITES:
                p = root / sat
                p.parent.mkdir(parents=True, exist_ok=True)
                if sat == "CLAUDE.md":
                    p.write_text("Review: Critic → Validate → Fact-Check → Report\n", encoding="utf-8")
                else:
                    p.write_text(
                        "Critic → Validate → Optimize (opt-in) → Fact-Check → "
                        "Simplify → Auto-Resolve → Report\n",
                        encoding="utf-8",
                    )
            result = mdl.check(root)
            sats = {f["satellite"] for f in result["findings"]}
            self.assertIn("CLAUDE.md", sats)
            self.assertNotIn("AGENTS.md", sats)


class CanonicalIsParameterizedTests(unittest.TestCase):
    """check(repo_root) must read the canonical FROM repo_root, not the module
    global. Guards the bug where --repo-root relocated only the satellites."""

    def test_temp_canonical_phrase_is_used(self) -> None:
        import tempfile

        # A phrase that does NOT appear in the real repo's methodology docs.
        unique = "ZZTOP -> UNIQUE -> SENTINEL -> PHRASE"
        canon = textwrap.dedent(
            f"""
            ### INV-SENTINEL  (ENFORCED)
            Canonical phrase (must appear):

            > {unique}
            """
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "references").mkdir()
            (root / "references" / "methodology-core.md").write_text(canon, encoding="utf-8")
            for sat in mdl.SATELLITES:
                p = root / sat
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"prefix {unique} suffix\n", encoding="utf-8")
            result = mdl.check(root)
            # Clean ONLY if the temp canonical (not the real one) was read.
            self.assertIsNone(result["error"], result["error"])
            self.assertEqual(result["findings"], [], result["findings"])
            self.assertIn("INV-SENTINEL", result["invariants_checked"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
