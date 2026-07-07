#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for audit_record_verdict.py oracle_completeness (B1). Zero deps."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import audit_record_verdict as arv  # noqa: E402

SCRIPT = HERE / "audit_record_verdict.py"


class ParseOracleCompletenessTests(unittest.TestCase):
    def test_none_when_absent(self) -> None:
        self.assertIsNone(arv._parse_oracle_completeness(None))
        self.assertIsNone(arv._parse_oracle_completeness(""))

    def test_valid_note_parsed(self) -> None:
        note = arv._parse_oracle_completeness(
            '{"covered": "auth", "uncovered": "webhooks", "coverage": "partial"}'
        )
        self.assertEqual(note, {"covered": "auth", "uncovered": "webhooks", "coverage": "partial"})

    def test_invalid_coverage_dropped(self) -> None:
        self.assertIsNone(arv._parse_oracle_completeness('{"coverage": "mostly"}'))

    def test_malformed_json_dropped_not_raised(self) -> None:
        # Observability never blocks — a bad note is dropped, not an exception.
        self.assertIsNone(arv._parse_oracle_completeness("{not json"))

    def test_non_object_dropped(self) -> None:
        self.assertIsNone(arv._parse_oracle_completeness('["a", "b"]'))

    def test_partial_note_covered_only(self) -> None:
        self.assertEqual(arv._parse_oracle_completeness('{"covered": "x"}'), {"covered": "x"})


class CliIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.state = self.workdir / ".build-loop" / "state.json"
        self.state.parent.mkdir(parents=True)
        self.state.write_text(json.dumps({"runs": [{"run_id": "r1", "judge_decisions": []}]}))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, extra: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--workdir", str(self.workdir),
             "--verdict", "yay", "--reason", "looks good"] + extra,
            capture_output=True, text=True,
        )

    def test_verdict_records_oracle_completeness(self) -> None:
        r = self._run(["--oracle-completeness", '{"covered":"auth","coverage":"thin"}'])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        dec = json.loads(self.state.read_text())["runs"][0]["judge_decisions"][-1]
        self.assertEqual(dec["verdict"], "yay")
        self.assertEqual(dec["oracle_completeness"], {"covered": "auth", "coverage": "thin"})

    def test_verdict_without_oracle_note_still_exit_0(self) -> None:
        r = self._run([])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        dec = json.loads(self.state.read_text())["runs"][0]["judge_decisions"][-1]
        self.assertNotIn("oracle_completeness", dec)


if __name__ == "__main__":
    unittest.main(verbosity=2)
