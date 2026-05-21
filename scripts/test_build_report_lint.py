#!/usr/bin/env python3
"""Tests for build_report_lint.py."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "build_report_lint.py"


def run_report(text: str) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(text)
        tmp = f.name
    try:
        return subprocess.run(
            [sys.executable, str(SCRIPT), tmp, "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        Path(tmp).unlink()


def rule_ids(payload: dict) -> set[str]:
    return {finding["rule_id"] for finding in payload["findings"]}


class BuildReportLintTests(unittest.TestCase):
    def test_good_parallel_report_passes(self) -> None:
        report = """
# Final report

parallel_batch:
  - [C1, C2]

merge_plan:
  clean_against: ["main"]
  conflicts_with: []
  suggested_order: ["C1", "C2"]
  merge_rationale: "C1 and C2 touch disjoint files."

## Done
- C1 verified. observer=codex method=pytest artifact=scripts/test_build_report_lint.py
- C2 known pass. observer=codex method=py_compile artifact=scripts/build_report_lint.py
"""
        result = run_report(report)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["summary"]["total"], 0)

    def test_verified_claim_requires_observer_method_artifact(self) -> None:
        result = run_report("""
# Final report

## Done
- C1 verified by xcodebuild.
""")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertIn("verification-evidence", rule_ids(payload))
        finding = payload["findings"][0]
        self.assertIn("observer", finding["message"])
        self.assertIn("artifact", finding["message"])

    def test_parallel_report_requires_dispatch_decision(self) -> None:
        result = run_report("""
# Final report

C1 and C2 were independent worktree chunks and parallel-safe.

merge_plan:
  clean_against: ["main"]
  conflicts_with: []
  suggested_order: ["C1", "C2"]
""")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertIn("parallel-decision-record", rule_ids(payload))

    def test_multi_chunk_report_requires_merge_plan(self) -> None:
        result = run_report("""
# Final report

parallel_batch:
  - [C1, C2]
""")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertIn("merge-plan-required", rule_ids(payload))

    def test_merge_plan_requires_required_fields(self) -> None:
        result = run_report("""
# Final report

parallel_batch:
  - [C1, C2]

merge_plan:
  clean_against: ["main"]
""")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertIn("merge-plan-fields", rule_ids(payload))

    def test_fenced_examples_are_ignored(self) -> None:
        result = run_report("""
# Final report

```md
C1 and C2 were independent and verified.
```
""")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
