#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Guards the dispatch docs that route subagent evidence through the citation
check and the shared facts file (standing rules, 2026-09-14).

A doc that names a command the script no longer accepts is a dormant gate, so
the test also runs the documented subcommand against a real citation.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFS = ROOT / "skills" / "build-loop" / "references"


class CitationGateDocs(unittest.TestCase):
    def test_verify_dispatch_names_the_citation_command(self) -> None:
        text = (REFS / "verify-dispatch.md").read_text(encoding="utf-8")
        self.assertIn("premise_revalidation.py citations --repo", text)

    def test_phase3_briefs_name_the_facts_file(self) -> None:
        text = (REFS / "phase-3-execute.md").read_text(encoding="utf-8")
        self.assertIn(".build-loop/facts/<run-id>.jsonl", text)
        self.assertIn("take the live measurement", text)

    def test_documented_command_rejects_a_drifted_citation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "a.rs").write_text("\n".join(["x"] * 49 + ["let s = validate_submit(submit)?;"]) + "\n", encoding="utf-8")
            lanes = repo / "lanes.json"
            lanes.write_text(json.dumps([{"evidence": [{"kind": "cited", "ref": "a.rs:7", "expect": "validate_submit(submit)"}]}]), encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "premise_revalidation.py"), "citations", "--repo", str(repo), "--input", str(lanes), "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 1, r.stderr)
            item = json.loads(r.stdout)["items"][0]
            self.assertEqual((item["status"], item["nearest_line"]), ("expect_not_found", 50))


if __name__ == "__main__":
    unittest.main()
