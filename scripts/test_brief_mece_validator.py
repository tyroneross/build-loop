#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for brief_mece_validator.py."""
from __future__ import annotations

import json
import subprocess
import sys
import shutil
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


class CaptureTests(unittest.TestCase):
    """Brief capture (Falsifier B's measurement substrate).

    Capture rides the MECE lint because the lint is already mandatory at the
    moment the assembled brief exists. Persisting there makes the measurement
    mechanical instead of an instruction an orchestrator must remember.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_capture_writes_brief_to_run_scoped_path(self) -> None:
        res = bmv.capture_brief(
            "## owns\n- a.py\n", workdir=Path(self.tmp),
            run_id="bl-20260721T000000Z-x-1", chunk_id="C2",
        )
        self.assertTrue(res["captured"])
        dest = Path(self.tmp) / ".build-loop" / "briefs" / "bl-20260721T000000Z-x-1" / "C2.md"
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_text(encoding="utf-8"), "## owns\n- a.py\n")

    def test_capture_sanitizes_path_segments(self) -> None:
        res = bmv.capture_brief(
            "x", workdir=Path(self.tmp),
            run_id="../../etc", chunk_id="a/b",
        )
        self.assertTrue(res["captured"])
        # No traversal: the written path stays under the workdir.
        self.assertTrue(Path(res["path"]).resolve().is_relative_to(Path(self.tmp).resolve()))

    def test_capture_fails_open_and_never_raises(self) -> None:
        # A file where the briefs directory should be makes mkdir fail.
        blocker = Path(self.tmp) / ".build-loop" / "briefs"
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("not a directory", encoding="utf-8")
        res = bmv.capture_brief(
            "x", workdir=Path(self.tmp), run_id="r", chunk_id="c",
        )
        self.assertFalse(res["captured"])
        self.assertIn("brief capture failed", res["warning"])

    def test_cli_capture_is_opt_in(self) -> None:
        brief = Path(self.tmp) / "brief.md"
        brief.write_text("## owns\n", encoding="utf-8")
        # Without the capture flags, nothing is written.
        bmv.main(["--brief-file", str(brief), "--workdir", self.tmp])
        self.assertFalse((Path(self.tmp) / ".build-loop" / "briefs").exists())


# --- EC-02 rca part 2: enforcement briefs must claim an activation path -------

_SEVEN = (
    "## Owns\nx\n## Does-not-own\ny\n## Interface-contract\nz\n"
    "## Integration-checkpoint\nc\n## Allowed-tools\nRead\n## Denied-tools\nBash\n"
    "## Acceptance-criteria\npasses\n"
)


def _brief(body: str) -> str:
    return _SEVEN + "\n" + body


def test_enforcement_brief_without_activation_warns():
    """The defect: a peer is handed a gate and never told what fires it."""
    out = bmv.validate_brief(_brief("Build a pre-commit gate that blocks staged secrets."))
    assert any("activation path" in w for w in out["warnings"]), out


def test_enforcement_brief_with_activation_claim_is_quiet():
    """Mutation check the other way: if this warns too, the rule fires on every
    enforcement brief and gets routed around."""
    out = bmv.validate_brief(_brief(
        "Build a pre-commit gate that blocks staged secrets.\n"
        "- trigger: .git/hooks/pre-commit — verified-live: yes\n"))
    assert not any("activation path" in w for w in out["warnings"]), out


def test_non_enforcement_brief_is_quiet():
    """A brief with no enforcement vocabulary must never trip this."""
    out = bmv.validate_brief(_brief("Rename the parser module and update imports."))
    assert not any("activation path" in w for w in out["warnings"]), out


def test_warning_never_invalidates_the_brief():
    """WARN, not BLOCK — `valid` stays governed by the seven MECE fields."""
    out = bmv.validate_brief(_brief("Add a lint that enforces the schema."))
    assert any("activation path" in w for w in out["warnings"])
    assert out["valid"] is True, "activation warning must not fail a complete brief"


def test_explicit_override_silences_it():
    out = bmv.validate_brief(_brief(
        "Add a lint that enforces the schema.\noverride: activation-claim-exempt\n"))
    assert not any("activation path" in w for w in out["warnings"]), out


def test_override_quoted_in_prose_does_not_silence_it():
    """Anchored: mentioning the token must not disable the rule."""
    out = bmv.validate_brief(_brief(
        "Add a lint that enforces the schema. Do NOT use `override: activation-claim-exempt`."))
    assert any("activation path" in w for w in out["warnings"]), out
