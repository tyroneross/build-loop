#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for promote — recurrence-gated promotion + lane mapping.

KEY DoD test: a lesson recurring across ≥N projects is promoted (gate
accepted, correct global lane); a one-off lesson is REJECTED.

Runnable via ``python3 scripts/memory_consolidate/promote/test_promote.py``.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE.parent))

from memory_consolidate.promote import promote as pr  # noqa: E402


def _make_memroot(projects: dict[str, dict[str, str]]) -> Path:
    """Build a memroot with the given project→{filename: body} layout.

    Each project's files land at ``projects/<p>/lessons/<filename>.md``
    (override lane via filename like ``architecture/x.md``).
    """
    root = Path(tempfile.mkdtemp())
    for project, files in projects.items():
        for filename, body in files.items():
            if "/" in filename:
                lane, fname = filename.split("/", 1)
            else:
                lane, fname = "lessons", filename
            d = root / "projects" / project / lane
            d.mkdir(parents=True, exist_ok=True)
            (d / fname).write_text(body, encoding="utf-8")
    return root


def _fm(name: str, type_: str = "lesson") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"type: {type_}\n"
        "---\n"
    )


class FindPromotionCandidatesTests(unittest.TestCase):
    def test_walks_lessons_lane_per_project(self):
        root = _make_memroot({
            "p1": {"alpha.md": _fm("always-quote-paths") + "always quote paths"},
            "p2": {"beta.md": _fm("always-quote-paths") + "always quote paths"},
        })
        candidates = pr.find_promotion_candidates(
            workdir=".", memory_root=root,
            siblings_fn=lambda body, own: (
                [{"project": "p2", "file_hint": "projects/p2/lessons/beta.md"}]
                if own == "p1" else
                [{"project": "p1", "file_hint": "projects/p1/lessons/alpha.md"}]
            ),
        )
        # One candidate per file walked.
        self.assertEqual(len(candidates), 2)
        # Each carries the cross-project sibling.
        for c in candidates:
            self.assertEqual(len(c.distinct_projects), 1)

    def test_skips_index_and_telemetry(self):
        root = _make_memroot({"p1": {"INDEX.md": "x", "TELEMETRY.md": "x", "real.md": _fm("real") + "real"}})
        cs = pr.find_promotion_candidates(
            workdir=".", memory_root=root, siblings_fn=lambda b, o: [],
        )
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs[0].name, "real")

    def test_walks_multiple_lanes(self):
        root = _make_memroot({
            "p1": {
                "lessons/a.md": _fm("a"),
                "architecture/b.md": _fm("b", "architecture"),
                "debugging/c.md": _fm("c", "debug-incident"),
            },
        })
        cs = pr.find_promotion_candidates(
            workdir=".", memory_root=root, siblings_fn=lambda b, o: [],
        )
        lanes = {c.lane for c in cs}
        self.assertEqual(lanes, {"lessons", "architecture", "debugging"})


class PromotionGateTests(unittest.TestCase):
    """**KEY DoD**: gate rejects one-off, accepts ≥N projects."""

    def _candidate(self, project: str, distinct: set[str]) -> pr.PromotionCandidate:
        return pr.PromotionCandidate(
            source_path=f"/tmp/{project}.md",
            name="x", type_="lesson", project=project, lane="lessons",
            body_excerpt="body", siblings=[],
            distinct_projects=distinct,
        )

    def test_one_off_lesson_rejected(self):
        c = self._candidate("p1", distinct=set())
        d = pr.promotion_gate(c, min_projects=2)
        self.assertFalse(d.accepted)
        self.assertEqual(d.reason, "single-project")
        self.assertEqual(d.distinct_project_count, 1)

    def test_two_project_recurrence_accepted_at_default(self):
        c = self._candidate("p1", distinct={"p2"})
        d = pr.promotion_gate(c, min_projects=2)
        self.assertTrue(d.accepted)
        self.assertEqual(d.reason, "recurrence-earned")
        self.assertEqual(d.distinct_project_count, 2)

    def test_higher_threshold_blocks_two_project(self):
        c = self._candidate("p1", distinct={"p2"})
        d = pr.promotion_gate(c, min_projects=3)
        self.assertFalse(d.accepted)
        self.assertEqual(d.reason, "not-enough-projects")

    def test_three_project_recurrence_accepted(self):
        c = self._candidate("p1", distinct={"p2", "p3"})
        d = pr.promotion_gate(c, min_projects=3)
        self.assertTrue(d.accepted)
        self.assertEqual(d.distinct_project_count, 3)


