# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/backlog/assess.build_item (F4 of the retro+backlog spec)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/ on path

from backlog.assess import build_item  # noqa: E402
from backlog.triage import classify    # noqa: E402


class BuildItemTests(unittest.TestCase):
    def _make_deferral(self, text: str, **overrides) -> dict:
        triage = classify(text)
        return {
            "title": text[:80],
            "text": text,
            "triage": triage,
            **overrides,
        }

    # ----- happy path -----

    def test_renders_required_frontmatter_fields(self) -> None:
        d = self._make_deferral("fix the broken Save button on the dashboard")
        body = build_item(d, repo="build-loop", branch="main", run_id="run-abc")
        # All mandatory frontmatter present
        for key in ("title:", "repo: build-loop", "branch: main", "created:",
                    "source: run/run-abc", "classify: SAFE", "effort: M",
                    "status: open", "product_impacting: true", "impact:"):
            self.assertIn(key, body, f"missing frontmatter: {key}")

    def test_renders_causal_tree_section(self) -> None:
        d = self._make_deferral("login form fails to render on mobile safari")
        body = build_item(d, repo="my-app", branch="feat/x", run_id="r1")
        self.assertIn("## Why it matters (causal tree)", body)
        self.assertIn("Surface signal:", body)
        self.assertIn("Triage rationale:", body)

    def test_impact_line_set_when_product_impacting(self) -> None:
        d = self._make_deferral("checkout shows wrong amount when coupon applied")
        body = build_item(d, repo="x", branch="main", run_id="r2")
        # impact field should not be empty after the colon
        impact_line = next(ln for ln in body.splitlines() if ln.startswith("impact:"))
        self.assertGreater(len(impact_line.strip()), len("impact: "))

    def test_passes_through_classify_and_effort_overrides(self) -> None:
        d = self._make_deferral("broken Save button — production hotfix needed",
                                classify="RISKY", effort="S")
        body = build_item(d, repo="x", branch="main", run_id="r3")
        self.assertIn("classify: RISKY", body)
        self.assertIn("effort: S", body)

    # ----- error path -----

    def test_raises_when_non_product_impacting(self) -> None:
        d = self._make_deferral("rename internal helper function")
        with self.assertRaises(ValueError) as ctx:
            build_item(d, repo="x", branch="main", run_id="r")
        self.assertIn("product_impacting=False", str(ctx.exception))

    # ----- defaults -----

    def test_defaults_classify_to_safe_and_effort_to_m(self) -> None:
        d = self._make_deferral("dashboard nav is broken on tablet")
        body = build_item(d, repo="x", branch="main", run_id="r")
        self.assertIn("classify: SAFE", body)
        self.assertIn("effort: M", body)

    def test_defaults_branch_to_main(self) -> None:
        d = self._make_deferral("user signin error is unclear")
        body = build_item(d, repo="x", run_id="r")  # branch omitted
        self.assertIn("branch: main", body)

    # ----- shape -----

    def test_body_ends_with_newline(self) -> None:
        d = self._make_deferral("user data integrity issue on import")
        body = build_item(d, repo="x", branch="main", run_id="r")
        self.assertTrue(body.endswith("\n") or body.endswith("\n\n"))

    def test_frontmatter_is_first_and_closed(self) -> None:
        d = self._make_deferral("user dashboard chart loads incorrectly")
        body = build_item(d, repo="x", branch="main", run_id="r")
        lines = body.splitlines()
        self.assertEqual(lines[0], "---", "frontmatter must start at line 1")
        # second '---' marks frontmatter close; must appear before any content.
        close_idx = next(i for i, ln in enumerate(lines[1:], start=1) if ln == "---")
        self.assertGreater(close_idx, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
