#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""
Tests for apple_sourcekit_triage.py.

All tests use dependency injection for the xcodebuild runner so no real
Xcode invocation happens. The triage() function is tested directly; the
CLI is tested via subprocess for the non-XcodeGen path.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "apple_sourcekit_triage.py"

# Import the module under test
sys.path.insert(0, str(HERE))
from apple_sourcekit_triage import triage  # noqa: E402


# ---------------------------------------------------------------------------
# Fake build runners
# ---------------------------------------------------------------------------

def _runner_success(project_root: Path) -> dict[str, Any]:
    """Simulates xcodebuild that exits 0 (BUILD SUCCEEDED)."""
    return {"exit_code": 0, "stderr": "** BUILD SUCCEEDED **", "scheme": "App"}


def _runner_fail_with_matching_error(project_root: Path) -> dict[str, Any]:
    """Simulates xcodebuild that fails reproducing the exact 'Cannot find type Foo' error."""
    stderr = (
        "/path/to/MyView.swift:12:5: error: Cannot find type 'Foo' in scope\n"
        "** BUILD FAILED **"
    )
    return {"exit_code": 1, "stderr": stderr, "scheme": "App"}


def _runner_fail_with_different_error(project_root: Path) -> dict[str, Any]:
    """Simulates xcodebuild that fails but with a DIFFERENT error (not the input diagnostic)."""
    stderr = (
        "/path/to/SomeOther.swift:5:1: error: Use of undeclared identifier 'bar'\n"
        "** BUILD FAILED **"
    )
    return {"exit_code": 1, "stderr": stderr, "scheme": "App"}


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestNonXcodeGenProject(unittest.TestCase):
    """No project.yml → applicable: false."""

    def test_no_project_yml_returns_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Explicitly no project.yml
            result = triage(root, [], build_runner=_runner_success)
        self.assertFalse(result["applicable"])
        self.assertEqual(result["reason"], "not an XcodeGen project")

    def test_non_existent_path_returns_not_applicable(self) -> None:
        result = triage(Path("/tmp/nonexistent_xyz_12345_test"), [], build_runner=_runner_success)
        self.assertFalse(result["applicable"])
        self.assertIn("reason", result)


class TestXcodebuildSucceeds(unittest.TestCase):
    """xcodebuild succeeds + input diagnostics → all classified false_positive."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "project.yml").write_text("name: TestApp\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_all_false_positive_when_build_succeeds(self) -> None:
        diags = [
            {"file": "Foo.swift", "line": 10, "message": "Cannot find type 'Foo' in scope"},
            {"file": "Bar.swift", "line": 20, "message": "Cannot find type 'Bar' in scope"},
        ]
        result = triage(self.root, diags, build_runner=_runner_success)

        self.assertTrue(result["applicable"])
        self.assertTrue(result["xcodebuild_succeeded"])
        self.assertEqual(result["xcodebuild_exit"], 0)
        self.assertEqual(result["summary"]["total"], 2)
        self.assertEqual(result["summary"]["false_positive"], 2)
        self.assertEqual(result["summary"]["real"], 0)
        for d in result["diagnostics"]:
            self.assertEqual(d["verdict"], "false_positive")
        self.assertIn("ground truth (xcodebuild) is clean", result["recommendation"])

    def test_empty_diagnostics_no_crash(self) -> None:
        result = triage(self.root, [], build_runner=_runner_success)

        self.assertTrue(result["applicable"])
        self.assertEqual(result["summary"]["total"], 0)
        self.assertEqual(result["summary"]["false_positive"], 0)
        self.assertEqual(result["summary"]["real"], 0)
        self.assertEqual(result["diagnostics"], [])

    def test_empty_diagnostics_recommendation_is_clean(self) -> None:
        result = triage(self.root, [], build_runner=_runner_success)
        self.assertIn("ground truth (xcodebuild) is clean", result["recommendation"])


class TestXcodebuildFailsWithMatchingError(unittest.TestCase):
    """xcodebuild fails AND reproduces the same error → verdict: real."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "project.yml").write_text("name: TestApp\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_matching_error_classified_real(self) -> None:
        diags = [
            {"file": "MyView.swift", "line": 12, "message": "Cannot find type 'Foo' in scope"},
        ]
        result = triage(self.root, diags, build_runner=_runner_fail_with_matching_error)

        self.assertTrue(result["applicable"])
        self.assertFalse(result["xcodebuild_succeeded"])
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["summary"]["real"], 1)
        self.assertEqual(result["summary"]["false_positive"], 0)
        self.assertEqual(result["diagnostics"][0]["verdict"], "real")
        self.assertIn("real errors present", result["recommendation"])

    def test_matching_error_reason_mentions_xcodebuild(self) -> None:
        diags = [
            {"file": "MyView.swift", "line": 12, "message": "Cannot find type 'Foo' in scope"},
        ]
        result = triage(self.root, diags, build_runner=_runner_fail_with_matching_error)
        # Reason should explain why it's real (xcodebuild confirmed)
        self.assertIn("reason", result["diagnostics"][0])


