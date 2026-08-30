#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for shared test-coverage lookup.

Guards the defect these tests were written for: the self-review DETECTOR and the
RE-VALIDATOR answered "does a test cover this script?" differently, so the deep
run generated 66 `self_missing_test` findings and the re-validator closed 53 of
them (80%) in the same session. Both now call `self_review.coverage`.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from self_review.coverage_lookup import CoverageIndex, find_test, is_live  # noqa: E402
from self_review.selfscan import _findings_missing_tests  # noqa: E402


class CoverageLookupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "scripts").mkdir()
        self.addCleanup(self._tmp.cleanup)

    def _script(self, name: str, body: str = "") -> Path:
        p = self.root / "scripts" / name
        p.write_text(body)
        return p

    def test_matching_filename_is_coverage(self) -> None:
        self._script("widget.py")
        self._script("test_widget.py", "import widget\n")
        self.assertEqual(find_test(self.root, "widget")[1], "filename")

    def test_differently_named_importing_test_is_coverage(self) -> None:
        """The exact false-positive shape: covered, but not by test_<name>.py."""
        self._script("slice_acp.py")
        self._script("test_acp.py", "from slice_acp import build\n")
        path, how = find_test(self.root, "slice_acp")
        self.assertEqual(how, "import")
        self.assertEqual(Path(path).name, "test_acp.py")

    def test_uncovered_script_reports_no_test(self) -> None:
        self._script("orphan.py")
        self._script("test_widget.py", "import widget\n")
        self.assertEqual(find_test(self.root, "orphan"), (None, ""))

    def test_substring_of_longer_module_is_not_coverage(self) -> None:
        """`test_embed_backend.py` must not read as coverage for `backend.py`."""
        self._script("test_embed_backend.py", "Tests for embed_backend.py\nimport embed_backend\n")
        self.assertEqual(find_test(self.root, "backend"), (None, ""))

    def test_hyphenated_filename_reference_is_coverage(self) -> None:
        self._script("test_miner.py", "Runs transcript-pattern-miner.py end to end\n")
        self.assertEqual(find_test(self.root, "transcript-pattern-miner")[1], "import")

    def test_mirror_copies_are_not_live_sources(self) -> None:
        self.assertFalse(is_live(Path("plugin-artifacts/codex/scripts/test_x.py")))
        self.assertFalse(is_live(Path("a/__pycache__/test_x.py")))
        self.assertTrue(is_live(Path("scripts/test_x.py")))

    def test_index_excludes_mirrored_tests(self) -> None:
        mirror = self.root / "plugin-artifacts" / "scripts"
        mirror.mkdir(parents=True)
        (mirror / "test_widget.py").write_text("import widget\n")
        self.assertEqual(CoverageIndex(self.root).lookup("widget"), (None, ""))


class DetectorAgreesWithRevalidatorTest(unittest.TestCase):
    """The detector must not emit a finding the re-validator would close."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "scripts").mkdir()
        self.addCleanup(self._tmp.cleanup)

    def test_no_finding_when_covered_under_another_name(self) -> None:
        (self.root / "scripts" / "slice_acp.py").write_text("")
        (self.root / "scripts" / "test_acp.py").write_text("from slice_acp import build\n")
        findings = _findings_missing_tests(self.root / "scripts", self.root)
        self.assertEqual([f["signal"] for f in findings], [])

    def test_finding_emitted_when_genuinely_uncovered(self) -> None:
        (self.root / "scripts" / "orphan.py").write_text("")
        findings = _findings_missing_tests(self.root / "scripts", self.root)
        self.assertEqual([f["signal"] for f in findings], ["No test file for orphan.py"])
        self.assertEqual(findings[0]["kind"], "self_missing_test")


if __name__ == "__main__":
    unittest.main()
