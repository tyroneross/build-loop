#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for brief_mece_validator.py."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import brief_mece_validator as bmv  # noqa: E402


class BriefMeceValidatorTests(unittest.TestCase):
    def test_accepts_markdown_packet_labels(self):
        result = bmv.validate_brief(
            "- **Owns** (Codex): scripts/brief_mece_validator.py\n"
            "- **Does not own**: agents/build-orchestrator.md\n"
            "- **Interface contract**: validate_brief returns JSON-ready dict\n"
            "- **Integration checkpoint**: test file passes\n"
            "- **Allowed tools**: []\n"
            "- **Denied tools**: []\n"
            "- **Acceptance criteria**: all 7 fields present → valid\n"
        )

        self.assertTrue(result["valid"])
        self.assertEqual(result["missing"], [])

    def test_accepts_heading_style_fields(self):
        result = bmv.validate_brief(
            "### owns\nscripts/x.py\n"
            "### does-not-own\nagents/y.md\n"
            "### interface-contract\nCLI exits 0/1\n"
            "### integration-checkpoint\norchestrator can parse JSON\n"
            "### allowed-tools\n[]\n"
            "### denied-tools\n[]\n"
            "### acceptance-criteria\nreturning envelope satisfies the oracle\n"
        )

        self.assertTrue(result["valid"])

    def test_rejects_six_field_brief_missing_acceptance_criteria(self):
        """A previously-valid 6-field brief is now rejected for missing acceptance-criteria."""
        result = bmv.validate_brief(
            "- **Owns** (Codex): scripts/brief_mece_validator.py\n"
            "- **Does not own**: agents/build-orchestrator.md\n"
            "- **Interface contract**: validate_brief returns JSON-ready dict\n"
            "- **Integration checkpoint**: test file passes\n"
            "- **Allowed tools**: []\n"
            "- **Denied tools**: []\n"
        )

        self.assertFalse(result["valid"])
        self.assertEqual(result["missing"], ["acceptance-criteria"])

    def test_six_pre_existing_field_checks_unchanged(self):
        """The original six field labels still resolve in their canonical order."""
        result = bmv.validate_brief(
            "- **Owns**: scripts/x.py\n"
            "- **Does not own**: agents/y.md\n"
            "- **Interface contract**: CLI exits 0/1\n"
            "- **Integration checkpoint**: tests pass\n"
            "- **Allowed tools**: []\n"
            "- **Denied tools**: []\n"
        )

        self.assertEqual(
            result["present"],
            [
                "owns",
                "does_not_own",
                "interface_contract",
                "integration_checkpoint",
                "allowed_tools",
                "denied_tools",
            ],
        )
        # Only the new 7th field is missing — the six legacy checks are intact.
        self.assertEqual(result["missing"], ["acceptance-criteria"])

    def test_reports_missing_fields(self):
        result = bmv.validate_brief(
            "- **Owns**: scripts/x.py\n"
            "- **Integration checkpoint**: tests pass\n"
        )

        self.assertFalse(result["valid"])
        self.assertEqual(
            result["missing"],
            [
                "does-not-own",
                "interface-contract",
                "allowed-tools",
                "denied-tools",
                "acceptance-criteria",
            ],
        )

    def test_rejects_four_field_brief_missing_tool_limits(self):
        """A previously-valid 4-field brief is now rejected for missing allowed/denied-tools."""
        result = bmv.validate_brief(
            "- **Owns** (Claude): scripts/foo.py\n"
            "- **Does not own**: agents/bar.md\n"
            "- **Interface contract**: returns exit 0 on success\n"
            "- **Integration checkpoint**: pytest passes\n"
        )

        self.assertFalse(result["valid"])
        self.assertIn("allowed-tools", result["missing"])
        self.assertIn("denied-tools", result["missing"])

    def test_empty_brief_warns_and_fails(self):
        result = bmv.validate_brief("")

        self.assertFalse(result["valid"])
        self.assertIn("brief is empty", result["warnings"])

    def test_cli_returns_json_and_exit_1_for_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            brief = Path(d) / "brief.md"
            brief.write_text("- **Owns**: scripts/x.py\n", encoding="utf-8")
            cmd = [
                sys.executable,
                str(HERE / "brief_mece_validator.py"),
                "--brief-file",
                str(brief),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)

        self.assertEqual(r.returncode, 1)
        payload = json.loads(r.stdout)
        self.assertFalse(payload["valid"])
        self.assertIn("does-not-own", payload["missing"])


if __name__ == "__main__":
    unittest.main()
