#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for async_runner — the four-arm orchestrator + hot-path guard.

**KEY DoD test**: ``intake.py`` and ``place.py`` MUST NOT import the
async_runner (or any of the four arms). Distillation/promotion/lifecycle/
backlinks live OFF the Stop / Phase 6 hot path.

Runnable via ``python3 scripts/memory_consolidate/test_async_runner.py``.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))         # scripts/

from memory_consolidate import async_runner as ar  # noqa: E402


def _make_memroot_with_recurring_lesson() -> Path:
    """Two projects share a near-identical lesson + a third one-off."""
    root = Path(tempfile.mkdtemp())
    def write(rel: str, fm: dict, body: str) -> Path:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = ["---"] + [f"{k}: {v}" for k, v in fm.items()] + ["---"]
        p.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")
        return p

    write("projects/p1/lessons/quote-paths.md",
          {"name": "quote-paths", "type": "lesson"},
          "always quote paths in shell scripts to avoid word splitting bugs")
    write("projects/p2/lessons/quote-paths.md",
          {"name": "quote-paths", "type": "lesson"},
          "always quote paths in shell scripts to avoid word splitting bugs")
    # One-off in p3:
    write("projects/p3/lessons/p3-only.md",
          {"name": "p3-only", "type": "lesson"},
          "this lesson is specific to p3 and only matters there")
    return root


class HotPathContractTests(unittest.TestCase):
    """**HEADLINE DoD**: the async path is not on the hot path."""

    SCRIPTS_DIR = HERE.parent

    def test_intake_does_not_import_async_runner(self):
        src = (self.SCRIPTS_DIR / "memory_consolidate" / "intake.py").read_text()
        self.assertNotIn("async_runner", src)
        self.assertNotIn("from .distill", src)
        self.assertNotIn("from .promote", src)
        self.assertNotIn("from .lifecycle", src)
        self.assertNotIn("from .backlinks", src)
        self.assertNotIn("distill", src)
        self.assertNotIn("promote", src)
        self.assertNotIn("lifecycle", src)
        self.assertNotIn("backlinks", src)

    def test_place_does_not_import_async_runner(self):
        src = (self.SCRIPTS_DIR / "memory_consolidate" / "place.py").read_text()
        self.assertNotIn("async_runner", src)
        self.assertNotIn("from .distill", src)
        self.assertNotIn("from .promote", src)
        self.assertNotIn("from .lifecycle", src)
        self.assertNotIn("from .backlinks", src)

    def test_classify_does_not_import_async_runner(self):
        src = (self.SCRIPTS_DIR / "memory_consolidate" / "classify.py").read_text()
        self.assertNotIn("async_runner", src)
        self.assertNotIn("from .distill", src)
        self.assertNotIn("from .promote", src)
        self.assertNotIn("from .lifecycle", src)
        self.assertNotIn("from .backlinks", src)

    def test_intake_module_imports_without_loading_arms(self):
        # The proof: even after intake/place are imported, the arm modules
        # may NOT appear in sys.modules unless explicitly imported.
        # We sandbox via a subprocess so import cache from this test session
        # (which loads async_runner above) doesn't pollute the check.
        import subprocess
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(self.SCRIPTS_DIR)!r})
            from memory_consolidate import intake, place, classify  # noqa: F401
            assert "memory_consolidate.distill" not in sys.modules, "distill arm loaded on hot path"
            assert "memory_consolidate.promote" not in sys.modules, "promote arm loaded on hot path"
            assert "memory_consolidate.lifecycle" not in sys.modules, "lifecycle arm loaded on hot path"
            assert "memory_consolidate.backlinks" not in sys.modules, "backlinks arm loaded on hot path"
            assert "memory_consolidate.async_runner" not in sys.modules, "async_runner loaded on hot path"
            print("OK")
        """)
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)


class AsyncRunnerOrchestrationTests(unittest.TestCase):
    """The orchestrator chains the four arms; each arm is tested in detail
    by its own colocated test_*.py — here we cover wiring + error
    isolation."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.memroot = _make_memroot_with_recurring_lesson()

    def test_run_async_emits_structured_report(self):
        # Promote arm: simulate cross-project siblings for the recurring lesson.
        def siblings_fn(body, own_project):
            if "quote paths" in body:
                return [{"project": "p1" if own_project != "p1" else "p2",
                         "file_hint": f"projects/p1/lessons/quote-paths.md"}]
            return []
        report = ar.run_async(
            workdir=str(self.tmp), memory_root=self.memroot,
            siblings_fn=siblings_fn,
        )
        self.assertIsInstance(report, ar.AsyncReport)
        d = report.to_dict()
        # Basic shape.
        self.assertIn("promotion_candidates", d)
        self.assertIn("lifecycle_transitions", d)
        self.assertIn("backlink_entries_touched", d)
        # Promotion arm fired: 3 lessons walked → 3 candidates.
        self.assertEqual(report.promotion_candidates, 3)
        # 2 of them recur across ≥2 projects → 2 accepted.
        self.assertEqual(report.promotion_accepted, 2)
        # 1 one-off → rejected.
        self.assertEqual(report.promotion_rejected, 1)
        # Lifecycle arm classified the 3 lessons.
        self.assertGreaterEqual(report.lifecycle_transitions, 1)
        # No errors.
        self.assertEqual(report.errors, [], report.errors)

    def test_one_arm_failure_does_not_stop_others(self):
        # similarity_fn raises → distill arm errors but rest run.
        def bad_sim(a, b):
            raise RuntimeError("boom")
        # Seed a placed candidate so distill has something to chew on.
        placed_dir = self.tmp / ".build-loop" / "pending-lessons" / "placed"
        placed_dir.mkdir(parents=True)
        for cid in ("a", "b"):
            (placed_dir / f"{cid}.json").write_text(json.dumps({
                "id": cid, "content": "x", "name": cid, "project": "p",
                "placement": {"absolute_path": f"/tmp/{cid}.md",
                              "scope": "project", "project": "p",
                              "lane": "lessons", "type": "lesson"},
            }), encoding="utf-8")
        report = ar.run_async(
            workdir=str(self.tmp), memory_root=self.memroot,
            similarity_fn=bad_sim, siblings_fn=lambda b, o: [],
        )
        # Distill errored.
        self.assertTrue(any("distill" in e for e in report.errors), report.errors)
        # But the other arms still ran.
        self.assertGreaterEqual(report.promotion_candidates, 1)
        self.assertGreaterEqual(report.lifecycle_transitions, 0)

    def test_write_false_skips_disk_writes(self):
        before = {}
        for p in self.memroot.rglob("*.md"):
            before[str(p)] = p.read_text(encoding="utf-8")
        ar.run_async(
            workdir=str(self.tmp), memory_root=self.memroot,
            write=False,
            siblings_fn=lambda b, o: [],
            related_fn=lambda b, o, p: [{"file_hint": "other.md",
                                          "subject": "other"}],
        )
        after = {str(p): p.read_text(encoding="utf-8")
                 for p in self.memroot.rglob("*.md")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
