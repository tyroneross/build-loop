#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``report_lint.py``.

Stdlib-only. Run with ``python3 scripts/test_report_lint.py``.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from report_lint import (  # noqa: E402
    run_lint,
    lint_headline,
    lint_validation_line,
    lint_jargon,
    lint_contrastive_pivot,
    lint_length,
    _strip_fenced_blocks,
)


def _write(text: str) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    f.write(text)
    f.close()
    return Path(f.name)


def _lines(text: str):
    return _strip_fenced_blocks(text)


def _rule_ids(result):
    return [f["rule_id"] for f in result["findings"]]


class TestHeadline(unittest.TestCase):
    def test_clean_sentence_headline_passes(self):
        text = "Auditor now runs on every build commit; the gap is closed."
        self.assertEqual(lint_headline(_lines(text)), [])

    def test_markdown_heading_flagged(self):
        text = "# Build Complete\n\nSome content."
        findings = lint_headline(_lines(text))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule_id"], "headline-present")

    def test_bullet_first_line_flagged(self):
        text = "- Did the thing.\n- Other thing."
        findings = lint_headline(_lines(text))
        self.assertEqual(len(findings), 1)

    def test_too_short_flagged(self):
        text = "Done."
        findings = lint_headline(_lines(text))
        self.assertEqual(len(findings), 1)

    def test_no_terminator_flagged(self):
        text = "Auditor now runs on every build commit and that closes the gap"
        findings = lint_headline(_lines(text))
        self.assertEqual(len(findings), 1)

    def test_noun_phrase_flagged(self):
        text = "A summary of recent changes to the auditor system overall."
        findings = lint_headline(_lines(text))
        self.assertEqual(len(findings), 1)

    def test_empty_file_flagged(self):
        text = ""
        findings = lint_headline(_lines(text))
        self.assertEqual(len(findings), 1)


class TestValidationLine(unittest.TestCase):
    def test_pytest_with_check_passes(self):
        text = (
            "Auditor now runs on every build commit.\n\n"
            "✅ Verified by python3 scripts/test_audit.py — 14 passed.\n"
        )
        self.assertEqual(lint_validation_line(_lines(text)), [])

    def test_warning_marker_with_method_passes(self):
        text = (
            "Auditor now runs on every build commit.\n\n"
            "⚠️ Untested — ran the test suite but coverage gap on the peer-host path.\n"
        )
        self.assertEqual(lint_validation_line(_lines(text)), [])

    def test_marker_with_script_artifact_passes(self):
        text = (
            "Auditor runs every commit now.\n\n"
            "✅ scripts/test_report_lint.py passed.\n"
        )
        self.assertEqual(lint_validation_line(_lines(text)), [])

    def test_no_validation_line_flagged(self):
        text = "Auditor now runs on every build commit.\n\nWe shipped it.\n"
        findings = lint_validation_line(_lines(text))
        self.assertEqual(len(findings), 1)

    def test_status_marker_without_method_flagged(self):
        text = "Auditor now runs on every build commit.\n\n✅ Done.\n"
        findings = lint_validation_line(_lines(text))
        self.assertEqual(len(findings), 1)


class TestJargon(unittest.TestCase):
    def test_gap_codename_flagged(self):
        text = "Auditor now runs on every build commit; GAP-1 closed.\n"
        findings = lint_jargon(_lines(text))
        self.assertTrue(any(f["rule_id"] == "jargon-blocklist" for f in findings))

    def test_auditor_status_enum_flagged(self):
        text = "Auditor result: auditor_status: not-run:parent-must-dispatch.\n"
        findings = lint_jargon(_lines(text))
        # Two distinct jargon hits, but lint emits one per line so one finding here.
        self.assertEqual(len(findings), 1)

    def test_substep_codename_flagged(self):
        text = "Sub-step G completed without issue.\n"
        findings = lint_jargon(_lines(text))
        self.assertTrue(any(f["rule_id"] == "jargon-blocklist" for f in findings))

    def test_phase_codename_flagged(self):
        text = "Phase 4G now runs the new lint.\n"
        findings = lint_jargon(_lines(text))
        self.assertTrue(any(f["rule_id"] == "jargon-blocklist" for f in findings))

    def test_mece_flagged(self):
        text = "The MECE packet shipped to every implementer.\n"
        findings = lint_jargon(_lines(text))
        self.assertTrue(any(f["rule_id"] == "jargon-blocklist" for f in findings))

    def test_envelope_flagged(self):
        text = "The envelope returned with status pass.\n"
        findings = lint_jargon(_lines(text))
        self.assertTrue(any(f["rule_id"] == "jargon-blocklist" for f in findings))

    def test_verdict_enum_flagged(self):
        text = "Independent-auditor verdict: suggest_correction on chunk 2.\n"
        findings = lint_jargon(_lines(text))
        self.assertTrue(any(f["rule_id"] == "jargon-blocklist" for f in findings))

    def test_plain_language_passes(self):
        text = "Auditor now runs on every build commit; the gap is closed.\n"
        findings = lint_jargon(_lines(text))
        self.assertEqual(findings, [])

    def test_fenced_jargon_ignored(self):
        text = (
            "Auditor now runs on every build commit.\n\n"
            "```\n"
            "Internal trace: auditor_status: ran:dispatched-agent\n"
            "```\n"
        )
        findings = lint_jargon(_lines(text))
        self.assertEqual(findings, [])


