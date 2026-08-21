#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
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
sys.path.insert(0, str(HERE))

import procedural_governance as pg  # noqa: E402


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

    def test_detect_patterns_clusters_nested_phase_root_cause(self) -> None:
        # f1: no production writer emits a TOP-LEVEL root_cause; the failing
        # phase records it under phases[N].root_cause (canonical Review-G schema).
        # The clusterer must harvest that nested field.
        runs = [
            {
                "run_id": f"r-{i}",
                "outcome": "fail",
                "phases": {
                    "3": {"status": "pass"},
                    "4": {"status": "fail", "root_cause": "db-connection-timeout"},
                },
            }
            for i in range(3)
        ]
        _make_state_json(self.workdir, runs)
        r = run_gov(self.workdir, "detect-patterns")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        cand_path = self.workdir / ".procedural" / "_candidates.jsonl"
        self.assertTrue(cand_path.exists())
        lines = [json.loads(l) for l in cand_path.read_text().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["root_cause"], "db-connection-timeout")
        self.assertEqual(lines[0]["incident_count"], 3)

    def test_cluster_dedupes_top_and_phase_same_cause_per_run(self) -> None:
        # A cause present at BOTH levels of one run is one incident, not two.
        import procedural_governance as pg
        runs = [
            {"run_id": "r-1", "root_cause": "flaky-oracle",
             "phases": {"4": {"status": "fail", "root_cause": "flaky-oracle"}}}
        ]
        clusters = pg.cluster_root_causes(runs)
        self.assertEqual(clusters["flaky-oracle"], ["r-1"])

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


# --- detect_patterns: locked, atomic candidate append -------------------------
# Filed as a phase-6-learn race. It is worse than "interleaved rows": the dedup read
# and the read-modify-write were both unlocked, so two concurrent Stop hooks each
# read the old file and the SECOND write replaced the first — lost rows.

class DetectPatternsAppendTests(unittest.TestCase):
    def setUp(self) -> None:
        import shutil, tempfile
        self.wd = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.wd, ignore_errors=True)
        self.cand = self.wd / ".procedural" / "_candidates.jsonl"

    def _seed_runs(self, root_cause: str, n: int) -> None:
        """Enough incidents on one root cause to clear PATTERN_THRESHOLD."""
        runs = [{"run_id": f"r{i}", "root_cause": root_cause}
                for i in range(max(n, pg.PATTERN_THRESHOLD))]
        state = self.wd / ".build-loop" / "state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"runs": runs}))

    def _rows(self) -> list[dict]:
        if not self.cand.exists():
            return []
        return [json.loads(l) for l in self.cand.read_text().splitlines() if l.strip()]

    def test_every_written_line_is_valid_json(self):
        self._seed_runs("db timeout", pg.PATTERN_THRESHOLD)
        pg.detect_patterns(self.wd)
        for line in self.cand.read_text().splitlines():
            if line.strip():
                json.loads(line)  # raises on a spliced row

    def test_existing_file_without_trailing_newline_is_not_spliced(self):
        """A prior row lacking its newline would otherwise concatenate onto the first
        new row, producing a line that parses as neither."""
        self.cand.parent.mkdir(parents=True, exist_ok=True)
        self.cand.write_text('{"name":"prior","root_cause":"old"}')  # no newline
        self._seed_runs("db timeout", pg.PATTERN_THRESHOLD)
        pg.detect_patterns(self.wd)
        rows = self._rows()
        self.assertGreaterEqual(len(rows), 2, self.cand.read_text())
        self.assertEqual(rows[0]["name"], "prior")

    def test_second_run_does_not_drop_the_first_runs_rows(self):
        self._seed_runs("db timeout", pg.PATTERN_THRESHOLD)
        pg.detect_patterns(self.wd)
        first = self._rows()
        self._seed_runs("disk full", pg.PATTERN_THRESHOLD)
        pg.detect_patterns(self.wd)
        names = [r["root_cause"] for r in self._rows()]
        self.assertIn("db timeout", names, "the earlier run's rows were replaced")
        self.assertIn("disk full", names)
        self.assertGreaterEqual(len(self._rows()), len(first) + 1)

    def test_rerun_is_idempotent(self):
        self._seed_runs("db timeout", pg.PATTERN_THRESHOLD)
        pg.detect_patterns(self.wd)
        once = self._rows()
        pg.detect_patterns(self.wd)
        self.assertEqual(self._rows(), once, "a re-run duplicated candidates")

    def test_the_write_is_serialised_by_the_shared_lock(self):
        """Holding the sidecar lock must block the append rather than let it race.
        This is the property the fix exists for; without the lock the call proceeds."""
        import atomic_io
        self._seed_runs("db timeout", pg.PATTERN_THRESHOLD)
        self.cand.parent.mkdir(parents=True, exist_ok=True)
        with atomic_io.LockedFile(self.cand):
            with self.assertRaises(TimeoutError):
                with atomic_io.LockedFile(self.cand, timeout_s=0.1):
                    pass

    def test_no_temp_files_are_left_behind(self):
        self._seed_runs("db timeout", pg.PATTERN_THRESHOLD)
        pg.detect_patterns(self.wd)
        litter = [p.name for p in self.cand.parent.iterdir() if ".tmp" in p.name]
        self.assertEqual(litter, [])
