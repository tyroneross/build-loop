#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""End-to-end: raw candidate → consolidate → guarded write → placement.

Proves the DoD: "raw candidate → correctly classified + placed + backlinked
on a real example." Also proves the writer guard never lets a misformed lane
prefix double-nest, even when the consolidator hands one to ``write()``.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "scripts"))
sys.path.insert(0, str(HERE.parent))


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.memroot = Path(tempfile.mkdtemp())
        self._prev_env = os.environ.get("BUILD_LOOP_MEMORY_STORE_ROOT")
        os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = str(self.memroot)
        import _paths
        importlib.reload(_paths)
        import memory_writer
        importlib.reload(memory_writer)
        from memory_consolidate import intake as intake_mod
        from memory_consolidate import classify as classify_mod
        from memory_consolidate import place as place_mod
        importlib.reload(intake_mod)
        importlib.reload(classify_mod)
        importlib.reload(place_mod)
        self.intake = intake_mod
        self.classify = classify_mod
        self.place = place_mod

    def tearDown(self):
        if self._prev_env is None:
            os.environ.pop("BUILD_LOOP_MEMORY_STORE_ROOT", None)
        else:
            os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = self._prev_env
        import _paths
        importlib.reload(_paths)

    def test_raw_to_placed_real_example(self):
        """A real-shaped candidate (the today's-bug postmortem) flows through
        submit → prepare → place and lands in the right lane with backlinks."""
        body = (
            "## Today's writer footgun\n\n"
            "Four agents independently passed `--file projects/<slug>/issues/x.md "
            "--scope project --project <slug>` and watched their memory file double-nest "
            "under `lessons/`. The writer guard now strips the redundant prefix.\n\n"
            "Pattern: any lane-prefix path under --scope project gets normalized."
        )
        c = self.intake.submit(
            body,
            workdir=self.tmp,
            run_id="run_2026_06_07",
            host="claude_code",
            hint="footgun gotcha — agents pick wrong path",
            project="build-loop",
            name="writer-path-footgun",
        )
        # 1. List shows it pending.
        pending = self.intake.list_pending(workdir=self.tmp)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, c.id)

        # 2. Prepare builds a packet — host LLM would read this.
        packet = self.classify.prepare(c.id, workdir=self.tmp)
        d = packet.suggested_decision
        # Heuristic routes "footgun gotcha" → lessons/gotcha.
        self.assertEqual(d["lane"], "lessons")
        self.assertEqual(d["type"], "gotcha")
        self.assertEqual(d["scope"], "project")
        self.assertEqual(d["project"], "build-loop")

        # 3. Simulate the host LLM amending the decision — adds explicit backlinks.
        host_decision = {
            **d,
            "backlinks": [
                "lessons/bl-memory-writer-path-normalization.md",
                "issues/bl-memory-writer-path-normalization.md",
            ],
        }
        fm = self.place.place(c.id, host_decision, workdir=self.tmp)

        # 4. The file lands at projects/build-loop/lessons/<canonical>.md
        landed = list(self.memroot.rglob("*.md"))
        self.assertEqual(len(landed), 1)
        rel = landed[0].relative_to(self.memroot)
        self.assertEqual(str(rel.parent), "projects/build-loop/lessons")
        # Canonical filename has YYYY-MM-DD-gotcha-... shape.
        self.assertRegex(rel.name, r"^\d{4}-\d{2}-\d{2}-gotcha-.*\.md$")

        # 5. The body carries the original content + backlinks footer.
        text = landed[0].read_text()
        self.assertIn("Today's writer footgun", text)
        self.assertIn("## Backlinks", text)
        self.assertIn("- lessons/bl-memory-writer-path-normalization.md", text)
        self.assertIn("- issues/bl-memory-writer-path-normalization.md", text)

        # 6. Frontmatter carries consolidation provenance.
        self.assertEqual(fm["name"], "writer-path-footgun")
        self.assertEqual(fm["type"], "gotcha")

        # 7. Candidate transitioned to placed/ with on-disk placement metadata.
        placed = self.intake.queue_dir(self.tmp, self.intake.PLACED_DIR)
        files = list(placed.glob("*.json"))
        self.assertEqual(len(files), 1)
        placement = json.loads(files[0].read_text())["placement"]
        self.assertEqual(placement["lane"], "lessons")
        self.assertEqual(placement["scope"], "project")
        self.assertEqual(placement["project"], "build-loop")

    def test_writer_guard_immune_when_decision_supplies_lane_prefixed_filename(self):
        """Even if the host LLM sets ``decision.filename = 'projects/<slug>/issues/x.md'``,
        the writer guard normalises it. DoD: the exact double-nest is impossible."""
        c = self.intake.submit(
            "another lesson body",
            workdir=self.tmp, run_id="run_x", host="claude_code",
            hint="issue", project="demoproj",
        )
        bad_decision = {
            "scope": "project", "project": "demoproj",
            "lane": "issues",
            "type": "gotcha",
            "name": "x",
            # Caller passes a fully-qualified path — guard MUST strip it.
            "filename": "projects/demoproj/issues/x.md",
            "backlinks": [],
        }
        self.place.place(c.id, bad_decision, workdir=self.tmp)
        landed = list(self.memroot.rglob("*.md"))
        rel = str(landed[0].relative_to(self.memroot))
        # Lands ONCE at projects/demoproj/issues/x.md — guard fixed it.
        self.assertEqual(rel, "projects/demoproj/issues/x.md")


class CLIEndToEndTests(unittest.TestCase):
    """Exercise `python3 -m memory_consolidate` end-to-end."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.memroot = Path(tempfile.mkdtemp())
        # Subprocess inherits this env.
        self.env = {
            **os.environ,
            "BUILD_LOOP_MEMORY_STORE_ROOT": str(self.memroot),
        }
        self.scripts = HERE.parent.parent / "scripts"

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "memory_consolidate", *args],
            cwd=str(self.scripts),
            env=self.env,
            capture_output=True, text=True,
        )

    def test_cli_submit_list_consolidate_roundtrip(self):
        # submit
        r = self._run(
            "--workdir", str(self.tmp),
            "submit", "--run-id", "rx", "--host", "claude_code",
            "--content", "footgun in path normalization",
            "--hint", "gotcha", "--project", "demoproj",
            "--json",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        cid = json.loads(r.stdout)["id"]
        # list
        r = self._run("--workdir", str(self.tmp), "list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        items = json.loads(r.stdout)
        self.assertEqual(len(items), 1)
        # consolidate (deterministic)
        r = self._run("--workdir", str(self.tmp), "consolidate", cid)
        self.assertEqual(r.returncode, 0, r.stderr)
        landed = list(self.memroot.rglob("*.md"))
        self.assertEqual(len(landed), 1)
        rel = str(landed[0].relative_to(self.memroot))
        # Heuristic routes gotcha → projects/<p>/lessons/
        self.assertTrue(rel.startswith("projects/demoproj/lessons/"), rel)


if __name__ == "__main__":
    unittest.main()
