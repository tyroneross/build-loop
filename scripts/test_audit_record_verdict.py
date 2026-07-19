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

import audit_before_commit as abc  # noqa: E402
import audit_record_verdict as arv  # noqa: E402

SCRIPT = HERE / "audit_record_verdict.py"


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, capture_output=True)


class SharedDiffHashTests(unittest.TestCase):
    """Guards the two scripts from ever computing a different hash for the same
    staged state (learn/risk-gated-commit-audit diff-hash tightening) — a
    duplicated implementation could silently drift."""

    def test_audit_record_verdict_imports_the_same_function_object(self) -> None:
        self.assertIs(arv.staged_diff_hash, abc.staged_diff_hash)

    def test_both_scripts_compute_identical_hash_for_the_same_staged_diff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_git_repo(repo)
            (repo / "a.txt").write_text("hello\nmore content\n", encoding="utf-8")
            subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
            h1 = abc.staged_diff_hash(cwd=repo)
            h2 = arv.staged_diff_hash(cwd=repo)
            self.assertIsNotNone(h1)
            self.assertEqual(h1, h2)

    def test_no_staged_changes_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_git_repo(repo)
            self.assertIsNone(abc.staged_diff_hash(cwd=repo))


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

    def test_no_git_repo_records_no_diff_hash(self) -> None:
        """workdir is a plain tempdir, not a git repo — staged_diff_hash() must fail
        safe (return None) rather than raise, and no diff_hash key gets written."""
        r = self._run([])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        dec = json.loads(self.state.read_text())["runs"][0]["judge_decisions"][-1]
        self.assertNotIn("diff_hash", dec)


class DiffHashRecordingTests(unittest.TestCase):
    """CLI integration against a real git repo — proves audit_record_verdict.py
    actually binds the recorded verdict to the exact staged diff hash."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        _init_git_repo(self.repo)
        self.state = self.repo / ".build-loop" / "state.json"
        self.state.parent.mkdir(parents=True)
        self.state.write_text(json.dumps({"runs": [{"run_id": "r1", "judge_decisions": []}]}))

    def test_recorded_diff_hash_matches_staged_diff_hash(self) -> None:
        (self.repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py"], cwd=self.repo, check=True, capture_output=True)
        expected_hash = abc.staged_diff_hash(cwd=self.repo)
        self.assertIsNotNone(expected_hash)

        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--workdir", str(self.repo),
             "--verdict", "yay", "--reason", "looks good"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        dec = json.loads(self.state.read_text())["runs"][0]["judge_decisions"][-1]
        self.assertEqual(dec.get("diff_hash"), expected_hash)


if __name__ == "__main__":
    unittest.main(verbosity=2)