class HeuristicPromotionDecisionTests(unittest.TestCase):
    def test_lane_maps_project_arch_to_global_arch(self):
        c = pr.PromotionCandidate(
            source_path="/tmp/p1.md", name="x", type_="architecture",
            project="p1", lane="architecture",
            body_excerpt="b", siblings=[], distinct_projects={"p2"},
        )
        d = pr.heuristic_promotion_decision(c)
        self.assertEqual(d["lane"], "architecture")
        self.assertEqual(d["scope"], "top-level")
        self.assertIsNone(d["project"])
        self.assertEqual(d["promoted_from_project"], "p1")

    def test_unknown_lane_falls_back_to_lessons(self):
        c = pr.PromotionCandidate(
            source_path="/tmp/p1.md", name="x", type_="lesson",
            project="p1", lane="raw",  # not in PROJECT_TO_GLOBAL_LANE
            body_excerpt="b", siblings=[], distinct_projects={"p2"},
        )
        d = pr.heuristic_promotion_decision(c)
        self.assertEqual(d["lane"], "lessons")

    def test_backlinks_include_source_plus_siblings(self):
        c = pr.PromotionCandidate(
            source_path="/tmp/p1.md", name="x", type_="lesson",
            project="p1", lane="lessons", body_excerpt="b",
            siblings=[
                {"project": "p2", "file_hint": "projects/p2/lessons/a.md"},
                {"project": "p3", "file_hint": "projects/p3/lessons/b.md"},
            ],
            distinct_projects={"p2", "p3"},
        )
        d = pr.heuristic_promotion_decision(c)
        self.assertIn("/tmp/p1.md", d["backlinks"])
        self.assertIn("projects/p2/lessons/a.md", d["backlinks"])
        self.assertIn("projects/p3/lessons/b.md", d["backlinks"])
        # Recurrence projects exposed for the writer-frontmatter.
        self.assertEqual(sorted(d["recurrence_projects"]), ["p1", "p2", "p3"])


class PromotionPacketTests(unittest.TestCase):
    def test_packet_gate_matches_standalone_call(self):
        c = pr.PromotionCandidate(
            source_path="/tmp/p1.md", name="x", type_="lesson",
            project="p1", lane="lessons", body_excerpt="b",
            siblings=[], distinct_projects=set(),
        )
        packet = pr.prepare_promotion_packet(c, min_projects=2)
        standalone = pr.promotion_gate(c, min_projects=2)
        self.assertEqual(packet.gate.accepted, standalone.accepted)
        self.assertEqual(packet.gate.reason, standalone.reason)
        self.assertIn("recurrence-earned", packet.instructions)


class EndToEndOneOffRejectVsMultiProjectAcceptTests(unittest.TestCase):
    """**DoD evidence test**: same memroot proves the gate rejects a
    one-off and accepts a cross-project recurrence."""

    def test_one_off_rejected_multi_project_accepted_in_same_run(self):
        root = _make_memroot({
            "p1": {
                # Recurring across p1 + p2 + p3
                "always-quote-paths.md": _fm("always-quote-paths") + "always quote paths in shell",
                # One-off — only p1
                "p1-private.md": _fm("p1-private-only") + "only matters for p1",
            },
            "p2": {"quote-paths.md": _fm("quote-paths") + "quote paths in shell scripts"},
            "p3": {"path-quoting.md": _fm("path-quoting") + "shell path quoting"},
        })

        # Sibling lookup: 'always-quote-paths' surfaces in p2+p3; 'p1-private-only' nowhere.
        def siblings_fn(body, own_project):
            if "always quote" in body or "quote paths" in body:
                out = []
                for p, hint in [("p1", "projects/p1/lessons/always-quote-paths.md"),
                                ("p2", "projects/p2/lessons/quote-paths.md"),
                                ("p3", "projects/p3/lessons/path-quoting.md")]:
                    if p != own_project:
                        out.append({"project": p, "file_hint": hint})
                return out
            return []

        candidates = pr.find_promotion_candidates(
            workdir=".", memory_root=root, siblings_fn=siblings_fn,
            min_projects=2,
        )
        # Group by source name for assertions.
        by_name = {c.name: c for c in candidates}

        self.assertIn("always-quote-paths", by_name)
        self.assertIn("p1-private-only", by_name)

        # Cross-project one promotes.
        accept = pr.promotion_gate(by_name["always-quote-paths"], min_projects=2)
        self.assertTrue(accept.accepted, accept)
        self.assertEqual(accept.reason, "recurrence-earned")
        self.assertGreaterEqual(accept.distinct_project_count, 2)

        # One-off is rejected.
        reject = pr.promotion_gate(by_name["p1-private-only"], min_projects=2)
        self.assertFalse(reject.accepted, reject)
        self.assertEqual(reject.reason, "single-project")

        # And the heuristic decision puts the accepted one in the right lane.
        dec = pr.heuristic_promotion_decision(by_name["always-quote-paths"])
        self.assertEqual(dec["scope"], "top-level")
        self.assertEqual(dec["lane"], "lessons")


if __name__ == "__main__":
    unittest.main()
