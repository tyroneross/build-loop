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
        # Canonical schema only (scripts/backlog.py FIELD_ORDER).
        for key in ("title:", "status: open", "priority:", "type:",
                    "area: product", "bucket: planned", "workstream: main",
                    "gated: none", "provenance:", "source: run/run-abc",
                    "ref: build-loop", "created:", "validated:"):
            self.assertIn(key, body, f"missing frontmatter: {key}")

    def test_emits_no_key_outside_the_canonical_schema(self) -> None:
        """The defect this collapse fixes: a second writer with its own shape.

        Two writers producing two frontmatter shapes into one directory is how
        49 items ended up on one schema and 17 on another. Pin the boundary
        rather than trusting the next editor to remember it.
        """
        d = self._make_deferral("checkout shows wrong amount when coupon applied")
        body = build_item(d, repo="x", branch="main", run_id="r9")
        head = body.split("---")[1]
        for retired in ("repo:", "branch:", "classify:", "effort:",
                        "product_impacting:", "impact:"):
            self.assertNotIn(retired, head, f"retired key still emitted: {retired}")

    def test_renders_causal_tree_section(self) -> None:
        d = self._make_deferral("login form fails to render on mobile safari")
        body = build_item(d, repo="my-app", branch="feat/x", run_id="r1")
        self.assertIn("## Why it matters (causal tree)", body)
        self.assertIn("Surface signal:", body)
        self.assertIn("Triage rationale:", body)

    def test_impact_survives_as_body_content_not_frontmatter(self) -> None:
        """impact moved from a key nothing read to content a human reads."""
        d = self._make_deferral("checkout shows wrong amount when coupon applied")
        body = build_item(d, repo="x", branch="main", run_id="r2")
        head, tail = body.split("---")[1], body.split("---", 2)[2]
        self.assertNotIn("impact:", head)
        self.assertIn("Surface signal:", tail)

    def test_classify_maps_to_gated_and_bucket(self) -> None:
        """The approved mapping. RISKY stays ungated on purpose — risk means
        'isolate to a worktree and continue', not 'stop and ask'."""
        cases = {
            "SAFE":       ("gated: none", "bucket: planned"),
            "RISKY":      ("gated: none", "bucket: planned"),
            "DECISION":   ("gated: product-decision", "bucket: decision"),
            "PRODUCTION": ("gated: prod-deploy", "bucket: planned"),
        }
        for classify, (gated, bucket) in cases.items():
            d = self._make_deferral("broken Save button", classify=classify)
            body = build_item(d, repo="x", branch="main", run_id="r3")
            self.assertIn(gated, body, f"{classify} -> {gated}")
            self.assertIn(bucket, body, f"{classify} -> {bucket}")

    def test_unknown_classify_falls_back_to_safe(self) -> None:
        d = self._make_deferral("broken Save button", classify="NONSENSE")
        body = build_item(d, repo="x", branch="main", run_id="r4")
        self.assertIn("gated: none", body)
        self.assertIn("bucket: planned", body)

    # ----- error path -----

    def test_raises_when_non_product_impacting(self) -> None:
        d = self._make_deferral("rename internal helper function")
        with self.assertRaises(ValueError) as ctx:
            build_item(d, repo="x", branch="main", run_id="r")
        self.assertIn("product_impacting=False", str(ctx.exception))

    # ----- defaults -----

    def test_defaults_classify_to_safe(self) -> None:
        d = self._make_deferral("dashboard nav is broken on tablet")
        body = build_item(d, repo="x", branch="main", run_id="r")
        self.assertIn("gated: none", body)
        self.assertIn("bucket: planned", body)

    def test_defaults_workstream_to_main(self) -> None:
        """branch became workstream — same concept, canonical name."""
        d = self._make_deferral("user signin error is unclear")
        body = build_item(d, repo="x", run_id="r")  # branch omitted
        self.assertIn("workstream: main", body)

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