class TestXcodebuildFailsWithDifferentError(unittest.TestCase):
    """xcodebuild fails but the specific diagnostic error was NOT reproduced → false_positive."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "project.yml").write_text("name: TestApp\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unrelated_build_failure_yields_false_positive(self) -> None:
        diags = [
            {
                "file": "MyView.swift",
                "line": 5,
                "message": "Cannot find type 'Qux' in scope",
            }
        ]
        # xcodebuild fails with a DIFFERENT error — 'Qux' was NOT reproduced
        result = triage(self.root, diags, build_runner=_runner_fail_with_different_error)

        self.assertTrue(result["applicable"])
        self.assertFalse(result["xcodebuild_succeeded"])
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["summary"]["false_positive"], 1)
        self.assertEqual(result["summary"]["real"], 0)
        self.assertEqual(result["diagnostics"][0]["verdict"], "false_positive")
        self.assertIn("not reproduce", result["diagnostics"][0]["reason"])

    def test_multiple_diags_mixed_when_only_one_matches(self) -> None:
        """Only the diagnostic matching xcodebuild's error is real; the rest are ghosts."""
        def _runner_fail_foo(project_root: Path) -> dict[str, Any]:
            # Only 'Foo' is in xcodebuild's output; 'Bar' is not
            stderr = "/path/to/X.swift:1:1: error: Cannot find type 'Foo' in scope\n"
            return {"exit_code": 1, "stderr": stderr, "scheme": "App"}

        diags = [
            {"file": "A.swift", "line": 1, "message": "Cannot find type 'Foo' in scope"},
            {"file": "B.swift", "line": 2, "message": "Cannot find type 'Bar' in scope"},
        ]
        result = triage(self.root, diags, build_runner=_runner_fail_foo)

        self.assertEqual(result["summary"]["total"], 2)
        self.assertEqual(result["summary"]["real"], 1)
        self.assertEqual(result["summary"]["false_positive"], 1)
        verdicts = {d["message"]: d["verdict"] for d in result["diagnostics"]}
        self.assertEqual(verdicts["Cannot find type 'Foo' in scope"], "real")
        self.assertEqual(verdicts["Cannot find type 'Bar' in scope"], "false_positive")


class TestEmptyDiagnostics(unittest.TestCase):
    """Edge case: empty input list must not crash and produce a valid envelope."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "project.yml").write_text("name: TestApp\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_with_failing_build(self) -> None:
        result = triage(self.root, [], build_runner=_runner_fail_with_matching_error)
        self.assertTrue(result["applicable"])
        self.assertEqual(result["summary"]["total"], 0)
        self.assertEqual(result["summary"]["real"], 0)
        self.assertEqual(result["diagnostics"], [])

    def test_empty_with_succeeding_build(self) -> None:
        result = triage(self.root, [], build_runner=_runner_success)
        self.assertTrue(result["applicable"])
        self.assertEqual(result["summary"]["total"], 0)
        self.assertEqual(result["diagnostics"], [])


class TestCLINonExistentProjectRoot(unittest.TestCase):
    """CLI: --project-root on a non-existent path returns applicable:false cleanly, no crash."""

    def test_nonexistent_project_root_json(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--project-root", "/tmp/nonexistent_xyz_12345_test",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["applicable"])
        self.assertIn("reason", data)


class TestEnvelopeShape(unittest.TestCase):
    """Verify the output envelope always has the required fields."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "project.yml").write_text("name: TestApp\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_full_envelope_fields_present(self) -> None:
        diags = [
            {"file": "A.swift", "line": 1, "message": "Cannot find type 'X' in scope"}
        ]
        result = triage(self.root, diags, build_runner=_runner_success)

        required_top = {"applicable", "xcodebuild_exit", "xcodebuild_succeeded",
                        "diagnostics", "summary", "recommendation"}
        self.assertTrue(required_top.issubset(result.keys()), result.keys())

        required_summary = {"total", "false_positive", "real"}
        self.assertTrue(required_summary.issubset(result["summary"].keys()))

        for d in result["diagnostics"]:
            self.assertIn("verdict", d)
            self.assertIn(d["verdict"], {"false_positive", "real"})
            self.assertIn("reason", d)

    def test_non_xcodegen_envelope_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = triage(Path(tmp), [], build_runner=_runner_success)
        self.assertIn("applicable", result)
        self.assertFalse(result["applicable"])
        self.assertIn("reason", result)


if __name__ == "__main__":
    unittest.main()