class TestContrastivePivot(unittest.TestCase):
    def test_not_dash_its_flagged(self):
        text = "It's not a workaround — it's the durable fix.\n"
        findings = lint_contrastive_pivot(_lines(text))
        self.assertEqual(len(findings), 1)

    def test_isnt_its_flagged(self):
        text = "This isn't a band-aid, it's the real fix.\n"
        findings = lint_contrastive_pivot(_lines(text))
        self.assertEqual(len(findings), 1)

    def test_not_just_but_flagged(self):
        text = "We added not just a lint but a self-heal step.\n"
        findings = lint_contrastive_pivot(_lines(text))
        self.assertEqual(len(findings), 1)

    def test_plain_statement_passes(self):
        text = "The lint runs at Phase 4G and the orchestrator revises on findings.\n"
        findings = lint_contrastive_pivot(_lines(text))
        self.assertEqual(findings, [])


class TestLength(unittest.TestCase):
    def test_under_cap_passes(self):
        text = "Headline.\n" + ("body line\n" * 10)
        self.assertEqual(lint_length(_lines(text), cap=300), [])

    def test_over_cap_flagged(self):
        text = "Headline.\n" + ("body line\n" * 305)
        findings = lint_length(_lines(text), cap=300)
        self.assertEqual(len(findings), 1)


class TestRunLintEndToEnd(unittest.TestCase):
    def test_good_report_clean(self):
        text = (
            "Auditor now runs on every build commit; the previous gap is closed.\n"
            "\n"
            "- Commit: 7e54621 fix(audit): close nested-orchestrator audit gap\n"
            "- Files: agents/build-orchestrator.md, scripts/audit_before_commit.py\n"
            "\n"
            "It captures:\n"
            "- Nested orchestrators now hand the audit back to the parent.\n"
            "- Approve verdict recorded in .build-loop/judge-decisions.json.\n"
            "\n"
            "✅ Verified by python3 scripts/test_audit_before_commit.py — 14 passed.\n"
        )
        path = _write(text)
        try:
            result = run_lint(path)
            self.assertEqual(result["summary"]["total"], 0, result["findings"])
        finally:
            path.unlink()

    def test_bad_report_catches_multiple(self):
        text = (
            "# Phase 4G Sub-step G\n"
            "\n"
            "The auditor_status field changed to ran:dispatched-agent based on GAP-1.\n"
            "The MECE envelope propagates to runs[].\n"
            "It's not a workaround — it's the canonical fix.\n"
            "\n"
            "Done.\n"
        )
        path = _write(text)
        try:
            result = run_lint(path)
            ids = _rule_ids(result)
            self.assertIn("headline-present", ids)
            self.assertIn("validation-line-present", ids)
            self.assertIn("jargon-blocklist", ids)
            self.assertIn("contrastive-pivot", ids)
        finally:
            path.unlink()


class TestCli(unittest.TestCase):
    def test_cli_json_exit_zero(self):
        text = "Auditor now runs on every build commit.\n\n✅ Verified by pytest — passed.\n"
        path = _write(text)
        try:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "report_lint.py"), str(path), "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertIn("summary", payload)
            self.assertEqual(payload["summary"]["total"], 0)
        finally:
            path.unlink()

    def test_cli_findings_still_exit_zero(self):
        """Warn-mode: even with findings, exit 0 — orchestrator decides."""
        text = "GAP-1 closed.\n"
        path = _write(text)
        try:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "report_lint.py"), str(path), "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertGreater(payload["summary"]["total"], 0)
        finally:
            path.unlink()

    def test_cli_missing_file_exits_two(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "report_lint.py"), "/tmp/does-not-exist-xyz.md", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
