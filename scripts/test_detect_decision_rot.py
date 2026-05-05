#!/usr/bin/env python3
"""Tests for detect_decision_rot.py. Zero deps. Run: python3 test_detect_decision_rot.py

Covers:
- 3 decisions dated 30/60/120 days ago; threshold 90 -> only the 120-day flagged
- threshold 0 -> all 3 flagged
- empty .episodic/decisions -> exit 0, [] output
- last_validated takes precedence over date when present
- output is valid JSON list
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "detect_decision_rot.py"


def _make_decision(workdir: Path, did: str, date_str: str, last_validated: str | None = None) -> Path:
    fm_lines = [
        "---",
        f"id: '{did}'",
        f"slug: 'decision-{did}'",
        f"title: Decision {did}",
        "type: decision",
        "status: accepted",
        "confidence: explicit",
        f"date: '{date_str}'",
        "tags: [testing]",
        "primary_tag: testing",
        f"entity: 'build-loop:{did}'",
        "source: manual",
    ]
    if last_validated is not None:
        fm_lines.append(f"last_validated: '{last_validated}'")
    fm_lines.append("---")
    body = "\n".join(fm_lines) + f"\n\n# Decision {did}\n\nbody.\n"
    decisions_dir = workdir / ".episodic" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    p = decisions_dir / f"{did}-{date_str}-decision.md"
    p.write_text(body)
    return p


def run_rot(workdir: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir)] + list(extra),
        capture_output=True,
        text=True,
    )


class DetectDecisionRotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _date_n_days_ago(self, n: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")

    def test_threshold_90_flags_only_120_day(self) -> None:
        _make_decision(self.workdir, "0001", self._date_n_days_ago(30))
        _make_decision(self.workdir, "0002", self._date_n_days_ago(60))
        _make_decision(self.workdir, "0003", self._date_n_days_ago(120))

        r = run_rot(self.workdir, "--threshold-days", "90")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "0003")
        self.assertGreaterEqual(out[0]["age_days"], 120)
        self.assertEqual(out[0]["primary_tag"], "testing")

    def test_threshold_zero_flags_all(self) -> None:
        _make_decision(self.workdir, "0001", self._date_n_days_ago(1))
        _make_decision(self.workdir, "0002", self._date_n_days_ago(5))
        r = run_rot(self.workdir, "--threshold-days", "0")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(len(out), 2)

    def test_empty_decisions_dir_returns_empty_list(self) -> None:
        # Create the dir so the script doesn't choke on missing path
        (self.workdir / ".episodic" / "decisions").mkdir(parents=True)
        r = run_rot(self.workdir, "--threshold-days", "90")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(json.loads(r.stdout), [])

    def test_last_validated_overrides_date(self) -> None:
        # Decision dated 200 days ago, last_validated 10 days ago — should NOT be flagged at threshold 90
        old_date = self._date_n_days_ago(200)
        recent_validation = self._date_n_days_ago(10)
        _make_decision(self.workdir, "0001", old_date, last_validated=recent_validation)
        r = run_rot(self.workdir, "--threshold-days", "90")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(json.loads(r.stdout), [])


if __name__ == "__main__":
    unittest.main()
