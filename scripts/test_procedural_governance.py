#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Tests for procedural_governance.py. Zero deps. Run: python3 test_procedural_governance.py

Covers:
- detect-patterns mode: state.json with 3 incidents same root_cause -> candidate written
- auto-draft mode gating: 4 hand-authored procedures -> no draft (gated)
- auto-draft mode: 5 hand-authored procedures + 1 candidate -> draft written (cheap_complete mocked)
- validate-symbols mode: present symbols -> no stale flag; absent -> stale: true
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "procedural_governance.py"


def _make_procedure(workdir: Path, name: str, depends_on: list[dict] | None = None) -> Path:
    proc_dir = workdir / ".procedural" / name
    proc_dir.mkdir(parents=True)
    fm_lines = [
        "---",
        f"name: {name}",
        "trigger: 'symptom'",
        "domains: [test]",
        "confidence: medium",
        "created: '2026-01-01'",
        "incident_count: 1",
    ]
    if depends_on:
        fm_lines.append("depends_on:")
        for d in depends_on:
            fm_lines.append(f"  - symbol: \"{d['symbol']}\"")
            fm_lines.append(f"    last_verified: \"{d.get('last_verified', '2026-01-01')}\"")
    else:
        fm_lines.append("depends_on: []")
    fm_lines.append("---")
    fm_lines.append(f"# {name}\nbody")
    proc_path = proc_dir / "procedure.md"
    proc_path.write_text("\n".join(fm_lines) + "\n")
    return proc_path


def _make_state_json(workdir: Path, runs: list[dict]) -> None:
    bd = workdir / ".build-loop"
    bd.mkdir(parents=True, exist_ok=True)
    (bd / "state.json").write_text(json.dumps({"runs": runs}))


def run_gov(workdir: Path, mode: str, *extra: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PROCEDURAL_GOVERNANCE_MOCK_DRAFT"] = "1"  # script-level test hook
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir), "--mode", mode] + list(extra),
        capture_output=True,
        text=True,
        env=env,
    )


class ProceduralGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_detect_patterns_writes_candidate_at_threshold(self) -> None:
        runs = [
            {"run_id": f"r-{i}", "outcome": "fail", "root_cause": "auth-token-mismatch"}
            for i in range(3)
        ]
        _make_state_json(self.workdir, runs)
        r = run_gov(self.workdir, "detect-patterns")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        cand_path = self.workdir / ".procedural" / "_candidates.jsonl"
        self.assertTrue(cand_path.exists())
        lines = [json.loads(l) for l in cand_path.read_text().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["incident_count"], 3)
        self.assertEqual(lines[0]["root_cause"], "auth-token-mismatch")

    def test_detect_patterns_below_threshold_writes_nothing(self) -> None:
        runs = [{"run_id": "r-1", "outcome": "fail", "root_cause": "rare"}]
        _make_state_json(self.workdir, runs)
        r = run_gov(self.workdir, "detect-patterns")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertFalse((self.workdir / ".procedural" / "_candidates.jsonl").exists())

    def test_auto_draft_gated_below_5_authored(self) -> None:
        for i in range(4):
            _make_procedure(self.workdir, f"hand-{i}")
        # And a pending candidate
        cand_dir = self.workdir / ".procedural"
        (cand_dir / "_candidates.jsonl").write_text(json.dumps({
            "name": "draft-me", "root_cause": "auth-token", "incident_count": 3, "run_ids": ["a", "b", "c"]
        }) + "\n")
        r = run_gov(self.workdir, "auto-draft")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertFalse((self.workdir / ".procedural" / "_drafts").exists())
        self.assertIn("gated", (r.stdout + r.stderr).lower())

    def test_auto_draft_fires_at_5_authored(self) -> None:
        for i in range(5):
            _make_procedure(self.workdir, f"hand-{i}")
        cand_dir = self.workdir / ".procedural"
        (cand_dir / "_candidates.jsonl").write_text(json.dumps({
            "name": "draft-me", "root_cause": "auth-token", "incident_count": 3, "run_ids": ["a", "b", "c"]
        }) + "\n")
        r = run_gov(self.workdir, "auto-draft")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        draft_path = self.workdir / ".procedural" / "_drafts" / "draft-me" / "procedure.md"
        self.assertTrue(draft_path.exists(), msg=f"stderr={r.stderr}")
        body = draft_path.read_text()
        self.assertIn("draft-me", body)
        self.assertIn("auth-token", body)

    def test_validate_symbols_present_means_not_stale(self) -> None:
        # Create a fake codebase file containing the symbol
        src_dir = self.workdir / "src"
        src_dir.mkdir()
        (src_dir / "foo.py").write_text("def MySymbol():\n    pass\n")
        _make_procedure(
            self.workdir,
            "present-symbol",
            depends_on=[{"symbol": "MySymbol", "last_verified": "2026-01-01"}],
        )
        r = run_gov(self.workdir, "validate-symbols", "--paths", "src")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        out = json.loads(r.stdout)
        # Find result for present-symbol
        target = next((row for row in out if row["name"] == "present-symbol"), None)
        self.assertIsNotNone(target)
        self.assertFalse(target["stale"])

    def test_validate_symbols_absent_means_stale(self) -> None:
        src_dir = self.workdir / "src"
        src_dir.mkdir()
        (src_dir / "bar.py").write_text("def OtherThing():\n    pass\n")
        _make_procedure(
            self.workdir,
            "missing-symbol",
            depends_on=[{"symbol": "GoneSymbol", "last_verified": "2026-01-01"}],
        )
        r = run_gov(self.workdir, "validate-symbols", "--paths", "src")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        out = json.loads(r.stdout)
        target = next((row for row in out if row["name"] == "missing-symbol"), None)
        self.assertIsNotNone(target)
        self.assertTrue(target["stale"])
        self.assertIn("GoneSymbol", target["missing_symbols"])


if __name__ == "__main__":
    unittest.main()
