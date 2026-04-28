#!/usr/bin/env python3
"""Tests for plan_verify.py. Stdlib only. Run: python3 test_plan_verify.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "plan_verify.py"
FIXTURES = HERE.parent / "skills" / "plan-verify" / "test-fixtures"
REPO_ROOT = HERE.parent  # build-loop repo root


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True, text=True, timeout=30,
    )


class ContractShapeTests(unittest.TestCase):
    """Verifier output must match the Plan Evidence Contract."""

    def test_empty_plan_produces_no_findings(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("# Empty plan\n\nThis plan does nothing.\n")
            tmp = f.name
        try:
            r = run([tmp, "--json"])
            self.assertEqual(r.returncode, 0, r.stderr)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["summary"]["by_severity"]["BLOCKER"], 0)
        finally:
            Path(tmp).unlink()

    def test_finding_has_required_keys(self) -> None:
        text = "## Phase 1\n\nDelete `scripts/optimize_loop.py` immediately.\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--repo", str(REPO_ROOT), "--json"])
            payload = json.loads(r.stdout)
            for f_obj in payload["findings"]:
                for k in ("claim_text", "claim_kind", "subject", "verification_command",
                          "evidence", "result", "marker", "severity", "confidence", "rule_id"):
                    self.assertIn(k, f_obj, f"missing key {k} in finding: {f_obj}")
                self.assertIn(f_obj["severity"], ("BLOCKER", "WARN", "INFO"))
        finally:
            Path(tmp).unlink()

    def test_exit_code_2_on_missing_file(self) -> None:
        r = run(["/nonexistent/plan.md"])
        self.assertEqual(r.returncode, 2)


class FencedCodeExclusionTests(unittest.TestCase):
    """Claims inside fenced code blocks must be ignored."""

    def test_delete_inside_fenced_block_is_ignored(self) -> None:
        text = (
            "# Plan\n\n"
            "```\n"
            "delete `scripts/optimize_loop.py`\n"
            "```\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--repo", str(REPO_ROOT), "--json"])
            self.assertEqual(r.returncode, 0)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["summary"]["by_severity"]["BLOCKER"], 0)
        finally:
            Path(tmp).unlink()


class NumericDriftTests(unittest.TestCase):
    def test_drift_in_orphan_count(self) -> None:
        text = "We removed **6 orphans** from Phase 1. Later: only **5 orphans** are left.\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--json"])
            payload = json.loads(r.stdout)
            rules = payload["summary"]["by_rule_id"]
            self.assertGreaterEqual(rules.get("numeric-drift", {}).get("BLOCKER", 0), 1)
            self.assertEqual(r.returncode, 1)
        finally:
            Path(tmp).unlink()

    def test_no_drift_when_counts_match(self) -> None:
        text = "We have **6 orphans**. Phase 2 also reports **6 orphans**.\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--json"])
            self.assertEqual(r.returncode, 0)
        finally:
            Path(tmp).unlink()


class RouteChangeTests(unittest.TestCase):
    def test_308_redirect_without_marker_is_blocker(self) -> None:
        text = "## Routes\n\nWe use a 308 redirect from /old to /new.\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--json"])
            payload = json.loads(r.stdout)
            rules = payload["summary"]["by_rule_id"]
            self.assertGreaterEqual(rules.get("route-change-evidence", {}).get("BLOCKER", 0), 1)
            self.assertEqual(r.returncode, 1)
        finally:
            Path(tmp).unlink()

    def test_308_redirect_with_marker_within_3_lines_passes(self) -> None:
        text = "## Routes\n\nWe use a 308 redirect from /old to /new.\n✅ verified by reading nginx.conf line 42.\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--json"])
            payload = json.loads(r.stdout)
            rules = payload["summary"]["by_rule_id"]
            self.assertEqual(rules.get("route-change-evidence", {}).get("BLOCKER", 0), 0)
        finally:
            Path(tmp).unlink()


class MissingEvidenceTests(unittest.TestCase):
    def test_unmarked_unused_claim_is_warn(self) -> None:
        text = "## Packages\n\nThe recharts package is unused; remove it.\n"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
            tmp = f.name
        try:
            r = run([tmp, "--json"])
            payload = json.loads(r.stdout)
            rules = payload["summary"]["by_rule_id"]
            self.assertGreaterEqual(rules.get("missing-evidence", {}).get("WARN", 0), 1)
            # WARN does not block
            self.assertEqual(r.returncode, 0)
        finally:
            Path(tmp).unlink()


class FixtureABTests(unittest.TestCase):
    """Run against the three committed fixtures and check expectations."""

    def _payload(self, fixture: str, with_repo: bool = True) -> dict:
        plan = FIXTURES / fixture
        args = [str(plan), "--json"]
        if with_repo:
            args += ["--repo", str(REPO_ROOT)]
        r = run(args)
        return {"rc": r.returncode, "payload": json.loads(r.stdout) if r.stdout else None,
                "stderr": r.stderr}

    def test_v20_fails_loudly(self) -> None:
        res = self._payload("atomize-ai-v20.md")
        self.assertEqual(res["rc"], 1, f"v2.0 should exit 1; stderr={res['stderr']}")
        rules = res["payload"]["summary"]["by_rule_id"]
        # Per goal.md criterion 2:
        self.assertGreaterEqual(rules.get("delete-with-callers", {}).get("BLOCKER", 0), 1,
                                f"v2.0 needs ≥1 delete-with-callers BLOCKER; got {rules}")
        self.assertGreaterEqual(rules.get("numeric-drift", {}).get("BLOCKER", 0), 1,
                                f"v2.0 needs ≥1 numeric-drift BLOCKER; got {rules}")
        self.assertGreaterEqual(rules.get("route-change-evidence", {}).get("BLOCKER", 0), 1,
                                f"v2.0 needs ≥1 route-change-evidence BLOCKER; got {rules}")
        self.assertGreaterEqual(rules.get("missing-evidence", {}).get("WARN", 0), 1,
                                f"v2.0 needs ≥1 missing-evidence WARN; got {rules}")

    def test_v22_passes(self) -> None:
        res = self._payload("atomize-ai-v22.md")
        self.assertEqual(res["rc"], 0,
                         f"v2.2 should exit 0; got {res['rc']}; "
                         f"BLOCKERs={res['payload']['summary']['by_severity']['BLOCKER']}; "
                         f"by_rule={res['payload']['summary']['by_rule_id']}")

    def test_unrelated_passes(self) -> None:
        res = self._payload("unrelated-good-plan.md")
        self.assertEqual(res["rc"], 0,
                         f"unrelated plan should exit 0; "
                         f"BLOCKERs={res['payload']['summary']['by_severity']['BLOCKER']}; "
                         f"by_rule={res['payload']['summary']['by_rule_id']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
