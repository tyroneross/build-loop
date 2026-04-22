#!/usr/bin/env python3
"""Tests for write_run_entry.py. Zero deps. Run: python3 test_write_run_entry.py"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "write_run_entry.py"
RUN_ID_RE = re.compile(r"^run_\d{8}T\d{6}Z_[0-9a-f]{8}$")


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
    )


class WriteRunEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.state = self.workdir / ".build-loop" / "state.json"
        self.experiments = self.workdir / ".build-loop" / "experiments"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _base_args(self, **overrides: str) -> list[str]:
        args = {
            "--workdir": str(self.workdir),
            "--goal": "ship write_run_entry",
            "--outcome": "pass",
            "--phases-json": '{"assess":{"status":"pass","duration_s":2}}',
        }
        args.update(overrides)
        flat: list[str] = []
        for k, v in args.items():
            flat.extend([k, v])
        return flat

    def test_first_run_creates_runs_array(self) -> None:
        result = run(self._base_args())
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        run_id = result.stdout.strip()
        self.assertRegex(run_id, RUN_ID_RE)
        state = json.loads(self.state.read_text())
        self.assertIn("runs", state)
        self.assertEqual(len(state["runs"]), 1)
        self.assertEqual(state["runs"][0]["run_id"], run_id)
        self.assertEqual(state["runs"][0]["outcome"], "pass")

    def test_second_run_appends(self) -> None:
        self.assertEqual(run(self._base_args()).returncode, 0)
        r2 = run(self._base_args(**{"--goal": "second build"}))
        self.assertEqual(r2.returncode, 0, msg=r2.stderr)
        state = json.loads(self.state.read_text())
        self.assertEqual(len(state["runs"]), 2)
        self.assertNotEqual(state["runs"][0]["run_id"], state["runs"][1]["run_id"])

    def test_legacy_state_additive_migration(self) -> None:
        # Simulate SpeakSavvy-iOS v0.2.0 shape: no runs[], rich existing data
        self.state.parent.mkdir(parents=True)
        legacy = {
            "goal": "old goal",
            "active": False,
            "currentPhase": "report",
            "phases": {"assess": {"status": "pass"}, "execute": {"status": "pass"}},
            "iterations": 2,
        }
        self.state.write_text(json.dumps(legacy, indent=2))
        result = run(self._base_args())
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        state = json.loads(self.state.read_text())
        # All legacy keys preserved
        for k, v in legacy.items():
            self.assertEqual(state[k], v, f"legacy key {k!r} mutated")
        self.assertEqual(len(state["runs"]), 1)

    def test_confound_across_two_artifacts(self) -> None:
        # Create baseline jsonl files for two experimental artifacts
        self.experiments.mkdir(parents=True)
        for name in ("skill-a", "skill-b"):
            (self.experiments / f"{name}.jsonl").write_text(
                json.dumps({"event": "created", "artifact": name, "baseline_metric": "x", "baseline_value": 1, "target_value": 2, "sample_size_target": 8}) + "\n"
            )
        result = run(self._base_args(**{"--active-experimental-artifacts": "skill-a,skill-b"}))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        for name, other in (("skill-a", "skill-b"), ("skill-b", "skill-a")):
            lines = (self.experiments / f"{name}.jsonl").read_text().strip().splitlines()
            self.assertEqual(len(lines), 2)  # created + applied
            applied = json.loads(lines[-1])
            self.assertEqual(applied["event"], "applied")
            self.assertEqual(applied["co_applied_experimental_artifacts"], [other])
            self.assertTrue(applied["confounded"])

    def test_malformed_phases_exits_1(self) -> None:
        result = run(self._base_args(**{"--phases-json": "not-json"}))
        self.assertEqual(result.returncode, 1)
        self.assertIn("validation error", result.stderr)

    def test_invalid_outcome_exits_1(self) -> None:
        result = run(self._base_args(**{"--outcome": "bogus"}))
        self.assertEqual(result.returncode, 1)

    def test_single_artifact_not_confounded(self) -> None:
        self.experiments.mkdir(parents=True)
        (self.experiments / "solo.jsonl").write_text(
            json.dumps({"event": "created", "artifact": "solo", "baseline_metric": "x", "baseline_value": 1, "target_value": 2, "sample_size_target": 8}) + "\n"
        )
        result = run(self._base_args(**{"--active-experimental-artifacts": "solo"}))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = (self.experiments / "solo.jsonl").read_text().strip().splitlines()
        applied = json.loads(lines[-1])
        self.assertEqual(applied["co_applied_experimental_artifacts"], [])
        self.assertFalse(applied["confounded"])

    def test_corrupt_state_json_exits_1(self) -> None:
        self.state.parent.mkdir(parents=True)
        self.state.write_text("{corrupted-not-json")
        result = run(self._base_args())
        self.assertEqual(result.returncode, 1)
        self.assertIn("validation error", result.stderr)

    def test_missing_baseline_skips_applied(self) -> None:
        # No baseline file for 'ghost' — script should warn and not create one
        result = run(self._base_args(**{"--active-experimental-artifacts": "ghost"}))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("no baseline for experiment 'ghost'", result.stderr)
        self.assertFalse((self.experiments / "ghost.jsonl").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
