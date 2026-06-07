#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for distill — cluster building + heuristic distilled decision.

Runnable via ``python3 scripts/memory_consolidate/distill/test_distill.py``
(pytest is broken in this env). Uses ``unittest.TestCase`` so bare
``def test_*`` are NOT silently skipped.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # scripts/
sys.path.insert(0, str(HERE.parent))         # memory_consolidate/

from memory_consolidate.distill import distill as d  # noqa: E402


def _write_placed(workdir: Path, *, cid: str, name: str, project: str,
                  body: str, type_: str = "lesson",
                  abs_path: str = "", lane: str = "lessons",
                  distilled_into: str | None = None) -> None:
    placed_dir = workdir / ".build-loop" / "pending-lessons" / "placed"
    placed_dir.mkdir(parents=True, exist_ok=True)
    placement = {
        "lane": lane,
        "scope": "project",
        "project": project,
        "type": type_,
        "filename": f"{cid}.md",
        "absolute_path": abs_path or f"/tmp/{cid}.md",
        "backlinks": [],
    }
    if distilled_into:
        placement["distilled_into"] = distilled_into
    record = {
        "id": cid,
        "content": body,
        "name": name,
        "project": project,
        "type": type_,
        "placement": placement,
        "submitted_at": "2026-06-07T00:00:00Z",
        "source_run_id": "rx",
        "source_host": "claude_code",
        "source_workdir": str(workdir),
    }
    (placed_dir / f"{cid}.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8",
    )


class FindDistillCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_empty_queue_returns_empty(self):
        self.assertEqual(d.find_distill_candidates(self.tmp), [])

    def test_skips_already_distilled(self):
        _write_placed(self.tmp, cid="a", name="alpha", project="p",
                      body="x", distilled_into="some-distill-id")
        _write_placed(self.tmp, cid="b", name="beta", project="p", body="y")
        out = d.find_distill_candidates(self.tmp)
        self.assertEqual([r.candidate_id for r in out], ["b"])

    def test_loads_body_excerpt_capped(self):
        big = "A" * 5000
        _write_placed(self.tmp, cid="a", name="alpha", project="p", body=big)
        out = d.find_distill_candidates(self.tmp)
        self.assertEqual(len(out), 1)
        self.assertLessEqual(len(out[0].body_excerpt), 600)


class ClusterSimilarTests(unittest.TestCase):
    """Use the injected ``similarity_fn`` path — deterministic, no recall."""

    def _ref(self, cid: str, name: str, project: str) -> d.PlacedRef:
        return d.PlacedRef(
            candidate_id=cid, name=name, type_="lesson",
            project=project, scope="project", lane="lessons",
            absolute_path=f"/tmp/{cid}.md", body_excerpt=f"body {cid}",
        )

    def test_below_threshold_no_clusters(self):
        refs = [self._ref("a", "alpha", "p"), self._ref("b", "beta", "p")]
        out = d.cluster_similar(refs, threshold=0.9,
                                similarity_fn=lambda a, b: 0.1)
        self.assertEqual(out, [])

    def test_above_threshold_forms_cluster(self):
        refs = [self._ref("a", "alpha", "p"),
                self._ref("b", "beta", "p"),
                self._ref("c", "gamma", "p")]
        out = d.cluster_similar(refs, threshold=0.5,
                                similarity_fn=lambda a, b: 0.9)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0].members), 3)
        self.assertEqual(out[0].project, "p")
        # Similarity scores recorded (pairs).
        self.assertEqual(len(out[0].similarity_scores), 3)

    def test_partial_clustering(self):
        """a~b strong, c far. Cluster has {a,b}, c dropped."""
        refs = [self._ref("a", "alpha", "p"),
                self._ref("b", "beta", "p"),
                self._ref("c", "carol", "p")]
        def sim(x, y):
            pair = {x.candidate_id, y.candidate_id}
            return 0.9 if pair == {"a", "b"} else 0.1
        out = d.cluster_similar(refs, threshold=0.5, similarity_fn=sim)
        self.assertEqual(len(out), 1)
        self.assertEqual({m.candidate_id for m in out[0].members}, {"a", "b"})

    def test_mixed_project_cluster_has_no_project(self):
        refs = [self._ref("a", "alpha", "p1"),
                self._ref("b", "beta", "p2")]
        out = d.cluster_similar(refs, threshold=0.5,
                                similarity_fn=lambda a, b: 0.9)
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0].project)

    def test_singleton_input_yields_no_clusters(self):
        refs = [self._ref("a", "alpha", "p")]
        out = d.cluster_similar(refs, threshold=0.5,
                                similarity_fn=lambda a, b: 0.9)
        self.assertEqual(out, [])


