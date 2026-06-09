#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the loop-builder skill generator."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SCRIPT = REPO_ROOT / "skills" / "loop-builder" / "scripts" / "loop_builder.py"
PRESETS = REPO_ROOT / "skills" / "loop-builder" / "presets"


def load_module():
    spec = importlib.util.spec_from_file_location("loop_builder", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load loop_builder module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LoopBuilderTests(unittest.TestCase):
    def test_all_presets_load_and_have_skill_chain(self) -> None:
        loop_builder = load_module()
        names = loop_builder.available_presets()
        self.assertGreaterEqual(
            set(names),
            {
                "active-project-evidence",
                "source-ingestion-raw-data-audit",
                "presentation-audit",
                "research-synthesis",
                "generic-artifact-loop",
            },
        )
        for name in names:
            preset = loop_builder.load_preset(name)
            self.assertIn("skill_chain", preset)
            self.assertIsInstance(preset["skill_chain"], dict)
            self.assertTrue(preset["validators"])

    def test_create_generates_loop_pack_with_validator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            target = Path(tmp_raw) / "active-project-evidence"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "create",
                    "active-project-evidence",
                    "--preset",
                    "active-project-evidence",
                    "--output",
                    str(target),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            loop_yaml = target / "loop.yaml"
            rubric = target / "rubric.md"
            report = target / "templates" / "report.md"
            validator = target / "validators" / "validate_loop.py"
            self.assertTrue(loop_yaml.is_file())
            self.assertTrue(rubric.is_file())
            self.assertTrue(report.is_file())
            self.assertTrue(validator.is_file())
            text = loop_yaml.read_text(encoding="utf-8")
            self.assertIn("skill_chain:", text)
            self.assertIn("source_preset: \"active-project-evidence\"", text)
            self.assertIn("pyramid-principle:pyramid-presentation", text)

            validate = subprocess.run(
                [sys.executable, str(validator)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(validate.returncode, 0, msg=validate.stderr + validate.stdout)

    def test_create_refuses_existing_target_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            target = Path(tmp_raw) / "loop"
            target.mkdir()
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "create",
                    "loop",
                    "--preset",
                    "generic-artifact-loop",
                    "--output",
                    str(target),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("target already exists", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