class HeuristicDistillDecisionTests(unittest.TestCase):
    def test_project_scope_when_members_share_project(self):
        cluster = d.DistillCluster(
            cluster_id="abc",
            members=[
                d.PlacedRef("a", "always-use-paths", "lesson", "build-loop",
                            "project", "lessons", "/tmp/a.md", "x"),
                d.PlacedRef("b", "always-use-pathsep", "lesson", "build-loop",
                            "project", "lessons", "/tmp/b.md", "y"),
            ],
            similarity_scores=[0.9],
            project="build-loop",
        )
        dec = d.heuristic_distill_decision(cluster)
        self.assertEqual(dec["scope"], "project")
        self.assertEqual(dec["project"], "build-loop")
        self.assertEqual(dec["lane"], "lessons")
        self.assertEqual(dec["type"], "lesson")
        self.assertIn("a", dec["distilled_from"])
        self.assertIn("b", dec["distilled_from"])
        self.assertEqual(dec["backlinks"], ["/tmp/a.md", "/tmp/b.md"])
        # Name shares the slug prefix.
        self.assertTrue(dec["name"].startswith("always-use-paths"))
        self.assertTrue(dec["name"].endswith("-distilled"))

    def test_mixed_project_falls_back_to_top_level(self):
        cluster = d.DistillCluster(
            cluster_id="xy",
            members=[
                d.PlacedRef("a", "foo", "lesson", "p1", "project",
                            "lessons", "/tmp/a.md", "x"),
                d.PlacedRef("b", "bar", "lesson", "p2", "project",
                            "lessons", "/tmp/b.md", "y"),
            ],
            similarity_scores=[0.9],
            project=None,
        )
        dec = d.heuristic_distill_decision(cluster)
        self.assertEqual(dec["scope"], "top-level")
        self.assertIsNone(dec["project"])

    def test_packet_carries_instructions_and_decision(self):
        cluster = d.DistillCluster(
            cluster_id="z",
            members=[
                d.PlacedRef("a", "a", "lesson", "p", "project",
                            "lessons", "/tmp/a.md", "x"),
                d.PlacedRef("b", "b", "lesson", "p", "project",
                            "lessons", "/tmp/b.md", "y"),
            ],
            similarity_scores=[0.9],
            project="p",
        )
        packet = d.prepare_distill_packet(cluster)
        self.assertIn("distilled lesson", packet.instructions)
        self.assertIn("NEVER promote", packet.instructions)
        self.assertEqual(packet.suggested_decision, d.heuristic_distill(packet))


class OffHotPathContractTests(unittest.TestCase):
    """The distill module must NEVER be imported by intake or place.

    Distillation is async. Importing it from the submit/place hot path
    would defeat the contract.
    """

    def test_intake_does_not_import_distill(self):
        src = (HERE.parent / "intake.py").read_text(encoding="utf-8")
        self.assertNotIn("distill", src)

    def test_place_does_not_import_distill(self):
        src = (HERE.parent / "place.py").read_text(encoding="utf-8")
        self.assertNotIn("distill", src)


if __name__ == "__main__":
    unittest.main()
